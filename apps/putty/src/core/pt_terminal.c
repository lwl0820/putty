#include "pt_terminal.h"
#include "putty.h"
#include "terminal.h"
#include "ssh.h"
#include "storage.h"
#include <limits.h>

typedef struct pt_pending {
    pt_packet header;
    strbuf *data;
    uint64_t request;
} pt_pending;
typedef struct pt_session {
    pr_core *core;
    pt_options options;
    void *renderer;
    Conf *conf;
    Terminal *terminal;
    Backend *backend;
    Ldisc *ldisc;
    LogContext *log;
    struct unicode_data unicode;
    TermWin window;
    Seat seat;
    LogPolicy policy;
    rgb palette[OSC4_NCOLOURS];
    pt_pending pending[PT_LOG_APPEND + 1];
    uint32_t unsupported;
    uint64_t waiting;
    int waiting_kind;
    void (*confirm)(void *, SeatPromptResult);
    void (*append)(void *, int);
    void *confirm_context;
    char *key_host, *key_type, *key_string;
    int key_port;
    bool trusted, destroying;
    int cursor_x, cursor_y;
    unsigned long auth_started;
} pt_session;

static pt_session *active_session;
const bool share_can_be_downstream = true, share_can_be_upstream = true;
const bool buildinfo_gtk_relevant = false;

/* 原版默认值及存储格式保留；此纵切仅显式选择 UTF-8 和自己的离屏字体。 */
char *platform_default_s(const char *name) { (void)name; return NULL; }
bool platform_default_b(const char *name, bool value) { (void)name; return value; }
int platform_default_i(const char *name, int value) { (void)name; return value; }
FontSpec *platform_default_fontspec(const char *name)
{ (void)name; return fontspec_new_default(); }
Filename *platform_default_filename(const char *name)
{ (void)name; return filename_from_str(""); }
char *platform_get_x_display(void) { return dupstr(getenv("DISPLAY")); }

