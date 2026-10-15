#include "pa_agent.h"
#include "putty.h"
#include "ssh.h"
#include "pageant.h"
#include <limits.h>
#include <errno.h>
#ifdef _WIN32
#include "pa_windows.h"
#include "security-api.h"
#if HAVE_AFUNIX_H
#include <afunix.h>
#endif
#else
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
#ifdef _WIN32
    pa_windows_ipc *ipc;
    PageantClient legacy;
    bool registered;
    struct pageant_listen_state *unix_listener;
    HANDLE unix_endpoint;
#endif
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
typedef struct pa_snapshot { strbuf *rows; uint32_t count; } pa_snapshot;
static void snapshot_row(void *ctx, char **fingerprints, const char *comment,
                         uint32_t flags, struct pageant_pubkey *key)
{
    pa_snapshot *snapshot = ctx;
    strbuf *buf = snapshot->rows;
    snapshot->count++;
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
#ifdef _WIN32
static void legacy_request(void *context, const void *data, size_t size)
{
    pa_state *s = context;
    if (!s->destroying && !pr_core_is_closing(s->core))
        pageant_handle_msg(&s->legacy, NULL, make_ptrlen(data, size));
}
static void legacy_response(PageantClient *client, PageantClientRequestId *id, ptrlen response)
{
    (void)id;
    pa_state *s = container_of(client, pa_state, legacy);
    if (s->ipc) pa_windows_ipc_reply(s->ipc, response.ptr, response.len);
}
static bool legacy_ask(PageantClient *client, PageantClientDialogId *dialog, const char *comment)
{
    pa_state *s = container_of(client, pa_state, legacy);
    return ask(&s->client, dialog, comment);
}
static const PageantClientVtable legacy_vt = {NULL, legacy_response, legacy_ask};
#endif
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
        pa_snapshot snapshot = {strbuf_new(), 0};
        strbuf *rows = snapshot.rows;
        char *error = NULL;
        int status = pageant_enum_keys(snapshot_row, &snapshot, &error);
#ifdef _WIN32
        if (s->options.client_only && pa_windows_query_failed()) status = PAGEANT_ACTION_FAILURE;
#endif
        pa_packet header = {0};
        header.kind = PA_SNAPSHOT;
        header.count = snapshot.count;
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
#ifdef _WIN32
#if HAVE_AFUNIX_H && defined(SIO_AF_UNIX_GETPEERPID)
static bool unix_peer_is_current_process(SOCKET socket)
{
    ULONG pid = 0;
    DWORD returned = 0;
    /* 部分旧驱动成功写入 PID 却返回零长度；成功状态和实际 PID 必须同时匹配。 */
    return WSAIoctl(socket, SIO_AF_UNIX_GETPEERPID, NULL, 0, &pid, sizeof(pid),
                    &returned, NULL, NULL) == 0 && pid == GetCurrentProcessId();
}
static bool wait_unix_probe(SOCKET socket, bool readable, ULONGLONG deadline)
{
    ULONGLONG now = GetTickCount64();
    if (now >= deadline) return false;
    DWORD remaining = (DWORD)(deadline - now);
    struct timeval timeout = {(long)(remaining / 1000), (long)(remaining % 1000) * 1000};
    fd_set ready, errors;
    FD_ZERO(&ready); FD_SET(socket, &ready);
    FD_ZERO(&errors); FD_SET(socket, &errors);
    int result = select(0, readable ? &ready : NULL, readable ? NULL : &ready,
                        &errors, &timeout);
    return result > 0 && FD_ISSET(socket, &ready) && !FD_ISSET(socket, &errors);
}
static bool verify_unix_endpoint(const char *path)
{
    SOCKET listener = INVALID_SOCKET, probe = INVALID_SOCKET, accepted = INVALID_SOCKET;
    bool verified = false;
    ULONGLONG deadline = GetTickCount64() + 250;
    /* 只枚举上游拥有的原生套接字，不从私有 Socket 结构推断句柄布局。 */
    int index;
    for (SOCKET candidate = first_socket(&index); candidate != INVALID_SOCKET;
         candidate = next_socket(&index)) {
        SOCKADDR_UN address = {0};
        int length = sizeof(address);
        if (getsockname(candidate, (struct sockaddr *)&address, &length) == 0 &&
            address.sun_family == AF_UNIX &&
            memchr(address.sun_path, '\0', sizeof(address.sun_path)) &&
            !strcmp(address.sun_path, path)) {
            if (listener != INVALID_SOCKET) return false;
            listener = candidate;
        }
    }
    if (listener == INVALID_SOCKET) return false;
    probe = socket(AF_UNIX, SOCK_STREAM, 0);
    if (probe == INVALID_SOCKET) goto finished;
    u_long nonblocking = 1;
    if (!SetHandleInformation((HANDLE)probe, HANDLE_FLAG_INHERIT, 0) ||
        ioctlsocket(probe, FIONBIO, &nonblocking) != 0) goto finished;
    SOCKADDR_UN address = {0};
    address.sun_family = AF_UNIX;
    memcpy(address.sun_path, path, strlen(path) + 1);
    if (connect(probe, (struct sockaddr *)&address, sizeof(address)) != 0 &&
        WSAGetLastError() != WSAEWOULDBLOCK) goto finished;
    /* Windows 的本地 connect 可能等待 accept。仅接收自己的原生监听，
     * 不在 create 内运行核心或 GUI 消息循环，整个探针最多等待 250 ms。 */
    if (!wait_unix_probe(listener, true, deadline)) goto finished;
    accepted = accept(listener, NULL, NULL);
    if (accepted == INVALID_SOCKET ||
        !SetHandleInformation((HANDLE)accepted, HANDLE_FLAG_INHERIT, 0) ||
        ioctlsocket(accepted, FIONBIO, &nonblocking) != 0 ||
        !wait_unix_probe(probe, false, deadline)) goto finished;
    int error = 0, error_size = sizeof(error);
    if (getsockopt(probe, SOL_SOCKET, SO_ERROR, (char *)&error, &error_size) != 0 || error)
        goto finished;
    verified = unix_peer_is_current_process(probe) && unix_peer_is_current_process(accepted) &&
               GetTickCount64() < deadline;
  finished:
    if (accepted != INVALID_SOCKET) {
        /* accept 可能继承 WSAAsyncSelect；按上游代次屏障丢弃已排队的旧句柄消息。 */
        do_select(accepted, false);
        closesocket(accepted);
        done_with_socket(accepted);
    }
    if (probe != INVALID_SOCKET) closesocket(probe);
    return verified;
}
#endif
static bool start_unix_listener(pa_state *s)
{
    const char *path = s->options.socket_path;
    if (!path[0]) return true;
#if HAVE_AFUNIX_H && defined(SIO_AF_UNIX_GETPEERPID)
    /* 沿固定上游传入 ACP 原字节；Winsock 内核却按 UTF-8 解读 sun_path。
     * 仅把同一组实际字节严格转换给文件 API，不宣称任意 ACP 路径可用。 */
    if (strlen(path) >= sizeof(((SOCKADDR_UN *)0)->sun_path)) return false;
    int chars = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, NULL, 0);
    if (!chars) return false;
    wchar_t *wide = snewn(chars, wchar_t);
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, wide, chars) != chars) {
        sfree(wide); return false;
    }
    DWORD attributes = GetFileAttributesW(wide);
    DWORD error = GetLastError();
    /* 现存端点也拒绝，不使用原版 remove 去删除用户指定的既有文件。 */
    if (attributes != INVALID_FILE_ATTRIBUTES ||
        (error != ERROR_FILE_NOT_FOUND && error != ERROR_PATH_NOT_FOUND)) {
        sfree(wide); return false;
    }
    Plug *plug;
    s->unix_listener = pageant_listener_new(&plug, &s->client);
    Socket *socket = sk_newlistener_unix(path, plug);
    bool failed = sk_socket_error(socket) != NULL;
    pageant_listener_got_socket(s->unix_listener, socket);
    if (failed) { sfree(wide); return false; }
