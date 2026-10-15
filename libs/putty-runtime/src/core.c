#include "putty_runtime/core.h"
#include "putty.h"
#include "tree234.h"
#include <limits.h>
#include <stdarg.h>
#ifndef _WINDOWS
#include <poll.h>
#endif

struct pr_work {
    struct pr_work *next;
    pr_core *core;
    pr_ticket ticket;
    pr_callback callback;
    void *context;
    bool timer, cancelled;
};
struct pr_request {
    struct pr_request *next;
    uint64_t id, object;
    pr_callback cancel;
    void *context;
};
struct pr_core {
    pr_host host;
    void *host_context, *module_context;
    pr_module module;
    uint64_t generation, sequence, ticket, request;
    struct pr_work *work;
    struct pr_request *requests;
    bool closing;
    unsigned dispatch_depth;
#ifdef _WINDOWS
    HWND hwnd;
#endif
};
static pr_core *active;

void modalfatalbox(const char *format, ...)
{
    va_list args;
    va_start(args, format);
    vfprintf(stderr, format, args);
    va_end(args);
    fputc('\n', stderr);
    abort();
}
void nonfatal(const char *format, ...)
{
    va_list args;
    va_start(args, format);
    char *message = dupvprintf(format, args);
    va_end(args);
    if (active && !active->closing) {
        pr_event event;
        memset(&event, 0, sizeof(event));
        event.type = PR_EVENT_ERROR;
        event.status = PR_PLATFORM_ERROR;
        event.data = message;
        event.size = strlen(message);
        pr_core_emit(active, &event);
    }
    fprintf(stderr, "%s\n", message);
    sfree(message);
}
#ifndef _WINDOWS
/* 与上游 main-gtk-application 一致，不从 Xresources 覆盖会话默认值。 */
char *x_get_default(const char *key) { (void)key; return NULL; }
#endif
#ifdef _WINDOWS
static volatile LONG locked;
#else
static volatile int locked;
static bool uxsel_started, network_started;
struct uxsel_id { int fd, mask; uint64_t registration; struct uxsel_id *next; };
static uxsel_id *fd_ids;
static uint64_t fd_registration;
#endif

static void owner(pr_core *core)
{
    assert(core && active == core && core->host.is_owner(core->host_context));
}
static void notify(void *context)
{
    pr_core *core = context;
    core->host.wake(core->host_context);
}
void timer_change_notify(unsigned long next)
{
    if (active)
        active->host.timer(active->host_context, next);
}
#ifndef _WINDOWS
uxsel_id *uxsel_input_add(int fd, int mask)
{
    uxsel_id *id = snew(uxsel_id);
    id->fd = fd;
    id->mask = mask;
    id->next = fd_ids;
    fd_ids = id;
    id->registration = ++fd_registration;
    active->host.watch_fd(active->host_context, fd, mask, id->registration);
    return id;
}
void uxsel_input_remove(uxsel_id *id)
{
    active->host.watch_fd(active->host_context, id->fd, 0, id->registration);
    uxsel_id **link = &fd_ids;
    while (*link && *link != id) link = &(*link)->next;
    assert(*link == id);
    *link = id->next;
    sfree(id);
}
static bool current_fd(int fd, uint64_t registration, int event)
{
    uxsel_id *id;
    for (id = fd_ids; id; id = id->next)
        if (id->fd == fd && id->registration == registration && (id->mask & event)) return true;
    return false;
}
#else
static LRESULT CALLBACK window_proc(HWND hwnd, UINT msg, WPARAM wp, LPARAM lp)
{
    if (msg == WM_NETEVENT || msg == WM_DONE_WITH_SOCKET) {
        winselgui_response(msg, wp, lp);
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wp, lp);
}
#endif

