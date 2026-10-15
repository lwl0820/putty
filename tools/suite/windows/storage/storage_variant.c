/* 仅本测试翻译单元的 Win32 边界被收窄；生产源和生产构建的语义不变。 */
#include "putty.h"
#include "storage.h"
#include <shlobj.h>
#include "seed_guard.h"

#ifdef UNICODE
#error 此测试变体必须保持上游 storage.c 的 ANSI Win32 接口
#endif
#undef HKEY_CURRENT_USER
#define HKEY_CURRENT_USER suite_storage_hkcu()
#define CreateFileA suite_seed_CreateFileA
#define ReadFile suite_seed_ReadFile
#define WriteFile suite_seed_WriteFile
#define CloseHandle suite_seed_CloseHandle
#define DeleteFileA suite_seed_DeleteFileA
/* 缺失/损坏配置在首次进入回退分支时终止，不读取个人目录候选。 */
#define load_system32_dll suite_seed_forbid_system_dll
#define GetEnvironmentVariableA suite_seed_forbid_environment
#define GetWindowsDirectoryA suite_seed_forbid_windows_directory
#include "../../../../vendor/putty/windows/storage.c"
