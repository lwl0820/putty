#ifndef SUITE_PRIVATE_HIVE_H
#define SUITE_PRIVATE_HIVE_H
#include <windows.h>

#ifdef __cplusplus
extern "C" {
#endif
typedef struct suite_private_hive suite_private_hive;

/* 仅测试隔离接口。成功后 HKCU 只在本进程映射；一进程至多一个活动映射。
 * 必须在任何产品存储、Qt 偏好或 PRNG 初始化之前调用并检查返回值。 */
DWORD suite_hive_create(const wchar_t *project_tmp, const wchar_t *parent,
                        suite_private_hive **result);
DWORD suite_hive_open(const wchar_t *project_tmp, const wchar_t *root,
                      suite_private_hive **result);
DWORD suite_hive_pause(suite_private_hive *context);
DWORD suite_hive_resume(suite_private_hive *context);
DWORD suite_hive_close(suite_private_hive *context);
/* close 保留文件供另一个显式初始化的进程加载；cleanup 只删除验证过的私有运行根。
 * 本进程存在活动映射时，cleanup 在删除任何文件之前返回 ERROR_BUSY。 */
DWORD suite_hive_cleanup(const wchar_t *project_tmp, const wchar_t *root);

int suite_hive_ready(const suite_private_hive *context);
const wchar_t *suite_hive_root(const suite_private_hive *context);
const wchar_t *suite_hive_seed(const suite_private_hive *context);
/* 借用句柄不得关闭；调用方打开的全部子键须在 pause/close 前关闭。 */
HKEY suite_hive_key(const suite_private_hive *context);
#ifdef __cplusplus
}
#endif
#endif
