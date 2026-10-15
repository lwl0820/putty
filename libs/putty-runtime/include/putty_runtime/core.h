#ifndef PUTTY_RUNTIME_CORE_H
#define PUTTY_RUNTIME_CORE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct pr_core pr_core;
typedef uint64_t pr_ticket;
typedef enum pr_status {
    PR_OK, PR_ARGUMENT, PR_BUSY, PR_CLOSING, PR_STALE, PR_BACKPRESSURE,
    PR_NOT_FOUND, PR_PLATFORM_ERROR
} pr_status;
typedef enum pr_command_type {
    PR_COMMAND_OPEN, PR_COMMAND_INPUT, PR_COMMAND_RESIZE,
    PR_COMMAND_REPLY, PR_COMMAND_CLOSE_OBJECT, PR_COMMAND_CUSTOM
} pr_command_type;
typedef enum pr_event_type {
    PR_EVENT_READY, PR_EVENT_DATA, PR_EVENT_STATE, PR_EVENT_REQUEST,
    PR_EVENT_COMPLETED, PR_EVENT_CLOSED, PR_EVENT_ERROR
} pr_event_type;
typedef struct pr_command {
    uint64_t generation, object_id, request_id;
    pr_command_type type;
    const void *data;
    size_t size;
    int64_t argument;
} pr_command;
typedef struct pr_event {
    uint64_t generation, sequence, object_id, request_id;
    pr_event_type type;
    pr_status status;
    const void *data;
    size_t size;
} pr_event;

/* 所有模块入口及事件接收器均在唯一核心线程执行；输入仅在调用期间有效。 */
typedef struct pr_module {
    void *(*create)(pr_core *core, const void *options, size_t size);
    pr_status (*command)(void *module, const pr_command *command);
    void (*destroy)(void *module);
    void (*drained)(void *module);
} pr_module;
typedef struct pr_host {
    int (*is_owner)(void *context);
    void (*wake)(void *context);
    void (*timer)(void *context, unsigned long deadline);
    void (*watch_fd)(void *context, int fd, int mask, uint64_t registration);
    pr_status (*event)(void *context, const pr_event *event);
} pr_host;
typedef void (*pr_callback)(void *context);

/* create/destroy 与其它入口必须同线程；进程内只允许一个活动核心。 */
pr_status pr_core_create(const pr_host *host, void *context,
                        uint64_t generation, const pr_module *module,
                        const void *options, size_t size, pr_core **result);
pr_status pr_core_command(pr_core *core, const pr_command *command);
pr_status pr_core_emit(pr_core *core, const pr_event *event);
pr_ticket pr_core_queue(pr_core *core, pr_callback callback, void *context);
pr_ticket pr_core_schedule(pr_core *core, int milliseconds,
                           pr_callback callback, void *context);
pr_status pr_core_cancel(pr_core *core, pr_ticket ticket);
/* 一次性请求：先登记，再发 REQUEST；回复/关闭后不会再次调用完成器。 */
uint64_t pr_core_request(pr_core *core, uint64_t object_id,
                         pr_callback cancelled, void *context);
void pr_core_forget_request(pr_core *core, uint64_t request_id);
int pr_core_callbacks(pr_core *core, unsigned budget);
void pr_core_timers(pr_core *core);
void pr_core_event_capacity(pr_core *core);
unsigned long pr_core_ticks(void);
int pr_core_timer_delay(unsigned long deadline, unsigned long now);
void pr_core_fd_ready(pr_core *core, int fd, int mask, uint64_t registration);
typedef struct pr_handle_watch {
    uintptr_t handle;
    /* 来自上游等待注册的不可复用身份；旧激活返回 PR_STALE。 */
    uint64_t registration;
} pr_handle_watch;
size_t pr_core_handles(pr_core *core, pr_handle_watch *handles, size_t capacity);
pr_status pr_core_handle_ready(pr_core *core, pr_handle_watch watch);
/* begin_close 可在回调中使用；destroy 只在顶层安全点使用，禁止从模块回调中调用。
 * destroy 释放模块后再次清扫回调和桥消息，运行时 Qt 门面会异步安排此安全点。 */
int pr_core_is_closing(pr_core *core);
void pr_core_begin_close(pr_core *core);
void pr_core_destroy(pr_core *core);

#ifdef __cplusplus
}
#endif
#endif