pr_status pt_publish(pr_core *core, pt_packet header, const void *data,
                     size_t size, uint64_t request)
{
    if (size > UINT32_MAX || size > SIZE_MAX - sizeof(header)) return PR_ARGUMENT;
    header.magic = PT_PACKET_MAGIC;
    header.length = (uint32_t)size;
    strbuf *packet = strbuf_new_nm();
    put_data(packet, &header, sizeof(header));
    put_data(packet, data, size);
    pr_event event = {0};
    event.object_id = 1;
    event.request_id = request;
    event.type = request ? PR_EVENT_REQUEST : PR_EVENT_DATA;
    event.data = packet->s;
    event.size = packet->len;
    pr_status result = pr_core_emit(core, &event);
    strbuf_free(packet);
    return result;
}
static void message(pt_session *s, int type, int value,
                    const void *data, size_t size, uint64_t request)
{
    if (s->destroying) return;
    assert(type > PT_FRAME && type <= PT_LOG_APPEND);
    /* 状态按类型合并，认证材料不进入事件；长远端文本另由 Terminal 消费。 */
    if (size > 16384) size = 16384;
    pt_packet header = {0};
    header.type = type;
    header.render_generation = s->options.render.generation;
    header.a = value;
    pt_pending *pending = &s->pending[type];
    if (type == PT_LOG && pending->data) {
        /* 日志保留顺序并有界合并；b 显式计数溢出，不能伪装完整历史。 */
        if (size < 16384 && pending->data->len < 16384 - size) {
            put_byte(pending->data, '\n'); put_data(pending->data, data, size);
        } else if (pending->header.b < INT_MAX) {
            ++pending->header.b;
        }
        return;
    }
    if (pending->data) { strbuf_free(pending->data); pending->data = NULL; }
    if (pt_publish(s->core, header, data, size, request) != PR_OK) {
        pending->header = header;
        pending->request = request;
        pending->data = strbuf_new();
        put_data(pending->data, data, size);
    }
}
static void text_message(pt_session *s, int type, int value, const char *text)
{ message(s, type, value, text, text ? strlen(text) : 0, 0); }
static void unsupported(pt_session *s, unsigned bit, const char *name)
{
    if (s->unsupported & (1U << bit)) return;
    s->unsupported |= 1U << bit;
    text_message(s, PT_UNSUPPORTED, bit, name);
}
void old_keyfile_warning(void)
{
    if (active_session)
        text_message(active_session, PT_ERROR, 0, "密钥使用旧版文件格式，请用 PuTTYgen 重新保存。");
}
static pt_session *tw(TermWin *w) { return container_of(w, pt_session, window); }
static pt_session *st(Seat *seat) { return container_of(seat, pt_session, seat); }
static pt_session *lp(LogPolicy *policy) { return container_of(policy, pt_session, policy); }
static uint32_t rgb_value(rgb c) { return ((uint32_t)c.r << 16) | ((uint32_t)c.g << 8) | c.b; }
static pt_style style(pt_session *s, unsigned long a, int line, truecolour tc)
{
    unsigned fg = (a & ATTR_FGMASK) >> ATTR_FGSHIFT;
    unsigned bg = (a & ATTR_BGMASK) >> ATTR_BGSHIFT;
    if (a & ATTR_REVERSE) { unsigned swap = fg; fg = bg; bg = swap; }
    pt_style result = {rgb_value(s->palette[fg]), rgb_value(s->palette[bg]), 0, line & LATTR_MODE};
    if (tc.fg.enabled) result.foreground = ((uint32_t)tc.fg.r << 16) | (tc.fg.g << 8) | tc.fg.b;
    if (tc.bg.enabled) result.background = ((uint32_t)tc.bg.r << 16) | (tc.bg.g << 8) | tc.bg.b;
    if ((a & ATTR_REVERSE) && (tc.fg.enabled || tc.bg.enabled)) {
        if (tc.fg.enabled) result.background = ((uint32_t)tc.fg.r << 16) | (tc.fg.g << 8) | tc.fg.b;
        else result.background = rgb_value(s->palette[bg]);
        if (tc.bg.enabled) result.foreground = ((uint32_t)tc.bg.r << 16) | (tc.bg.g << 8) | tc.bg.b;
        else result.foreground = rgb_value(s->palette[fg]);
    }
    if (a & ATTR_BOLD) result.flags |= PT_BOLD;
    if (a & ATTR_UNDER) result.flags |= PT_UNDERLINE;
    if (a & ATTR_WIDE) result.flags |= PT_WIDE;
    if (a & ATTR_NARROW) result.flags |= PT_NARROW;
    if (a & ATTR_STRIKE) result.flags |= PT_STRIKE;
    if (a & TATTR_COMBINING) result.flags |= PT_COMBINING;
    if (a & ATTR_ACTCURS) result.flags |= PT_CURSOR_ACTIVE;
    return result;
}
static bool draw_begin(TermWin *w)
{ pt_session *s = tw(w); return s->options.draw.begin(s->renderer); }
static void draw_text(TermWin *w, int x, int y, wchar_t *text, int len,
                       unsigned long attrs, int line, truecolour colour)
{
    pt_session *s = tw(w);
    s->options.draw.text(s->renderer, x, y, text, len, style(s, attrs, line, colour));
}
static void draw_cursor(TermWin *w, int x, int y, wchar_t *text, int len,
                         unsigned long attrs, int line, truecolour colour)
{
    pt_session *s = tw(w);
    s->options.draw.cursor(s->renderer, x, y, text, len, style(s, attrs, line, colour));
}
static void draw_sigil(TermWin *w, int x, int y)
{ pt_session *s = tw(w); s->options.draw.sigil(s->renderer, x, y); }
static int char_width(TermWin *w, int uc)
{ pt_session *s = tw(w); return s->options.draw.width(s->renderer, (uint32_t)uc); }
static void draw_end(TermWin *w)
{ pt_session *s = tw(w); s->options.draw.end(s->renderer); }
static void cursor_position(TermWin *w, int x, int y)
{ tw(w)->cursor_x = x; tw(w)->cursor_y = y; }
static void raw_mouse(TermWin *w, bool value)
{ if (value) unsupported(tw(w), 0, "鼠标跟踪"); }
static void raw_pointer(TermWin *w, bool value)
{ if (value) unsupported(tw(w), 1, "鼠标指针形状"); }
static void scrollbar(TermWin *w, int total, int start, int page)
{ (void)total; (void)start; (void)page; unsupported(tw(w), 2, "滚动条"); }
static void bell(TermWin *w, int mode)
{ (void)mode; unsupported(tw(w), 3, "铃声"); }
static void clipboard(TermWin *w, int id, wchar_t *text, int *attrs,
                       truecolour *colours, int len, bool deselect)
{
    (void)id; (void)text; (void)attrs; (void)colours; (void)len; (void)deselect;
    unsupported(tw(w), 4, "复制");
}
static void paste(TermWin *w, int id)
{ (void)id; unsupported(tw(w), 5, "粘贴"); }
static void refresh(TermWin *w) { (void)w; /* 完整帧已在 draw_end 异步发布。 */ }
static void request_resize(TermWin *w, int width, int height)
{
    (void)width; (void)height;
    pt_session *s = tw(w);
    unsupported(s, 6, "远端窗口尺寸请求");
    term_resize_request_completed(s->terminal);
}
static void title(TermWin *w, const char *value, int codepage)
{
    /* 非 UTF-8 的原版标题不猜编码，后续按平台代码页适配。 */
    pt_session *s = tw(w);
    if (codepage != CP_UTF8 && codepage != DEFAULT_CODEPAGE) {
        unsupported(s, 7, "非 UTF-8 标题"); return;
    }
    text_message(s, PT_TITLE, 0, value);
}
static void icon_title(TermWin *w, const char *value, int codepage)
{ (void)value; (void)codepage; unsupported(tw(w), 8, "图标标题"); }
static void minimise(TermWin *w, bool value)
{ (void)value; unsupported(tw(w), 9, "远端最小化"); }
static void maximise(TermWin *w, bool value)
{ (void)value; unsupported(tw(w), 10, "远端最大化"); }
static void move_window(TermWin *w, int x, int y)
{ (void)x; (void)y; unsupported(tw(w), 11, "远端移动窗口"); }
static void zorder(TermWin *w, bool top)
{ (void)top; unsupported(tw(w), 12, "远端窗口层级"); }
static void palette_set(TermWin *w, unsigned start, unsigned count, const rgb *colours)
{
    assert(start <= OSC4_NCOLOURS && count <= OSC4_NCOLOURS - start);
    memcpy(tw(w)->palette + start, colours, count * sizeof(rgb));
}
static void palette_overrides(TermWin *w, Terminal *terminal)
{ (void)w; (void)terminal; /* 此前端以 Conf 配色为准，没有 OS 本地覆盖。 */ }
static void unthrottle(TermWin *w, size_t size)
{ if (tw(w)->backend) backend_unthrottle(tw(w)->backend, size); }
static const TermWinVtable window_vtable = {
    .setup_draw_ctx = draw_begin, .draw_text = draw_text, .draw_cursor = draw_cursor,
    .draw_trust_sigil = draw_sigil, .char_width = char_width, .free_draw_ctx = draw_end,
    .set_cursor_pos = cursor_position, .set_raw_mouse_mode = raw_mouse,
    .set_raw_mouse_mode_pointer = raw_pointer, .set_scrollbar = scrollbar, .bell = bell,
    .clip_write = clipboard, .clip_request_paste = paste, .refresh = refresh,
    .request_resize = request_resize, .set_title = title, .set_icon_title = icon_title,
    .set_minimised = minimise, .set_maximised = maximise, .move = move_window,
    .set_zorder = zorder, .palette_set = palette_set,
    .palette_get_overrides = palette_overrides, .unthrottle = unthrottle,
};