#ifdef PA_WINDOWS_TEST_NAMESPACE
    /* 专用宿主在此替换端点，回归 bind 与文件句柄获取之间的竞态。 */
    extern void pa_test_unix_after_bind(const char *path);
    pa_test_unix_after_bind(path);
#endif
    HANDLE endpoint = CreateFileW(wide, DELETE | FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING,
        FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, NULL);
    sfree(wide);
    if (endpoint == INVALID_HANDLE_VALUE) return false;
    FILE_ATTRIBUTE_TAG_INFO tag;
    BY_HANDLE_FILE_INFORMATION info;
    if (!GetFileInformationByHandleEx(endpoint, FileAttributeTagInfo, &tag, sizeof(tag)) ||
        tag.ReparseTag != IO_REPARSE_TAG_AF_UNIX ||
        !(tag.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) ||
        (tag.FileAttributes & FILE_ATTRIBUTE_DIRECTORY) ||
        !GetFileInformationByHandle(endpoint, &info) || info.nNumberOfLinks != 1) {
        CloseHandle(endpoint);
        return false;
    }
    /* 固定路径对象后，须实际连接回本次监听并核双方 PID；类型检查不能证明归属。 */
    if (!verify_unix_endpoint(path)) {
        CloseHandle(endpoint);
        return false;
    }
    /* 只有归属探针成功才取得删除权；收尾不再按路径删除。 */
    s->unix_endpoint = endpoint;
    return true;
