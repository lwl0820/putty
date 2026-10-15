/* 创建失败及其后的核心复用使用真实 callback/timing 实现，不创建外部资源。 */
#include "putty.h"
#include "putty_runtime/core.h"

#ifdef NDEBUG
#error 运行时创建失败测试必须保留 Release 断言
#endif

struct test_state {
    unsigned created, destroyed, cancelled, callbacks, timers, wakes, timer_changes;
    bool fail;
};
static struct test_state state;
static unsigned global_callbacks;
static const pr_host host;
static const pr_module module;

static int is_owner(void *context) { return context == &state; }
static void wake(void *context)
{
    assert(context == &state);
    state.wakes++;
}
static void timer(void *context, unsigned long deadline)
{
    (void)deadline;
    assert(context == &state);
    state.timer_changes++;
}
static void watch(void *context, int fd, int mask, uint64_t registration)
{
    (void)context; (void)fd; (void)mask; (void)registration;
    /* 此宿主不建立任何 socket 或 fd 注册。 */
    abort();
}
static pr_status event(void *context, const pr_event *value)
{
    (void)context; (void)value;
    abort();
    return PR_PLATFORM_ERROR;
}
static const pr_host host = {is_owner, wake, timer, watch, event};

static void unexpected(void *context)
{
    (void)context;
    abort();
}
static void cancelled(void *context)
{
    assert(context == &state);
    state.cancelled++;
}
static void global_callback(void *context)
{
    assert(context == NULL);
    global_callbacks++;
}
static void callback(void *context)
{
    assert(context == &state);
    state.callbacks++;
}
static void timed(void *context, unsigned long deadline)
{
    (void)deadline;
    assert(context == &state);
    state.timers++;
}
static void unexpected_timer(void *context, unsigned long deadline)
{
    (void)deadline;
    unexpected(context);
}

static void *create(pr_core *core, const void *options, size_t size)
{
    pr_core *nested = core;
    assert(options == NULL && size == 0);
    state.created++;
    /* 创建中仍持有唯一核心锁；失败返回后必须能再次取得该锁。 */
    assert(pr_core_create(&host, &state, state.created, &module,
                          NULL, 0, &nested) == PR_BUSY);
    assert(nested == NULL);
    assert(pr_core_request(core, 1, cancelled, &state) != 0);
    assert(pr_core_queue(core, unexpected, &state) != 0);
    assert(pr_core_schedule(core, 60000, unexpected, &state) != 0);
    /* 核心级回调也必须在创建失败后撤销。 */
    queue_toplevel_callback(unexpected, core);
    if (state.fail)
        return NULL;
    queue_toplevel_callback(callback, &state);
    schedule_timer(60000, timed, &state);
    return &state;
}
static pr_status command(void *context, const pr_command *value)
{
    assert(context == &state && value != NULL);
    return PR_OK;
}
static void destroy(void *context)
{
    assert(context == &state && !state.fail);
    state.destroyed++;
    /* 正常析构中新排入的模块级回调和计时器仍须最终清扫。 */
    queue_toplevel_callback(unexpected, context);
    schedule_timer(60000, unexpected_timer, context);
}
static const pr_module module = {create, command, destroy, NULL};

static void assert_empty(void)
{
    unsigned long next;
    assert(!run_timers(GETTICKCOUNT(), &next));
    assert(!toplevel_callback_pending());
    assert(!run_toplevel_callbacks());
}
static void fail_create(unsigned expected)
{
    pr_core *core = (pr_core *)&state;
    state.fail = true;
    /* NULL 是上游全局回调的合法上下文，不属于创建失败的模块。 */
    queue_toplevel_callback(global_callback, NULL);
    assert(pr_core_create(&host, &state, expected, &module,
                          NULL, 0, &core) == PR_PLATFORM_ERROR);
    assert(core == NULL);
    assert(state.created == expected && state.cancelled == expected);
    assert(state.destroyed == (expected - 1) / 2);
    assert(run_toplevel_callbacks());
    assert(global_callbacks == (expected + 1) / 2);
    assert_empty();
}
static void successful_create(unsigned expected)
{
    pr_core *core = NULL;
    state.fail = false;
    assert(pr_core_create(&host, &state, expected, &module,
                          NULL, 0, &core) == PR_OK);
    assert(core != NULL && state.created == expected);
    /* 初始计时器真实参与上游调度，随后销毁仍取消其余长计时器。 */
    unsigned long next;
    assert(run_timers(GETTICKCOUNT(), &next));
    pr_core_begin_close(core);
    pr_core_destroy(core);
    assert(state.destroyed == expected / 2 && state.cancelled == expected);
    assert(state.callbacks == 0 && state.timers == 0);
    assert_empty();
}

int main(void)
{
    fail_create(1);
    successful_create(2);
    fail_create(3);
    successful_create(4);
    assert(state.wakes >= 2 && state.timer_changes >= 4);
    puts("运行时模块创建失败与核心复用检查通过：4 次创建、2 次析构");
    return 0;
}