static size_t output(Seat *seat, SeatOutputType type, const void *data, size_t size)
{ (void)type; return term_data(st(seat)->terminal, data, size); }
static size_t banner(Seat *seat, const void *data, size_t size)
{ return output(seat, SEAT_OUTPUT_STDERR, data, size); }
static void auth_work_finished(void *context)
{
    pt_session *s = context;
    text_message(s, PT_AUTH_WORK_END, (int)(pr_core_ticks() - s->auth_started), "");
}
static SeatPromptResult userpass(Seat *seat, prompts_t *p)
{
    pt_session *s = st(seat);
    if (!p->data && p->spr.kind == SPRK_INCOMPLETE)
        text_message(s, PT_AUTH_PROMPT, p->from_server ? 1 : 0,
                     p->name ? p->name : "身份验证");
    SeatPromptResult result = term_get_userpass_input(s->terminal, p);
    if (result.kind == SPRK_OK && !p->to_server && !p->from_server) {
        /* 上游紧接此返回同步解密 PPK。计时器只在该核心调用返回后运行，
         * 测的是这段真实认证工作的响应延迟，不插入模拟耗时。 */
        s->auth_started = pr_core_ticks();
        text_message(s, PT_AUTH_WORK_BEGIN, 0, "");
        pr_core_schedule(s->core, 1, auth_work_finished, s);
    }
    return result;
}
static void started(Seat *seat) { text_message(st(seat), PT_SESSION_STARTED, 0, ""); }
static void remote_exit(Seat *seat)
{ pt_session *s = st(seat); text_message(s, PT_REMOTE_EXIT, s->backend ? backend_exitcode(s->backend) : -1, ""); }
static void disconnected(Seat *seat)
{
    pt_session *s = st(seat);
    if (!s->backend || !backend_connected(s->backend)) text_message(s, PT_DISCONNECTED, 0, "");
}
static void fatal(Seat *seat, const char *value) { text_message(st(seat), PT_ERROR, 1, value); }
static void seat_nonfatal_impl(Seat *seat, const char *value) { text_message(st(seat), PT_ERROR, 0, value); }
static void specials(Seat *seat) { unsupported(st(seat), 13, "特殊命令菜单"); }
static char *ttymode(Seat *seat, const char *mode) { return term_get_ttymode(st(seat)->terminal, mode); }
static void busy(Seat *seat, BusyStatus value) { (void)seat; (void)value; }
static void clear_confirmation(pt_session *s)
{
    sfree(s->key_host); sfree(s->key_type); sfree(s->key_string);
    s->key_host = s->key_type = s->key_string = NULL;
    s->waiting = 0; s->confirm = NULL; s->append = NULL; s->confirm_context = NULL;
}
static void cancel_confirmation(void *context)
{
    /* begin_close 已拒绝业务输入；不从取消器重新进入即将析构的 SSH 协程。 */
    clear_confirmation(context);
}
static SeatPromptResult confirm_dialog(pt_session *s, int kind, SeatDialogText *text,
    void (*callback)(void *, SeatPromptResult), void *context)
{
    if (s->waiting) return SPR_SW_ABORT("已有待处理确认");
    strbuf *value = strbuf_new();
    for (size_t i = 0; i < text->nitems; ++i) {
        size_t length = strlen(text->items[i].text);
        if (length >= 16384 || value->len > 16384 - length - 1) {
            strbuf_free(value); return SPR_SW_ABORT("确认内容过长");
        }
        put_dataz(value, text->items[i].text);
        put_byte(value, '\n');
    }
    if (value->len > 16384) { strbuf_free(value); return SPR_SW_ABORT("确认内容过长"); }
    s->waiting = pr_core_request(s->core, 1, cancel_confirmation, s);
    if (!s->waiting) { strbuf_free(value); return SPR_SW_ABORT("无法登记确认请求"); }
    s->waiting_kind = kind;
    s->confirm = callback; s->confirm_context = context;
    message(s, kind, 0, value->s, value->len, s->waiting);
    strbuf_free(value);
    return SPR_INCOMPLETE;
}
static SeatPromptResult host_key(Seat *seat, const char *host, int port,
    const char *keytype, char *keystr, SeatDialogText *text, HelpCtx help,
    void (*callback)(void *, SeatPromptResult), void *context)
{
    (void)help;
    pt_session *s = st(seat);
    SeatPromptResult result = confirm_dialog(s, PT_HOST_KEY, text, callback, context);
    if (result.kind == SPRK_INCOMPLETE) {
        s->key_host = dupstr(host); s->key_type = dupstr(keytype);
        s->key_string = dupstr(keystr); s->key_port = port;
    }
    return result;
}
static SeatPromptResult weak_crypto(Seat *seat, SeatDialogText *text,
    void (*callback)(void *, SeatPromptResult), void *context)
{ return confirm_dialog(st(seat), PT_WEAK_CRYPTO, text, callback, context); }
static const SeatDialogPromptDescriptions *descriptions(Seat *seat)
{
    (void)seat;
    static const SeatDialogPromptDescriptions value = {
        "接受并保存", "仅本次连接", "取消", "取消", "继续", "取消"
    };
    return &value;
}
static bool pixel_size(Seat *seat, int *w, int *h)
{ pt_session *s = st(seat); s->options.draw.pixels(s->renderer, w, h); return true; }
static StripCtrlChars *strip(Seat *seat, BinarySink *sink, SeatInteractionContext context)
{ (void)context; return stripctrl_new_term(sink, false, 0, st(seat)->terminal); }
static void trust(Seat *seat, bool value)
{
    pt_session *s = st(seat); s->trusted = value;
    term_set_trust_status(s->terminal, value);
    text_message(s, PT_TRUST, value, "");
}
static bool cursor_query(Seat *seat, int *x, int *y)
{ *x = st(seat)->cursor_x; *y = st(seat)->cursor_y; return true; }
static const SeatVtable seat_vtable = {
    .output = output, .eof = nullseat_eof, .sent = nullseat_sent, .banner = banner,
    .get_userpass_input = userpass, .notify_session_started = started,
    .notify_remote_exit = remote_exit, .notify_remote_disconnect = disconnected,
    .connection_fatal = fatal, .nonfatal = seat_nonfatal_impl,
    .update_specials_menu = specials, .get_ttymode = ttymode, .set_busy_status = busy,
    .confirm_ssh_host_key = host_key, .confirm_weak_crypto_primitive = weak_crypto,
    .confirm_weak_cached_hostkey = weak_crypto, .prompt_descriptions = descriptions,
    .is_utf8 = nullseat_is_always_utf8, .echoedit_update = nullseat_echoedit_update,
    .get_display = nullseat_get_display, .get_windowid = nullseat_get_windowid,
    .get_window_pixel_size = pixel_size, .stripctrl_new = strip, .set_trust_status = trust,
    .can_set_trust_status = nullseat_can_set_trust_status_yes,
    .has_mixed_input_stream = nullseat_has_mixed_input_stream_yes,
    .verbose = nullseat_verbose_yes, .interactive = nullseat_interactive_yes,
    .get_cursor_position = cursor_query,
};
static void log_event(LogPolicy *policy, const char *value) { text_message(lp(policy), PT_LOG, 0, value); }
static void log_error(LogPolicy *policy, const char *value) { text_message(lp(policy), PT_ERROR, 0, value); }
static int log_append(LogPolicy *policy, Filename *filename, void (*callback)(void *, int), void *context)
{
    pt_session *s = lp(policy);
    if (s->waiting) return 0;
    s->waiting = pr_core_request(s->core, 1, cancel_confirmation, s);
    if (!s->waiting) return 0;
    s->waiting_kind = PT_LOG_APPEND; s->append = callback; s->confirm_context = context;
    message(s, PT_LOG_APPEND, 0, filename_to_str(filename), strlen(filename_to_str(filename)), s->waiting);
    return -1;
}
static const LogPolicyVtable policy_vtable = {log_event, log_append, log_error, null_lp_verbose_yes};