#else
    return false;
#endif
}
#endif
static void destroy(void *context)
{
    pa_state *s = context;
    s->destroying = true;
#ifdef _WIN32
    /* 先解除旧 IPC 的同步等待，再撤销上游客户端与在途提示。 */
    pa_windows_ipc_stop(s->ipc); s->ipc = NULL;
#endif
    /* Runtime 先撤销请求；这里也覆盖 create 失败后的部分初始化。 */
    cancel_prompts(s);
#ifdef _WIN32
    if (s->registered) pageant_unregister_client(&s->legacy);
#endif
    if (s->listener) {
        pageant_listener_shutdown_connections(s->listener);
        pageant_listener_free(s->listener);
    }
#ifdef _WIN32
    if (s->unix_listener) {
        pageant_listener_shutdown_connections(s->unix_listener);
        pageant_listener_free(s->unix_listener);
    }
    if (s->unix_endpoint) {
        FILE_DISPOSITION_INFO disposition;
        disposition.DeleteFile = TRUE;
        SetFileInformationByHandle(s->unix_endpoint, FileDispositionInfo,
                                   &disposition, sizeof(disposition));
        CloseHandle(s->unix_endpoint);
    }
#endif
#ifndef _WIN32
    if (s->options.socket_path[0]) unlink(s->options.socket_path);
#endif
#ifdef _WIN32
    if (!s->options.client_only) pageant_delete_all();
#else
    pageant_delete_all();
#endif
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
    if (!got_advapi() || !pa_windows_class_name() ||
        (input->client_only && input->socket_path[0])) return NULL;
#else
    if (!valid_socket_path(input->socket_path)) return NULL;
#endif
    pa_state *s = snew(pa_state);
    memset(s, 0, sizeof(*s));
    s->core = core; s->options = *input; s->tail = &s->head;
    s->client.vt = &listener_vt; s->client.suppress_logging = true;
    active = s;
#ifdef _WIN32
    if (input->client_only) {
        /* 原 pageant.c 的客户端操作会调用 agent_query_synchronous。
         * 它们只在核心线程执行，绝不在 Qt GUI 或兼容窗口中等候。 */
        keylist_update();
        return s;
    }
#endif
    pageant_init();
    Plug *plug;
    s->listener = pageant_listener_new(&plug, &s->client);
#ifdef _WIN32
    char *endpoint = pa_windows_pipe_name();
    Socket *socket = new_named_pipe_listener(endpoint, plug);
#else
    const char *endpoint = input->socket_path;
    Socket *socket = new_unix_listener(unix_sock_addr(input->socket_path), plug);
#endif
    const char *error = sk_socket_error(socket);
    pageant_listener_got_socket(s->listener, socket);
    bool failed = error != NULL;
#ifdef _WIN32
    if (!failed) failed = !start_unix_listener(s);
    if (!failed) {
        s->legacy.vt = &legacy_vt; s->legacy.suppress_logging = true;
        pageant_register_client(&s->legacy); s->registered = true;
        s->ipc = pa_windows_ipc_start(legacy_request, s);
        failed = !s->ipc;
    }
#else
    if (!failed) failed = chmod(input->socket_path, 0600) != 0;
#endif
    pa_packet header = {0}; header.kind = PA_ENDPOINT;
    if (!failed) failed = publish(s, header, endpoint, strlen(endpoint), 0, PR_EVENT_STATE) != PR_OK;
#ifdef _WIN32
    sfree(endpoint);
#endif
    if (failed) { destroy(s); return NULL; }
    keylist_update();
    return s;
}
static void add_key(pa_state *s, const char *path, const char *passphrase,
                     bool encrypted, uint64_t operation)
{
#ifdef _WIN32
    wchar_t *wide_path = decode_utf8_to_wide_string(path);
    Filename *file = filename_from_wstr(wide_path);
    sfree(wide_path);
#else
    Filename *file = filename_from_str(path);
#endif
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
