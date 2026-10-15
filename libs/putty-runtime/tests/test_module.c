#include "test_module.h"
#include "putty.h"
#include "storage.h"
#ifndef _WINDOWS
#include <pthread.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <poll.h>
#endif

struct test_module {
    pr_core *core;
    Plug plug;
    Socket *socket;
    pr_ticket timer, callback;
    uint64_t request;
    unsigned callback_count;
    bool urgent_notified;
    char pending[65536];
    size_t pending_size;
#ifdef _WINDOWS
    DWORD thread;
    HANDLE event;
    HandleWait *wait;
#else
    pthread_t thread;
#endif
};
static unsigned destroyed, late_callbacks, cancelled, thread_errors, oversize_done;
static unsigned load_counter(unsigned *counter)
{
#ifdef _WINDOWS
    return (unsigned)InterlockedCompareExchange((volatile LONG *)counter, 0, 0);
#else
    return __atomic_load_n(counter, __ATOMIC_SEQ_CST);
#endif
}
static void increment(unsigned *counter)
{
#ifdef _WINDOWS
    InterlockedIncrement((volatile LONG *)counter);
#else
    __atomic_add_fetch(counter, 1, __ATOMIC_SEQ_CST);
#endif
}
unsigned runtime_test_oversize_done(void) { return load_counter(&oversize_done); }
unsigned runtime_test_destroyed(void) { return load_counter(&destroyed); }
unsigned runtime_test_late_callbacks(void) { return load_counter(&late_callbacks); }
unsigned runtime_test_cancelled(void) { return load_counter(&cancelled); }
unsigned runtime_test_thread_errors(void) { return load_counter(&thread_errors); }

