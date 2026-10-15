/* 使用真实 Windows 网络及 HANDLE；本模块从不初始化 PRNG 或会话存储。 */
#include "event_module.h"
#include "putty.h"

struct event_module {
    pr_core *core;
    Plug plug;
    Socket *socket;
    HANDLE manual_event, auto_event;
    HandleWait *manual_wait, *auto_wait;
    DWORD owner;
};
static volatile LONG thread_errors, destroyed;
unsigned windows_event_thread_errors(void)
{ return (unsigned)InterlockedCompareExchange(&thread_errors, 0, 0); }
unsigned windows_event_destroyed(void)
{ return (unsigned)InterlockedCompareExchange(&destroyed, 0, 0); }

static pr_status emit(struct event_module *module, pr_event_type type,
                      const void *data, size_t size, uint64_t object)
{
    if (module->owner != GetCurrentThreadId()) InterlockedIncrement(&thread_errors);
    pr_event event;
    memset(&event, 0, sizeof(event));
    event.type = type;
    event.object_id = object;
    event.data = data;
    event.size = size;
    return pr_core_emit(module->core, &event);
}
static void manual_ready(void *context)
{
    struct event_module *module = context;
    assert(ResetEvent(module->manual_event));
    emit(module, PR_EVENT_STATE, "manual", 6, 0);
}
static void auto_ready(void *context)
{
    struct event_module *module = context;
    emit(module, PR_EVENT_STATE, "auto", 4, 0);
}
static void log_event(Plug *plug, Socket *socket, PlugLogType type,
                      SockAddr *address, int port, const char *message, int code)
{
    struct event_module *module = container_of(plug, struct event_module, plug);
    (void)socket; (void)address; (void)port; (void)message; (void)code;
    if (type == PLUGLOG_CONNECT_SUCCESS)
        emit(module, PR_EVENT_STATE, "connected", 9, 0);
}
static void closing(Plug *plug, PlugCloseType type, const char *message)
{
    struct event_module *module = container_of(plug, struct event_module, plug);
    Socket *socket = module->socket;
    module->socket = NULL;
    if (socket) sk_close(socket);
    emit(module, type == PLUGCLOSE_NORMAL ? PR_EVENT_STATE : PR_EVENT_ERROR,
         message ? message : "closed", message ? strlen(message) : 6, 0);
}
static void receive(Plug *plug, int urgent, const char *data, size_t size)
{
    struct event_module *module = container_of(plug, struct event_module, plug);
    emit(module, PR_EVENT_DATA, data, size, urgent == 2 ? 2 : 1);
}
static void sent(Plug *plug, size_t backlog) { (void)plug; (void)backlog; }
static int accepting(Plug *plug, accept_fn_t fn, accept_ctx_t context)
{ (void)plug; (void)fn; (void)context; return 1; }
static const PlugVtable plug_vtable = {log_event, closing, receive, sent, accepting};

static void *create(pr_core *core, const void *options, size_t size)
{
    (void)options; (void)size;
    struct event_module *module = snew(struct event_module);
    memset(module, 0, sizeof(*module));
    module->core = core;
    module->owner = GetCurrentThreadId();
    module->plug.vt = &plug_vtable;
    module->manual_event = CreateEventW(NULL, TRUE, FALSE, NULL);
    module->auto_event = CreateEventW(NULL, FALSE, FALSE, NULL);
    if (!module->manual_event || !module->auto_event) {
        if (module->manual_event) CloseHandle(module->manual_event);
        if (module->auto_event) CloseHandle(module->auto_event);
        sfree(module);
        return NULL;
    }
    module->manual_wait = add_handle_wait(module->manual_event, manual_ready, module);
    module->auto_wait = add_handle_wait(module->auto_event, auto_ready, module);
    return module;
}
static pr_status command(void *context, const pr_command *command)
{
    struct event_module *module = context;
    if (module->owner != GetCurrentThreadId()) InterlockedIncrement(&thread_errors);
    switch (command->type) {
      case PR_COMMAND_OPEN: {
        char *canonical = NULL;
        if (module->socket) sk_close(module->socket);
        SockAddr *address = sk_namelookup("127.0.0.1", &canonical, ADDRTYPE_IPV4);
        sfree(canonical);
        /* 非 inline OOB，确保走实际 FD_OOB 而不是普通 FD_READ。 */
        module->socket = sk_new(address, (int)command->argument,
                                false, false, true, false, &module->plug);
        return sk_socket_error(module->socket) ? PR_PLATFORM_ERROR : PR_OK;
      }
      case PR_COMMAND_INPUT:
        if (!module->socket) return PR_NOT_FOUND;
        sk_write(module->socket, command->data, command->size);
        return PR_OK;
      case PR_COMMAND_CLOSE_OBJECT:
        if (module->socket) { sk_close(module->socket); module->socket = NULL; }
        return emit(module, PR_EVENT_STATE, "local-close", 11, 0);
      case PR_COMMAND_CUSTOM:
        if (command->argument == 1)
            return SetEvent(module->manual_event) ? PR_OK : PR_PLATFORM_ERROR;
        if (command->argument == 2)
            return SetEvent(module->auto_event) ? PR_OK : PR_PLATFORM_ERROR;
        if (command->argument == 3) {
            /* 已激活但未分发的 HANDLE 在核心关闭时撤销。 */
            SetEvent(module->manual_event);
            SetEvent(module->auto_event);
            pr_core_begin_close(module->core);
            return PR_OK;
        }
        return PR_ARGUMENT;
      default:
        return PR_ARGUMENT;
    }
}
static void destroy(void *context)
{
    struct event_module *module = context;
    if (module->owner != GetCurrentThreadId()) InterlockedIncrement(&thread_errors);
    if (module->socket) sk_close(module->socket);
    delete_handle_wait(module->manual_wait);
    delete_handle_wait(module->auto_wait);
    CloseHandle(module->manual_event);
    CloseHandle(module->auto_event);
    InterlockedIncrement(&destroyed);
    sfree(module);
}
const pr_module windows_event_module = {create, command, destroy, NULL};
