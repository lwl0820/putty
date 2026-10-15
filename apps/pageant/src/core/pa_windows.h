#ifndef PAGEANT_WINDOWS_H
#define PAGEANT_WINDOWS_H
#include <stddef.h>
#include <stdbool.h>
#ifdef __cplusplus
extern "C" {
#endif
/* 兼容窗口只搬运字节；回调由唯一核心线程的 HANDLE 等待触发。 */
typedef struct pa_windows_ipc pa_windows_ipc;
typedef void (*pa_windows_request)(void *, const void *, size_t);
pa_windows_ipc *pa_windows_ipc_start(pa_windows_request request, void *context);
void pa_windows_ipc_reply(pa_windows_ipc *ipc, const void *data, size_t size);
void pa_windows_ipc_stop(pa_windows_ipc *ipc);
char *pa_windows_pipe_name(void);
const char *pa_windows_class_name(void);
void pa_windows_prepare(void);
#ifdef PA_WINDOWS_TEST_NAMESPACE
/* 仅测试编译变体存在；必须在创建线程前设置一次，不接受生产环境变量。 */
bool pa_windows_set_test_scope(const char *scope);
#endif
/* 与原版共享启动锁；在任何核心线程建立之前调用，在两条入口就绪后释放。 */
void *pa_windows_startup_lock(bool *exists, char **error);
void pa_windows_startup_unlock(void *lock);
void pa_windows_free(void *data);
/* 二次启动的私有同步传输失败后，不把空响应当作成功的空密钥库。 */
bool pa_windows_query_failed(void);
#ifdef __cplusplus
}
#endif
#endif
