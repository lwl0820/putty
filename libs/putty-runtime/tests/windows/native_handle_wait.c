/* 原生事件及上游等待注册测试，不初始化网络、存储或随机源。 */
#include "putty.h"
#include <stdarg.h>
#include <stdio.h>

#ifdef NDEBUG
#error Windows 原生等待测试必须保留 Release 断言
#endif

#define CHECK(value) do { if (!(value)) { \
    fprintf(stderr, "原生等待断言失败：%s，行 %d\n", #value, __LINE__); \
    exit(1); } } while (0)

void modalfatalbox(const char *format, ...)
{
    va_list args;
    va_start(args, format);
    vfprintf(stderr, format, args);
    va_end(args);
    abort();
}

static void count(void *context) { ++*(unsigned *)context; }
struct cancellation { HandleWait *wait; unsigned calls; };
static void cancel_self(void *context)
{
    struct cancellation *state = context;
    ++state->calls;
    delete_handle_wait(state->wait);
    state->wait = NULL;
}

static void reset_semantics(BOOL manual)
{
    unsigned calls = 0;
    HANDLE event = CreateEventW(NULL, manual, FALSE, NULL);
    CHECK(event != NULL);
    HandleWait *wait = add_handle_wait(event, count, &calls);
    HandleWaitList *snapshot = get_handle_wait_list();
    CHECK(snapshot->nhandles == 1);
    CHECK(WaitForSingleObject(event, 0) == WAIT_TIMEOUT);
    CHECK(SetEvent(event));
    CHECK(WaitForSingleObject(event, 1000) == WAIT_OBJECT_0);
    handle_wait_activate(snapshot, 0);
    CHECK(calls == 1);
    CHECK(WaitForSingleObject(event, 0) == (manual ? WAIT_OBJECT_0 : WAIT_TIMEOUT));
    CHECK(ResetEvent(event));
    CHECK(WaitForSingleObject(event, 0) == WAIT_TIMEOUT);
    CHECK(SetEvent(event));
    CHECK(WaitForSingleObject(event, 1000) == WAIT_OBJECT_0);
    handle_wait_activate(snapshot, 0);
    CHECK(calls == 2);
    delete_handle_wait(wait);
    handle_wait_list_free(snapshot);
    CHECK(CloseHandle(event));
}

static void stale_activation(void)
{
    unsigned old_calls = 0, new_calls = 0;
    HANDLE event = CreateEventW(NULL, FALSE, FALSE, NULL);
    CHECK(event != NULL);
    HandleWait *old = add_handle_wait(event, count, &old_calls);
    HandleWaitList *queued = get_handle_wait_list();
    CHECK(queued->nhandles == 1);
    CHECK(SetEvent(event));
    CHECK(WaitForSingleObject(event, 1000) == WAIT_OBJECT_0);
    /* 等待已完成，但回调尚未交付：撤销后以完全相同的真实 HANDLE 重建。 */
    delete_handle_wait(old);
    HandleWait *current = add_handle_wait(event, count, &new_calls);
    HandleWaitList *fresh = get_handle_wait_list();
    CHECK(fresh->handles[0] == queued->handles[0]);
    CHECK(fresh->registrations[0] != queued->registrations[0]);
    handle_wait_activate(queued, 0);
    CHECK(old_calls == 0 && new_calls == 0);
    CHECK(SetEvent(event));
    CHECK(WaitForSingleObject(event, 1000) == WAIT_OBJECT_0);
    handle_wait_activate(fresh, 0);
    CHECK(new_calls == 1);
    delete_handle_wait(current);
    handle_wait_activate(fresh, 0);
    CHECK(new_calls == 1);
    handle_wait_list_free(fresh);
    handle_wait_list_free(queued);
    CHECK(CloseHandle(event));

    struct cancellation state = {NULL, 0};
    event = CreateEventW(NULL, TRUE, TRUE, NULL);
    CHECK(event != NULL);
    state.wait = add_handle_wait(event, cancel_self, &state);
    queued = get_handle_wait_list();
    CHECK(WaitForSingleObject(event, 1000) == WAIT_OBJECT_0);
    handle_wait_activate(queued, 0);
    handle_wait_activate(queued, 0);
    CHECK(state.calls == 1 && state.wait == NULL);
    handle_wait_list_free(queued);
    CHECK(CloseHandle(event));
}

int main(void)
{
    reset_semantics(TRUE);
    reset_semantics(FALSE);
    stale_activation();
    HandleWaitList *empty = get_handle_wait_list();
    CHECK(empty->nhandles == 0);
    handle_wait_list_free(empty);
    puts("{\"schema\":1,\"kind\":\"handles\",\"passed\":true,"
         "\"checks\":[\"manual-reset\",\"auto-reset\",\"queued-same-value\",\"self-cancel\"],"
         "\"storage\":\"not-run\"}");
    return 0;
}