/* 打印和其余窗口策略尚未接入；显式报告能力，不能把空方法作为完整实现。 */
printer_job *printer_start_job(char *name)
{ (void)name; if (active_session) unsupported(active_session, 14, "打印"); return NULL; }
void printer_job_data(printer_job *job, const void *data, size_t size)
{ (void)job; (void)data; (void)size; assert(!"打印任务未创建"); }
void printer_finish_job(printer_job *job) { assert(job == NULL); }
printer_enum *printer_start_enum(int *count) { *count = 0; return NULL; }
char *printer_get_name(printer_enum *enumeration, int index)
{ (void)enumeration; (void)index; return NULL; }
void printer_finish_enum(printer_enum *enumeration) { (void)enumeration; }

static void *create(pr_core *core, const void *options, size_t size)
{
    if (size != sizeof(pt_options)) return NULL;
    const pt_options *input = options;
    if (!input->draw.create || !input->draw.configure || !input->draw.begin || !input->draw.text ||
        !input->draw.cursor || !input->draw.sigil || !input->draw.width || !input->draw.end ||
        !input->draw.destroy || !input->draw.drained || !input->draw.pixels || !input->draw.revision) return NULL;
    pt_session *s = snew(pt_session);
    memset(s, 0, sizeof(*s));
    s->core = core; s->options = *input;
    s->renderer = input->draw.create(core, &input->render);
    if (!s->renderer) { sfree(s); return NULL; }
    active_session = s;
    random_ref();
    s->window.vt = &window_vtable; s->seat.vt = &seat_vtable; s->policy.vt = &policy_vtable;
    s->conf = conf_new(); do_defaults(NULL, s->conf);
    conf_set_str(s->conf, CONF_line_codepage, "UTF-8");
    conf_set_int(s->conf, CONF_savelines, 2000);
    conf_set_bool(s->conf, CONF_no_remote_resize, true);
    conf_set_bool(s->conf, CONF_ssh_connection_sharing, false);
    init_ucs_generic(s->conf, &s->unicode);
    s->terminal = term_init(s->conf, &s->unicode, &s->window);
    term_size(s->terminal, input->render.rows, input->render.columns, 2000);
    term_set_focus(s->terminal, true);
    s->log = log_init(&s->policy, s->conf);
    term_provide_logctx(s->terminal, s->log);
    term_update(s->terminal);
    return s;
}
static pr_status open_connection(pt_session *s, const pr_command *command)
{
    if (s->backend) return PR_BUSY;
    if (command->size != sizeof(pt_connection)) return PR_ARGUMENT;
    const pt_connection *c = command->data;
    if (!memchr(c->host, 0, sizeof(c->host)) || !c->host[0] ||
        !memchr(c->user, 0, sizeof(c->user)) || !memchr(c->keyfile, 0, sizeof(c->keyfile)) ||
        !memchr(c->command, 0, sizeof(c->command)) || c->port < 1 || c->port > 65535)
        return PR_ARGUMENT;
    conf_set_int(s->conf, CONF_protocol, PROT_SSH);
    conf_set_int(s->conf, CONF_port, c->port);
    conf_set_str(s->conf, CONF_host, c->host);
    conf_set_str(s->conf, CONF_username, c->user);
    conf_set_str(s->conf, CONF_remote_cmd, c->command);
    conf_set_bool(s->conf, CONF_nopty, !c->request_pty);
    conf_set_bool(s->conf, CONF_tryagent, false);
    Filename *key = filename_from_str(c->keyfile);
    conf_set_filename(s->conf, CONF_keyfile, key); filename_free(key);
    term_set_trust_status(s->terminal, true);
    char *realhost = NULL;
    char *error = backend_init(&ssh_backend, &s->seat, &s->backend, s->log, s->conf,
                               c->host, c->port, &realhost, true, false);
    if (error) { text_message(s, PT_ERROR, 1, error); sfree(error); sfree(realhost); return PR_PLATFORM_ERROR; }
    term_setup_window_titles(s->terminal, realhost);
    sfree(realhost);
    term_provide_backend(s->terminal, s->backend);
    s->ldisc = ldisc_create(s->conf, s->terminal, s->backend, &s->seat);
    backend_size(s->backend, s->options.render.columns, s->options.render.rows);
    return PR_OK;
}
static pr_status screen(pt_session *s)
{
    term_invalidate(s->terminal); term_update(s->terminal);
    strbuf *value = strbuf_new();
    for (int y = 0; y < s->options.render.rows; ++y) {
        termline *line = term_get_line(s->terminal, y);
        for (int x = 0; x < line->cols; ++x) {
            unsigned long c = line->chars[x].chr;
            if (c == UCSWIDE) continue;
            if ((c & CSET_MASK) == CSET_ASCII) c = s->unicode.unitab_line[c & 0xff];
            if ((c & CSET_MASK) == CSET_LINEDRW) c = s->unicode.unitab_xterm[c & 0xff];
            put_utf8_char(value, c);
        }
        term_release_line(line);
        put_byte(value, '\n');
    }
    message(s, PT_SCREEN, (int)s->options.draw.revision(s->renderer), value->s, value->len, 0);
    strbuf_free(value); return PR_OK;
}
static pr_status command(void *context, const pr_command *command)
{
    pt_session *s = context;
    switch (command->type) {
      case PR_COMMAND_OPEN: return open_connection(s, command);
      case PR_COMMAND_INPUT:
        if (!s->ldisc) return PR_NOT_FOUND;
        size_t buffered = backend_sendbuffer(s->backend);
        if (buffered > 65536 || command->size > 65536 - buffered) return PR_BACKPRESSURE;
        term_seen_key_event(s->terminal);
        term_keyinput(s->terminal, CP_UTF8, command->data, (int)command->size);
        return PR_OK;
      case PR_COMMAND_REPLY: {
        if (!s->waiting || s->waiting != command->request_id) return PR_STALE;
        int kind = s->waiting_kind, answer = (int)command->argument;
        void (*callback)(void *, SeatPromptResult) = s->confirm;
        void (*append_callback)(void *, int) = s->append;
        void *callback_context = s->confirm_context;
        if (kind == PT_HOST_KEY && answer == 2)
            store_host_key(&s->seat, s->key_host, s->key_port, s->key_type, s->key_string);
        clear_confirmation(s);
        if (kind == PT_LOG_APPEND) append_callback(callback_context, answer >= 0 && answer <= 2 ? answer : 0);
        else callback(callback_context, answer == 1 || answer == 2 ? SPR_OK : SPR_USER_ABORT);
        return PR_OK;
      }
      case PR_COMMAND_CUSTOM:
        if (command->argument == PT_GET_SCREEN) return screen(s);
        if (command->argument == PT_FOCUS) {
            if (command->size != 1) return PR_ARGUMENT;
            term_set_focus(s->terminal, *(const char *)command->data != 0); return PR_OK;
        }
        if (command->argument == PT_KEY) {
            if (!s->ldisc || command->size != sizeof(int)) return PR_ARGUMENT;
            int key; memcpy(&key, command->data, sizeof(key));
            const char codes[] = {'A', 'B', 'D', 'C'};
            if (key < PT_KEY_UP || key > PT_KEY_RIGHT) return PR_ARGUMENT;
            bool consumed_alt = false;
            char data[32]; int count = format_arrow_key(data, s->terminal, codes[key - 1], false, false, false, &consumed_alt);
            term_keyinput(s->terminal, -1, data, count); return PR_OK;
        }
        if (command->argument == PT_SET_RENDER) {
            if (command->size != sizeof(pt_render_config)) return PR_ARGUMENT;
            const pt_render_config *config = command->data;
            if (config->generation <= s->options.render.generation) return PR_STALE;
            if (!s->options.draw.configure(s->renderer, config)) return PR_ARGUMENT;
            s->options.render = *config;
            term_size(s->terminal, config->rows, config->columns, 2000);
            term_invalidate(s->terminal); term_update(s->terminal);
            return PR_OK;
        }
        return PR_ARGUMENT;
      default: return PR_ARGUMENT;
    }
}
static void drained(void *context)
{
    pt_session *s = context;
    /* 请求和退出状态先于可替代帧重试，不无限追加状态队列。 */
    for (int i = PT_LOG_APPEND; i > PT_FRAME; --i) {
        pt_pending *pending = &s->pending[i];
        if (pending->data && pt_publish(s->core, pending->header, pending->data->s,
                                       pending->data->len, pending->request) == PR_OK) {
            strbuf_free(pending->data); pending->data = NULL;
        }
    }
    s->options.draw.drained(s->renderer);
}
static void destroy(void *context)
{
    pt_session *s = context; s->destroying = true;
    if (s->ldisc) { ldisc_free(s->ldisc); s->ldisc = NULL; }
    if (s->backend) { backend_free(s->backend); s->backend = NULL; }
    term_provide_backend(s->terminal, NULL);
    term_free(s->terminal);
    log_free(s->log);
    s->options.draw.destroy(s->renderer);
    clear_confirmation(s);
    for (int i = PT_FRAME + 1; i <= PT_LOG_APPEND; ++i)
        if (s->pending[i].data) strbuf_free(s->pending[i].data);
    conf_free(s->conf);
    random_unref();
    active_session = NULL;
    sfree(s);
}
const pr_module pt_terminal_module = {create, command, destroy, drained};
