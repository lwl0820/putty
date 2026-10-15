#include "putty.h"
#include <stdio.h>
#include <stdarg.h>

/* tree234 使用真实上游分配器，其不可恢复错误仍立即终止测试。 */
void modalfatalbox(const char *format, ...)
{
    va_list args;
    va_start(args, format);
    vfprintf(stderr, format, args);
    va_end(args);
    abort();
}
static void count_callback(void *context) { ++*(unsigned *)context; }
struct self_cancel {
    unsigned calls;
    HandleWait *wait;
};
static void cancel_callback(void *context)
{
    struct self_cancel *self = context;
    ++self->calls;
    delete_handle_wait(self->wait);
    self->wait = NULL;
}
static int find_handle(const HandleWaitList *list, HANDLE handle)
{
    for (int i = 0; i < list->nhandles; ++i)
        if (list->handles[i] == handle) return i;
    abort();
}
int main(void)
{
    unsigned old_calls = 0, new_calls = 0, other_calls = 0;
    HandleWait *old = add_handle_wait((HANDLE)37, count_callback, &old_calls);
    HandleWaitList *old_snapshot = get_handle_wait_list();
    assert(old_snapshot->nhandles == 1);
    const uint64_t old_registration = old_snapshot->registrations[0];
    assert(old_registration != 0);
    handle_wait_activate(old_snapshot, 0);
    assert(old_calls == 1);
    delete_handle_wait(old);
    /* 已排队的旧快照在撤销后激活，不能解引用旧对象。 */
    handle_wait_activate(old_snapshot, 0);
    assert(old_calls == 1);

    HandleWait *current = add_handle_wait((HANDLE)37, count_callback, &new_calls);
    HandleWaitList *current_snapshot = get_handle_wait_list();
    assert(current_snapshot->registrations[0] > old_registration);
    /* 同值 HANDLE 和树索引都复用，旧激活仍不能交给新等待项。 */
    handle_wait_activate(old_snapshot, 0);
    assert(old_calls == 1 && new_calls == 0);
    handle_wait_activate(current_snapshot, 0);
    assert(new_calls == 1);

    HandleWait *other = add_handle_wait((HANDLE)58, count_callback, &other_calls);
    HandleWaitList *two_snapshot = get_handle_wait_list();
    const int old_index = find_handle(two_snapshot, (HANDLE)37);
    const int other_index = find_handle(two_snapshot, (HANDLE)58);
    delete_handle_wait(current);
    current = add_handle_wait((HANDLE)93, count_callback, &new_calls);
    handle_wait_activate(two_snapshot, old_index);
    assert(new_calls == 1);
    /* 一个注册改变，不影响同快照中的其它仍有效等待项。 */
    handle_wait_activate(two_snapshot, other_index);
    assert(other_calls == 1);
    delete_handle_wait(current);
    delete_handle_wait(other);

    struct self_cancel self = {0, NULL};
    self.wait = add_handle_wait((HANDLE)37, cancel_callback, &self);
    HandleWaitList *self_snapshot = get_handle_wait_list();
    handle_wait_activate(self_snapshot, 0);
    assert(self.calls == 1 && self.wait == NULL);
    handle_wait_activate(self_snapshot, 0);
    assert(self.calls == 1);
    HandleWaitList *empty = get_handle_wait_list();
    assert(empty->nhandles == 0);
    handle_wait_list_free(empty);
    handle_wait_list_free(self_snapshot);
    handle_wait_list_free(two_snapshot);
    handle_wait_list_free(current_snapshot);
    handle_wait_list_free(old_snapshot);
    puts("实际 Windows 等待算法：普通激活、撤销、同值复用、旧快照和回调中注销通过");
    return 0;
}
