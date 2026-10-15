#include "pa_agent.h"
#include "putty.h"
#include "ssh.h"
#include "pageant.h"
#include <limits.h>
#include <errno.h>
#ifndef _WIN32
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#endif

typedef struct pa_state pa_state;
typedef struct pa_pending {
    struct pa_pending *next;
    pa_state *owner;
    uint64_t id, operation;
    PageantClientDialogId *dialog;
    char *path, *comment;
    bool encrypted, announced;
} pa_pending;
typedef struct pa_output {
    struct pa_output *next;
    strbuf *data;
    uint64_t request;
    pr_event_type type;
} pa_output;
struct pa_state {
    pr_core *core;
    PageantListenerClient client;
    struct pageant_listen_state *listener;
    pa_pending *prompts;
    pa_output *head, **tail;
    unsigned outputs;
    bool dirty, queued, destroying;
    pa_options options;
};
static pa_state *active;
static void flush(void *context);
static void scheduled_flush(void *context)
{
    pa_state *s = context;
    s->queued = false;
    flush(s);
}

static pr_status publish(pa_state *s, pa_packet header, const void *bytes,
                         size_t size, uint64_t request, pr_event_type type)
{
    header.magic = PA_MAGIC;
    header.length = (uint32_t)size;
    strbuf *buf = strbuf_new();
    put_data(buf, &header, sizeof(header));
    if (size) put_data(buf, bytes, size);
    pr_event event = {0};
    event.object_id = 1;
    event.request_id = request;
    event.type = type;
    event.data = buf->s;
    event.size = buf->len;
    pr_status result = pr_core_emit(s->core, &event);
    strbuf_free(buf);
    return result;
}
static void retain(pa_state *s, pa_packet header, const void *bytes,
                   size_t length, uint64_t request, pr_event_type type)
{
    header.magic = PA_MAGIC;
    header.length = (uint32_t)length;
    pa_output *out = snew(pa_output);
    out->data = strbuf_new();
    put_data(out->data, &header, sizeof(header));
    if (length) put_data(out->data, bytes, length);
    out->request = request;
    out->type = type;
    out->next = NULL;
    *s->tail = out;
    s->tail = &out->next;
    s->outputs++;
    flush(s);
}
static void result(pa_state *s, uint32_t action, uint32_t code,
                   const char *detail, uint64_t request)
{
    /* 操作开始前保留有界结果槽；状态快照另行合并，不覆盖操作结果。 */
    pa_packet header = {PA_MAGIC, PA_RESULT, action, code, 0, 0};
    size_t length = detail ? strlen(detail) : 0;
    if (length > 4096) length = 4096;
    retain(s, header, detail, length, request, PR_EVENT_DATA);
}
static void snapshot_row(void *ctx, char **fingerprints, const char *comment,
                         uint32_t flags, struct pageant_pubkey *key)
{
    strbuf *buf = ctx;
    const char *fp = fingerprints[SSH_FPTYPE_SHA256];
    if (!fp) fp = fingerprints[SSH_FPTYPE_MD5];
    if (!fp) fp = "";
    pa_public_key row = {(uint32_t)key->ssh_version, flags,
        (uint32_t)key->blob->len, (uint32_t)strlen(comment), (uint32_t)strlen(fp)};
    /* 先限额，避免网络添加的大量身份产生无界 GUI 快照。 */
    size_t extra = sizeof(row) + row.blob_size + row.comment_size + row.fingerprint_size;
    if (buf->len > PA_MAX_PUBLIC_BYTES || extra > PA_MAX_PUBLIC_BYTES - buf->len) {
        if (buf->len <= PA_MAX_PUBLIC_BYTES)
            put_padding(buf, PA_MAX_PUBLIC_BYTES + 1 - buf->len, 0);
        return;
    }
    put_data(buf, &row, sizeof(row));
    put_datapl(buf, ptrlen_from_strbuf(key->blob));
    put_data(buf, comment, row.comment_size);
    put_data(buf, fp, row.fingerprint_size);
}
static void free_prompt(pa_pending *p)
{
    sfree(p->path);
    sfree(p->comment);
    sfree(p);
}
static void unlink_prompt(pa_pending *p)
{
    pa_pending **link = &p->owner->prompts;
    while (*link && *link != p) link = &(*link)->next;
    if (*link) *link = p->next;
}
static void cancelled(void *ctx)
{
    pa_pending *p = ctx;
    unlink_prompt(p);
    if (p->dialog) pageant_passphrase_request_refused(p->dialog);
    free_prompt(p);
}
static void cancel_prompt(pa_state *s, pa_pending *p)
{
    pr_core_forget_request(s->core, p->id);
    const uint64_t operation = p->operation;
    const uint64_t request = p->id;
    const bool adding = p->path != NULL;
    cancelled(p);
    if (!s->destroying) {
        /* 撤销与结果一样保留到队列可写，不能让背压留下已失效的对话框。 */
        pa_packet header = {0}; header.kind = PA_DISMISS;
        retain(s, header, NULL, 0, request, PR_EVENT_STATE);
    }
    if (adding && !s->destroying)
        result(s, PA_ADD, PA_CANCELLED, NULL, operation);
}
static void cancel_prompts(pa_state *s)
{
    while (s->prompts) cancel_prompt(s, s->prompts);
    pageant_forget_passphrases();
}
static pr_status invalid_command(pa_state *s, const pr_command *cmd)
{
    /* Runtime 已消费一次性回复编号，错误载荷也必须结束对应提示。 */
    if (cmd->type == PR_COMMAND_REPLY) {
        for (pa_pending *p = s->prompts; p; p = p->next)
            if (p->id == cmd->request_id) { cancel_prompt(s, p); break; }
    }
    return PR_ARGUMENT;
}
static pa_pending *prompt(pa_state *s, PageantClientDialogId *dialog,
                          const char *comment, const char *path,
                          bool encrypted, uint64_t operation)
{
    pa_pending *p = snew(pa_pending);
    memset(p, 0, sizeof(*p));
    p->owner = s;
    p->dialog = dialog;
    p->comment = dupstr(comment ? comment : "");
    p->path = path ? dupstr(path) : NULL;
    p->encrypted = encrypted;
    p->operation = operation;
    p->id = pr_core_request(s->core, 1, cancelled, p);
    if (!p->id) { free_prompt(p); return NULL; }
    p->next = s->prompts;
    s->prompts = p;
    return p;
}
static bool ask(PageantListenerClient *client, PageantClientDialogId *id,
                const char *comment)
{
    pa_state *s = container_of(client, pa_state, client);
    if (s->destroying || pr_core_is_closing(s->core)) return false;
    pa_pending *p = prompt(s, id, comment, NULL, false, 0);
    if (!p) return false;
    /* ask 返回后才允许提交口令；发布事件本身不会同步执行 GUI 回复。 */
    flush(s);
    return true;
}
static void log_message(PageantListenerClient *client, const char *fmt, va_list ap)
{ (void)client; (void)fmt; (void)ap; }
static const PageantListenerClientVtable listener_vt = {log_message, ask};
static void flush(void *context)
{
    pa_state *s = context;
    if (s->destroying || pr_core_is_closing(s->core)) return;
    while (s->head) {
        pa_output *out = s->head;
        pr_event event = {0};
        event.object_id = 1; event.request_id = out->request; event.type = out->type;
        event.data = out->data->s; event.size = out->data->len;
        if (pr_core_emit(s->core, &event) != PR_OK) return;
        s->head = out->next;
        s->outputs--;
        strbuf_free(out->data);
        sfree(out);
    }
    s->tail = &s->head;
    for (pa_pending *p = s->prompts; p; p = p->next) {
        if (p->announced) continue;
        pa_packet header = {0};
        header.kind = PA_PROMPT;
        header.action = p->path ? PA_ADD : PA_REPLY;
        if (publish(s, header, p->comment, strlen(p->comment), p->id,
                    PR_EVENT_REQUEST) != PR_OK) return;
        p->announced = true;
    }
    if (s->dirty) {
        strbuf *rows = strbuf_new();
        char *error = NULL;
        int status = pageant_enum_keys(snapshot_row, rows, &error);
        pa_packet header = {0};
        header.kind = PA_SNAPSHOT;
        header.count = (uint32_t)(pageant_count_ssh1_keys() + pageant_count_ssh2_keys());
        if (status != PAGEANT_ACTION_OK || rows->len > PA_MAX_PUBLIC_BYTES) {
            header.result = PA_FAILURE;
            header.count = 0;
            strbuf_clear(rows);
        }
        if (publish(s, header, rows->s, rows->len, 0, PR_EVENT_STATE) == PR_OK)
            s->dirty = false;
        strbuf_free(rows);
        sfree(error);
    }
}
void keylist_update(void)
{
    if (!active || active->destroying) return;
    active->dirty = true;
    if (!active->queued) {
        active->queued = true;
        pr_core_queue(active->core, scheduled_flush, active);
    }
}
#ifndef _WIN32
static bool valid_socket_path(const char *path)
{
    if (!path || path[0] != '/' || strlen(path) >= 108) return false;
    char *copy = dupstr(path), *slash = strrchr(copy, '/');
    bool valid = false;
    if (!slash || !slash[1]) { sfree(copy); return false; }
    *slash = '\0';
    /* 每层用 O_NOFOLLOW 打开，拒绝路径中任何符号链接及 ..。 */
    int fd = open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    char *save = NULL;
    for (char *part = strtok_r(copy, "/", &save); fd >= 0 && part;
         part = strtok_r(NULL, "/", &save)) {
        if (!strcmp(part, ".") || !strcmp(part, "..")) { close(fd); fd = -1; break; }
        int next = openat(fd, part, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        close(fd); fd = next;
    }
    if (fd >= 0) {
        struct stat st;
        valid = !fstat(fd, &st) && st.st_uid == getuid() && !(st.st_mode & 077);
        if (valid) {
            const char *name = strrchr(path, '/') + 1;
            valid = fstatat(fd, name, &st, AT_SYMLINK_NOFOLLOW) < 0 && errno == ENOENT;
        }
        close(fd);
    }
    sfree(copy);
    return valid;
}
#endif
static void destroy(void *context)
{
    pa_state *s = context;
    s->destroying = true;
    /* Runtime 先撤销请求；这里也覆盖 create 失败后的部分初始化。 */
    cancel_prompts(s);
    if (s->listener) {
        pageant_listener_shutdown_connections(s->listener);
        pageant_listener_free(s->listener);
    }
#ifndef _WIN32
    if (s->options.socket_path[0]) unlink(s->options.socket_path);
#endif
    pageant_delete_all();
    pageant_forget_passphrases();
    while (s->head) {
        pa_output *out = s->head;
        s->head = out->next;
        strbuf_free(out->data); sfree(out);
    }
    active = NULL;
    sfree(s);
}
static void *create(pr_core *core, const void *options, size_t size)
{
    if (!options || size != sizeof(pa_options) || active) return NULL;
    const pa_options *input = options;
    if (!memchr(input->socket_path, '\0', sizeof(input->socket_path))) return NULL;
#ifdef _WIN32
    /* Windows 双 IPC 在原生适配阶段接入，不能暴露一个貌似可用的代理。 */
    (void)core; return NULL;
#else
    if (!valid_socket_path(input->socket_path)) return NULL;
    pa_state *s = snew(pa_state);
    memset(s, 0, sizeof(*s));
    s->core = core; s->options = *input; s->tail = &s->head;
    s->client.vt = &listener_vt; s->client.suppress_logging = true;
    pageant_init();
    active = s;
    Plug *plug;
    s->listener = pageant_listener_new(&plug, &s->client);
    Socket *socket = new_unix_listener(unix_sock_addr(input->socket_path), plug);
    const char *error = sk_socket_error(socket);
    pageant_listener_got_socket(s->listener, socket);
    if (error || chmod(input->socket_path, 0600)) { destroy(s); return NULL; }
    pa_packet header = {0}; header.kind = PA_ENDPOINT;
    if (publish(s, header, input->socket_path, strlen(input->socket_path), 0,
                PR_EVENT_STATE) != PR_OK) { destroy(s); return NULL; }
    keylist_update();
    return s;
#endif
}
static void add_key(pa_state *s, const char *path, const char *passphrase,
                     bool encrypted, uint64_t operation)
{
    Filename *file = filename_from_str(path);
    char *detail = NULL;
    int status = pageant_add_keyfile(file, passphrase, &detail, encrypted);
    filename_free(file);
    pageant_forget_passphrases();
    if (status == PAGEANT_ACTION_NEED_PP) {
        if (!prompt(s, NULL, detail, path, encrypted, operation))
            result(s, PA_ADD, PA_FAILURE, "Passphrase request limit", operation);
        flush(s);
    } else {
        result(s, PA_ADD, status == PAGEANT_ACTION_OK ? PA_SUCCESS :
               status == PAGEANT_ACTION_WARNING ? PA_WARNING : PA_FAILURE, detail, operation);
    }
    sfree(detail);
    keylist_update();
}
static pr_status command(void *context, const pr_command *cmd)
{
    pa_state *s = context;
    if (cmd->object_id != 1 || cmd->size < sizeof(pa_input)) return invalid_command(s, cmd);
    pa_input in;
    memcpy(&in, cmd->data, sizeof(in));
    const size_t total = (size_t)in.path_size + in.passphrase_size + in.blob_size;
    if (in.magic != PA_MAGIC || in.reserved || in.flags & ~(PA_ENCRYPTED | PA_CANCEL) ||
        total != cmd->size - sizeof(in) || total > 1024 * 1024 ||
        in.path_size > 65536 || in.passphrase_size > 65536) return invalid_command(s, cmd);
    const char *bytes = (const char *)cmd->data + sizeof(in);
    if (memchr(bytes, '\0', (size_t)in.path_size + in.passphrase_size)) return invalid_command(s, cmd);
    if (cmd->type == PR_COMMAND_REPLY && in.action != PA_REPLY) return invalid_command(s, cmd);
    if (s->outputs >= 64 && in.action != PA_REPLY) return PR_BACKPRESSURE;
    if (in.action == PA_REPLY) {
        if (cmd->type != PR_COMMAND_REPLY || in.path_size || in.blob_size ||
            in.flags & ~PA_CANCEL) return invalid_command(s, cmd);
        pa_pending *p = s->prompts;
        while (p && p->id != cmd->request_id) p = p->next;
        if (!p) return PR_STALE;
        unlink_prompt(p);
        if (p->dialog) {
            if (in.flags & PA_CANCEL) pageant_passphrase_request_refused(p->dialog);
            else pageant_passphrase_request_success(p->dialog,
                make_ptrlen(bytes + in.path_size, in.passphrase_size));
        } else if (in.flags & PA_CANCEL) {
            result(s, PA_ADD, PA_CANCELLED, NULL, p->operation);
        } else {
            char *pp = dupprintf("%.*s", (int)in.passphrase_size, bytes + in.path_size);
            add_key(s, p->path, pp, p->encrypted, p->operation);
            smemclr(pp, in.passphrase_size); sfree(pp);
        }
        free_prompt(p);
        return PR_OK;
    }
    if (cmd->type != PR_COMMAND_CUSTOM || in.action < PA_REFRESH ||
        in.action > PA_REENCRYPT_ALL) return PR_ARGUMENT;
    if (in.action == PA_REFRESH) { keylist_update(); return PR_OK; }
    if (in.action == PA_ADD) {
        if (!in.path_size || in.blob_size) return PR_ARGUMENT;
        for (pa_pending *p = s->prompts; p; p = p->next) if (p->path) return PR_BUSY;
        char *path = dupprintf("%.*s", (int)in.path_size, bytes);
        char *pp = in.passphrase_size ? dupprintf("%.*s", (int)in.passphrase_size,
                                               bytes + in.path_size) : NULL;
        add_key(s, path, pp, (in.flags & PA_ENCRYPTED) != 0, cmd->request_id);
        if (pp) { smemclr(pp, in.passphrase_size); sfree(pp); }
        sfree(path);
        return PR_OK;
    }
    /* 删除前完成现有提示，避免旧口令让已移除身份重新出现。 */
    if (in.action == PA_DELETE || in.action == PA_DELETE_ALL) cancel_prompts(s);
    char *detail = NULL;
    int status;
    if (in.action == PA_DELETE_ALL) status = pageant_delete_all_keys(&detail);
    else if (in.action == PA_REENCRYPT_ALL) status = pageant_reencrypt_all_keys(&detail);
    else {
        if (!in.blob_size || (in.ssh_version != 1 && in.ssh_version != 2)) return PR_ARGUMENT;
        struct pageant_pubkey key;
        key.ssh_version = (int)in.ssh_version;
        key.blob = strbuf_new();
        key.comment = dupstr("");
        put_data(key.blob, bytes + in.path_size + in.passphrase_size, in.blob_size);
        status = in.action == PA_DELETE ? pageant_delete_key(&key, &detail) :
                                        pageant_reencrypt_key(&key, &detail);
        strbuf_free(key.blob); sfree(key.comment);
    }
    result(s, in.action, status == PAGEANT_ACTION_OK ? PA_SUCCESS :
           status == PAGEANT_ACTION_WARNING ? PA_WARNING : PA_FAILURE, detail, cmd->request_id);
    sfree(detail);
    keylist_update();
    return PR_OK;
}
const pr_module pa_agent_module = {create, command, destroy, flush};