pr_status pr_core_create(const pr_host *host, void *context,
                        uint64_t generation, const pr_module *module,
                        const void *options, size_t size, pr_core **result)
{
    pr_core *core;
    if ((size && !options) || !result || !host || !host->is_owner || !host->wake || !host->timer ||
        !host->event || !module || !module->create || !module->command ||
        !module->destroy || !host->is_owner(context))
        return PR_ARGUMENT;
    *result = NULL;
#ifdef _WINDOWS
    if (InterlockedCompareExchange(&locked, 1, 0))
        return PR_BUSY;
#else
    if (!host->watch_fd)
        return PR_ARGUMENT;
    if (!__sync_bool_compare_and_swap(&locked, 0, 1))
        return PR_BUSY;
#endif
    core = snew(pr_core);
    memset(core, 0, sizeof(*core));
    core->host = *host;
    core->host_context = context;
    core->generation = generation;
    core->module = *module;
    active = core;
    request_callback_notifications(notify, core);
#ifdef _WINDOWS
    WNDCLASSW cls;
    memset(&cls, 0, sizeof(cls));
    cls.lpfnWndProc = window_proc;
    cls.hInstance = GetModuleHandleW(NULL);
    cls.lpszClassName = L"PuTTYSuiteNetworkBridge";
    RegisterClassW(&cls);
    core->hwnd = CreateWindowExW(0, cls.lpszClassName, L"", 0, 0, 0, 0, 0,
                                HWND_MESSAGE, NULL, cls.hInstance, NULL);
    if (!core->hwnd) {
        request_callback_notifications(NULL, NULL);
        active = NULL;
        sfree(core);
        InterlockedExchange(&locked, 0);
        return PR_PLATFORM_ERROR;
    }
    winselgui_set_hwnd(core->hwnd);
#else
    if (!uxsel_started) {
        uxsel_init();
        uxsel_started = true;
    }
#endif
#ifdef _WINDOWS
    sk_init();
#else
    if (!network_started) { sk_init(); network_started = true; }
#endif
    core->module_context = module->create(core, options, size);
    if (!core->module_context) {
        pr_core_destroy(core);
        return PR_PLATFORM_ERROR;
    }
    *result = core;
    return PR_OK;
}

pr_status pr_core_emit(pr_core *core, const pr_event *event)
{
    pr_event copy;
    owner(core);
    if (!event || (event->size && !event->data))
        return PR_ARGUMENT;
    if (core->closing)
        return PR_CLOSING;
    copy = *event;
    copy.generation = core->generation;
    copy.sequence = ++core->sequence;
    return core->host.event(core->host_context, &copy);
}