static void check_thread(struct test_module *module)
{
#ifdef _WINDOWS
    if (module->thread != GetCurrentThreadId()) increment(&thread_errors);
#else
    if (!pthread_equal(module->thread, pthread_self())) increment(&thread_errors);
#endif
}
static pr_status emit(struct test_module *module, pr_event_type type,
                      const void *data, size_t size, uint64_t request)
{
    pr_event event;
    check_thread(module);
    memset(&event, 0, sizeof(event));
    event.type = type;
    event.object_id = 1;
    event.request_id = request;
    event.data = data;
    event.size = size;
    return pr_core_emit(module->core, &event);
}
static void late(void *context)
{
    (void)context;
    increment(&late_callbacks);
}
static void timer(void *context)
{
    struct test_module *module = context;
    emit(module, PR_EVENT_STATE, "timer", 5, 0);
}
static void callback(void *context)
{
    struct test_module *module = context;
    if (++module->callback_count < 200)
        pr_core_queue(module->core, callback, module);
    else
        emit(module, PR_EVENT_STATE, "callback", 8, 0);
}
static void close_from_callback(void *context)
{
    struct test_module *module = context;
    pr_core_begin_close(module->core);
}
static void request_cancelled(void *context)
{
    struct test_module *module = context;
    check_thread(module);
    increment(&cancelled);
}
static void log_event(Plug *plug, Socket *socket, PlugLogType type, SockAddr *addr,
                      int port, const char *message, int code)
{
    struct test_module *module = container_of(plug, struct test_module, plug);
    (void)socket; (void)addr; (void)port; (void)message; (void)code;
    if (type == PLUGLOG_CONNECT_SUCCESS)
        emit(module, PR_EVENT_STATE, "connected", 9, 0);
}
static void closing(Plug *plug, PlugCloseType type, const char *message)
{
    struct test_module *module = container_of(plug, struct test_module, plug);
    Socket *socket = module->socket;
    module->socket = NULL;
    if (socket) sk_close(socket);
    emit(module, type == PLUGCLOSE_NORMAL ? PR_EVENT_STATE : PR_EVENT_ERROR,
         message ? message : "disconnected", message ? strlen(message) : 12, 0);
}
static void receive(Plug *plug, int urgent, const char *data, size_t size)
{
    struct test_module *module = container_of(plug, struct test_module, plug);
    if (urgent && !module->urgent_notified) {
        module->urgent_notified = true;
        emit(module, PR_EVENT_STATE, "urgent", 6, 0);
    }
    if (emit(module, PR_EVENT_DATA, data, size, 0) != PR_OK) {
        assert(size <= sizeof(module->pending) - module->pending_size);
        memcpy(module->pending + module->pending_size, data, size);
        module->pending_size += size;
        sk_set_frozen(module->socket, true);
    }
}
static void sent(Plug *plug, size_t backlog) { (void)plug; (void)backlog; }
static int accepting(Plug *plug, accept_fn_t fn, accept_ctx_t context)
{
    (void)plug; (void)fn; (void)context;
    return 1;
}
static const PlugVtable plug_vtable = {log_event, closing, receive, sent, accepting};
static void *create(pr_core *core, const void *options, size_t size)
{
    struct test_module *module = snew(struct test_module);
    (void)options; (void)size;
    memset(module, 0, sizeof(*module));
    module->core = core;
    module->plug.vt = &plug_vtable;
#ifdef _WINDOWS
    module->thread = GetCurrentThreadId();
#else
    module->thread = pthread_self();
#endif
    random_ref();
    return module;
}
static pr_status command(void *context, const pr_command *command)
{
    struct test_module *module = context;
    check_thread(module);
    switch (command->type) {
    case PR_COMMAND_OPEN: {
        char *canonical;
        if (module->socket) sk_close(module->socket);
        SockAddr *address = sk_namelookup("127.0.0.1", &canonical, ADDRTYPE_IPV4);
        sfree(canonical);
        module->socket = sk_new(address, (int)command->argument,
                                false, true, true, false, &module->plug);
        if (sk_socket_error(module->socket)) return PR_PLATFORM_ERROR;
        return PR_OK;
    }
    case PR_COMMAND_INPUT:
        if (!module->socket) return PR_NOT_FOUND;
        sk_write(module->socket, command->data, command->size);
        return PR_OK;
    case PR_COMMAND_CLOSE_OBJECT:
        if (module->socket) { sk_close(module->socket); module->socket = NULL; }
        return PR_OK;
    case PR_COMMAND_REPLY:
        return emit(module, PR_EVENT_STATE, "reply", 5, command->request_id);
    case PR_COMMAND_CUSTOM:
        if (command->argument == 1) {
            pr_core_queue(module->core, callback, module);
            module->timer = pr_core_schedule(module->core, 15, timer, module);
            module->callback = pr_core_queue(module->core, late, module);
            pr_core_cancel(module->core, module->callback);
            pr_core_cancel(module->core, module->callback);
            pr_ticket cancel_timer = pr_core_schedule(module->core, 2, late, module);
            pr_core_cancel(module->core, cancel_timer);
        } else if (command->argument == 2) {
            module->request = pr_core_request(module->core, 1, request_cancelled, module);
            return emit(module, PR_EVENT_REQUEST, "confirm", 7, module->request);
        } else if (command->argument == 3) {
            /* 模拟不可中断的核心操作，GUI 仍须继续处理心跳及异步关闭。 */
#ifdef _WINDOWS
            Sleep(300);
#else
            usleep(300000);
#endif
        } else if (command->argument == 4) {
            pr_core_queue(module->core, close_from_callback, module);
        } else if (command->argument == 5) {
            char *error = NULL;
            settings_w *writer = open_settings_w("runtime-isolation", &error);
            if (!writer) { sfree(error); return PR_PLATFORM_ERROR; }
            write_setting_s(writer, "HostName", "127.0.0.1");
            write_setting_i(writer, "PortNumber", 2222);
            close_settings_w(writer);
            settings_r *reader = open_settings_r("runtime-isolation");
            char *hostname = read_setting_s(reader, "HostName");
            bool valid = hostname && !strcmp(hostname, "127.0.0.1") &&
                         read_setting_i(reader, "PortNumber", 0) == 2222;
            sfree(hostname);
            close_settings_r(reader);
            if (!valid) return PR_PLATFORM_ERROR;
            return emit(module, PR_EVENT_STATE, "storage", 7, 0);
        } else if (command->argument == 6) {
            emit(module, PR_EVENT_STATE, "delete-in-slot", 14, 0);
            emit(module, PR_EVENT_STATE, "after-delete", 12, 0);
        } else if (command->argument == 7) {
            /* 超限载荷来自不可读页；旧的先复制实现会在拒绝前出错。 */
#ifdef _WINDOWS
            void *guard = VirtualAlloc(NULL, 4096, MEM_RESERVE | MEM_COMMIT, PAGE_NOACCESS);
            if (!guard) return PR_PLATFORM_ERROR;
#else
            void *guard = mmap(NULL, 4096, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
            if (guard == MAP_FAILED) return PR_PLATFORM_ERROR;
#endif
            const char prefix[48] = {0};
            pr_status initial = emit(module, PR_EVENT_DATA, prefix, sizeof(prefix), 0);
            pr_status remaining = emit(module, PR_EVENT_DATA, guard, 32, 0);
            pr_status oversized = emit(module, PR_EVENT_DATA, guard, 128, 0);
#ifdef _WINDOWS
            VirtualFree(guard, 0, MEM_RELEASE);
#else
            munmap(guard, 4096);
#endif
            if (initial != PR_OK || remaining != PR_BACKPRESSURE || oversized != PR_BACKPRESSURE)
                return PR_PLATFORM_ERROR;
            increment(&oversize_done);
        }
        return PR_OK;
    default:
        return PR_ARGUMENT;
    }
}
static void drained(void *context)
{
    struct test_module *module = context;
    if (module->pending_size &&
        emit(module, PR_EVENT_DATA, module->pending, module->pending_size, 0) == PR_OK) {
        module->pending_size = 0;
        if (module->socket) sk_set_frozen(module->socket, false);
    }
}
static void late_timer(void *context, unsigned long now)
{
    (void)now;
    late(context);
}
static void destroy(void *context)
{
    struct test_module *module = context;
    check_thread(module);
    if (module->socket) sk_close(module->socket);
    random_unref();
    expire_timer_context(module);
    delete_callbacks_for_context(module);
    /* 故意在析构末尾排队：宿主必须在释放后再清扫一次。 */
    queue_toplevel_callback(late, module);
    schedule_timer(1, late_timer, module);
    increment(&destroyed);
    sfree(module);
}
const pr_module runtime_test_module = {create, command, destroy, drained};

#ifndef _WINDOWS
struct fd_close_probe {
    pr_core *core;
    pthread_t thread;
    int sockets[2];
    uint64_t registration;
    unsigned reads, writes;
};
static struct fd_close_probe *active_fd_probe;

static void fd_probe_ready(int fd, int flags)
{
    struct fd_close_probe *probe = active_fd_probe;
    assert(pthread_equal(probe->thread, pthread_self()));
    if (flags == SELECT_R) {
        char byte;
        assert(read(fd, &byte, 1) == 1 && byte == 'x');
        ++probe->reads;
        pr_core_begin_close(probe->core);
    } else if (flags == SELECT_W) {
        ++probe->writes;
    }
}
static int fd_probe_owner(void *context)
{
    struct fd_close_probe *probe = context;
    return pthread_equal(probe->thread, pthread_self());
}
static void fd_probe_wake(void *context) { (void)context; }
static void fd_probe_timer(void *context, unsigned long deadline)
{ (void)context; (void)deadline; }
static void fd_probe_watch(void *context, int fd, int mask, uint64_t registration)
{
    struct fd_close_probe *probe = context;
    assert(fd == probe->sockets[0]);
    if (mask) probe->registration = registration;
}
static pr_status fd_probe_event(void *context, const pr_event *event)
{ (void)context; (void)event; return PR_OK; }
static void *fd_probe_create(pr_core *core, const void *options, size_t size)
{
    struct fd_close_probe *probe = active_fd_probe;
    (void)options; (void)size;
    probe->core = core;
    uxsel_set(probe->sockets[0], SELECT_R | SELECT_W, fd_probe_ready);
    return probe;
}
static pr_status fd_probe_command(void *context, const pr_command *command)
{ (void)context; (void)command; return PR_OK; }
static void fd_probe_destroy(void *context)
{
    struct fd_close_probe *probe = context;
    uxsel_del(probe->sockets[0]);
    close(probe->sockets[0]);
    close(probe->sockets[1]);
    active_fd_probe = NULL;
}
int runtime_test_fd_close(void)
{
    struct fd_close_probe probe;
    memset(&probe, 0, sizeof(probe));
    probe.thread = pthread_self();
    assert(socketpair(AF_UNIX, SOCK_STREAM, 0, probe.sockets) == 0);
    assert(write(probe.sockets[1], "x", 1) == 1);
    active_fd_probe = &probe;
    const pr_host host = {fd_probe_owner, fd_probe_wake, fd_probe_timer,
                          fd_probe_watch, fd_probe_event};
    const pr_module module = {fd_probe_create, fd_probe_command, fd_probe_destroy, NULL};
    assert(pr_core_create(&host, &probe, 800, &module, NULL, 0, &probe.core) == PR_OK);
    struct pollfd ready = {probe.sockets[0], POLLIN | POLLOUT, 0};
    assert(poll(&ready, 1, 0) == 1);
    assert((ready.revents & (POLLIN | POLLOUT)) == (POLLIN | POLLOUT));
    /* 同一个真实就绪批次：读回调发起关闭后，写回调不得继续进入模块。 */
    pr_core_fd_ready(probe.core, probe.sockets[0], SELECT_R | SELECT_W, probe.registration);
    const bool valid = pr_core_is_closing(probe.core) && probe.reads == 1 && probe.writes == 0;
    pr_core_destroy(probe.core);
    return valid;
}
#endif
