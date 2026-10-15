#ifndef SUITE_STORAGE_SEED_GUARD_H
#define SUITE_STORAGE_SEED_GUARD_H
#include "putty.h"
#include "private_hive.h"

/* 拒绝码必须由独立子进程实际触发；调用方不能把拒绝转换成回退。 */
enum suite_seed_rejection {
    SUITE_SEED_PATH = 80, SUITE_SEED_OPEN = 81, SUITE_SEED_FALLBACK = 82,
    SUITE_SEED_IO = 83, SUITE_SEED_PERMISSION = 84, SUITE_SEED_UNBOUND = 85
};
typedef struct suite_seed_statistics {
    unsigned reads, writes, opens_read, opens_write, registry_accesses;
    unsigned long long bytes_read, bytes_written;
} suite_seed_statistics;

DWORD suite_seed_guard_bind(suite_private_hive *context);
void suite_seed_guard_unbind(void);
suite_seed_statistics suite_seed_guard_statistics(void);
HKEY suite_storage_hkcu(void);
HANDLE WINAPI suite_seed_CreateFileA(LPCSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES, DWORD, DWORD, HANDLE);
BOOL WINAPI suite_seed_ReadFile(HANDLE, LPVOID, DWORD, LPDWORD, LPOVERLAPPED);
BOOL WINAPI suite_seed_WriteFile(HANDLE, LPCVOID, DWORD, LPDWORD, LPOVERLAPPED);
BOOL WINAPI suite_seed_CloseHandle(HANDLE);
BOOL WINAPI suite_seed_DeleteFileA(LPCSTR);
HMODULE suite_seed_forbid_system_dll(const char *);
DWORD WINAPI suite_seed_forbid_environment(LPCSTR, LPSTR, DWORD);
UINT WINAPI suite_seed_forbid_windows_directory(LPSTR, UINT);
#endif