uint64_t pr_core_request(pr_core *core, uint64_t object_id,
                         pr_callback cancelled, void *context)
{
    struct pr_request *request;
    owner(core);

    if (core->closing)
        return 0;
    unsigned count = 0;
    for (request = core->requests; request; request = request->next)
        if (++count >= 128) return 0;
    request = snew(struct pr_request);
    request->id = ++core->request;
    request->object = object_id;
    request->cancel = cancelled;
    request->context = context;
    request->next = core->requests;
    core->requests = request;
    return request->id;
}
static struct pr_request *take_request(pr_core *core, uint64_t id)
{
    struct pr_request **link = &core->requests, *request;
    while ((request = *link) != NULL) {
        if (request->id == id) {
            *link = request->next;
            return request;
        }
        link = &request->next;
    }
    return NULL;
}
void pr_core_forget_request(pr_core *core, uint64_t id)
{
    owner(core);
    sfree(take_request(core, id));
}
pr_status pr_core_command(pr_core *core, const pr_command *command)
{
    owner(core);
    if (!command || (command->size && !command->data))
        return PR_ARGUMENT;
    if (core->closing)
        return PR_CLOSING;
    if (command->generation != core->generation)
        return PR_STALE;
    if (command->type == PR_COMMAND_REPLY) {
        struct pr_request *request;
        for (request = core->requests; request; request = request->next)
            if (request->id == command->request_id) break;
        if (!request || request->object != command->object_id)
            return PR_STALE;
        request = take_request(core, command->request_id);
        sfree(request);
    }
    core->dispatch_depth++;
    pr_status status = core->module.command(core->module_context, command);
    core->dispatch_depth--;
    return status;
}
static void unlink_work(struct pr_work *work)
{
    struct pr_work **link = &work->core->work;
    while (*link && *link != work)
        link = &(*link)->next;
    assert(*link == work);
    *link = work->next;
}
static void run_work(void *context)
{
    struct pr_work *work = context;
    pr_callback callback = work->callback;
    void *argument = work->context;
    bool run = !work->cancelled && !work->core->closing;
    unlink_work(work);
    sfree(work);
    if (run)
        callback(argument);
}
static void timer_work(void *context, unsigned long now)
{
    (void)now;
    run_work(context);
}
static pr_ticket add_work(pr_core *core, int milliseconds,
                          pr_callback callback, void *context, bool timer)
{
    struct pr_work *work;
    owner(core);
    if (core->closing || !callback || milliseconds < 0)
        return 0;
    unsigned count = 0;
    for (work = core->work; work; work = work->next)
        if (++count >= 4096) return 0;
    work = snew(struct pr_work);
    work->core = core;
    work->ticket = ++core->ticket;
    work->callback = callback;
    work->context = context;
    work->timer = timer;
    work->cancelled = false;
    work->next = core->work;
    core->work = work;
    if (timer)
        schedule_timer(milliseconds, timer_work, work);
    else
        queue_toplevel_callback(run_work, work);
    return work->ticket;
}
pr_ticket pr_core_queue(pr_core *core, pr_callback callback, void *context)
{
    return add_work(core, 0, callback, context, false);
}
pr_ticket pr_core_schedule(pr_core *core, int ms, pr_callback callback, void *ctx)
{
    return add_work(core, ms, callback, ctx, true);
}
pr_status pr_core_cancel(pr_core *core, pr_ticket ticket)
{
    struct pr_work *work;
    owner(core);
    for (work = core->work; work; work = work->next) {
        if (work->ticket == ticket) {
            work->cancelled = true;
            /* 计时器保留到原期限再释放包装器；撤销后不执行业务回调。 */
            if (!work->timer) {
                delete_callbacks_for_context(work);
                unlink_work(work);
                sfree(work);
            }
            return PR_OK;
        }
    }
    return PR_NOT_FOUND;
}
int pr_core_callbacks(pr_core *core, unsigned budget)
{
    owner(core);
    core->dispatch_depth++;
    while (budget-- && !core->closing && run_toplevel_callbacks()) {}
    core->dispatch_depth--;
    return !core->closing && toplevel_callback_pending();
}
void pr_core_event_capacity(pr_core *core)
{
    owner(core);
    if (!core->closing && core->module.drained) {
        core->dispatch_depth++;
        core->module.drained(core->module_context);
        core->dispatch_depth--;
    }
}
void pr_core_timers(pr_core *core)
{
    unsigned long next;
    owner(core);
    core->dispatch_depth++;
    if (!core->closing && run_timers(GETTICKCOUNT(), &next))
        timer_change_notify(next);
    core->dispatch_depth--;
}
unsigned long pr_core_ticks(void) { return GETTICKCOUNT(); }
int pr_core_timer_delay(unsigned long deadline, unsigned long now)
{
    /* 与上游相同宽度的有符号差值，包含 Windows 32 位 tick 回绕。 */
    long delay = (long)(deadline - now);
    return delay <= 0 ? 0 : delay > INT_MAX ? INT_MAX : (int)delay;
}
void pr_core_fd_ready(pr_core *core, int fd, int mask, uint64_t registration)
{
    owner(core);
    core->dispatch_depth++;
#ifndef _WINDOWS
    if (!core->closing) {
        /* 读通知先抵达时仍探测 OOB，保持上游异常、读、写顺序。 */
        struct pollfd pfd = {fd, POLLIN | POLLOUT | POLLPRI, 0};
        if (poll(&pfd, 1, 0) > 0 && (pfd.revents & POLLPRI)) mask |= SELECT_X;
        /* 前一项可发起关闭或重配注册；后续项必须重新核对全部状态。 */
        if (!core->closing && (mask & SELECT_X) && current_fd(fd, registration, SELECT_X)) select_result(fd, SELECT_X);
        if (!core->closing && (mask & SELECT_R) && current_fd(fd, registration, SELECT_R)) select_result(fd, SELECT_R);
        if (!core->closing && (mask & SELECT_W) && current_fd(fd, registration, SELECT_W)) select_result(fd, SELECT_W);
    }
#else
    (void)fd; (void)mask; (void)registration;
#endif
    core->dispatch_depth--;
}
size_t pr_core_handles(pr_core *core, pr_handle_watch *handles, size_t capacity)
{
    owner(core);
#ifdef _WINDOWS
    HandleWaitList *list = get_handle_wait_list();
    size_t count = list->nhandles;
    for (size_t i = 0; i < count && i < capacity; ++i) {
        handles[i].handle = (uintptr_t)list->handles[i];
        handles[i].registration = list->registrations[i];
    }
    handle_wait_list_free(list);
    return count;
#else
    (void)handles; (void)capacity;
    return 0;
#endif
}
pr_status pr_core_handle_ready(pr_core *core, pr_handle_watch watch)
{
    owner(core);
    if (core->closing) return PR_CLOSING;
    pr_status status = PR_STALE;
#ifdef _WINDOWS
    HandleWaitList *list = get_handle_wait_list();
    for (size_t i = 0; i < list->nhandles; ++i) {
        if ((uintptr_t)list->handles[i] == watch.handle &&
            list->registrations[i] == watch.registration) {
            core->dispatch_depth++;
            handle_wait_activate(list, (int)i);
            core->dispatch_depth--;
            status = PR_OK;
            break;
        }
    }
    handle_wait_list_free(list);
#else
    (void)watch;
#endif
    return status;
}
int pr_core_is_closing(pr_core *core)
{
    owner(core);
    return core->closing;
}
void pr_core_begin_close(pr_core *core)
{
    struct pr_request *request;
    owner(core);
    core->closing = true;
    while ((request = core->requests) != NULL) {
        core->requests = request->next;
        if (request->cancel)
            request->cancel(request->context);
        sfree(request);
    }
}
void pr_core_destroy(pr_core *core)
{
    struct pr_work *work;
    unsigned long unused;
    owner(core);
    assert(core->dispatch_depth == 0);
    pr_core_begin_close(core);
    /* 模块析构可新增上游回调，最终清扫必须在析构之后。 */
    if (core->module_context) {
        core->module.destroy(core->module_context);
        delete_callbacks_for_context(core->module_context);
        expire_timer_context(core->module_context);
    }
    while ((work = core->work) != NULL) {
        core->work = work->next;
        delete_callbacks_for_context(work);
        expire_timer_context(work);
        sfree(work);
    }
    /* 清除失效计时器后再允许新运行时复用这些地址。 */
    run_timers(GETTICKCOUNT(), &unused);
    sk_cleanup();
#ifndef _WINDOWS
    /* 全部网络对象应已由模块释放；残留 fd 不得跨运行时代次。 */
    assert(fd_ids == NULL);
#endif
#ifdef _WINDOWS
    MSG message;
    while (PeekMessageW(&message, core->hwnd, WM_NETEVENT,
                         WM_DONE_WITH_SOCKET, PM_REMOVE)) {
        if (message.message == WM_DONE_WITH_SOCKET)
            winselgui_response(message.message, message.wParam, message.lParam);
    }
    /* 关闭 socket 产生的 WM_DONE_WITH_SOCKET 已处理，清掉 HWND 的迟到消息。 */
    delete_callbacks_for_context(core);
    winselgui_clear_hwnd();
    DestroyWindow(core->hwnd);
#endif
    delete_callbacks_for_context(core);
    request_callback_notifications(NULL, NULL);
    active = NULL;
    sfree(core);
#ifdef _WINDOWS
    InterlockedExchange(&locked, 0);
#else
    __sync_lock_release(&locked);
#endif
}
