/* 只允许本次 hive 预建的 seed；任何失败都退出测试子进程，绝不继续回退。 */
#include "seed_guard.h"
#include <aclapi.h>
#include <wchar.h>

#define PATH_CAP 1024
static suite_private_hive *active;
static HANDLE seed_handle = INVALID_HANDLE_VALUE;
static int seed_writing;
static DWORD owner_thread;
static wchar_t seed_path[PATH_CAP];
static BYTE user_information[1024], system_sid[SECURITY_MAX_SID_SIZE];
static PSID user_sid;
static suite_seed_statistics statistics;

static __declspec(noreturn) void reject(unsigned code)
{
    /* 只输出拒绝码，不输出路径、SID、种子或随机字节。 */
    fprintf(stderr, "种子隔离拒绝，代码 %u\n", code);
    ExitProcess(code);
}
static void require_ready(void)
{
    if (!active || owner_thread != GetCurrentThreadId() || !suite_hive_ready(active))
        reject(SUITE_SEED_UNBOUND);
}
static DWORD check_handle(HANDLE file)
{
    BY_HANDLE_FILE_INFORMATION information;
    wchar_t final_path[PATH_CAP + 4];
    if (!GetFileInformationByHandle(file, &information)) return GetLastError();
    if (GetFileType(file) != FILE_TYPE_DISK || information.nNumberOfLinks != 1 ||
        information.dwFileAttributes & (FILE_ATTRIBUTE_REPARSE_POINT | FILE_ATTRIBUTE_DIRECTORY))
        return ERROR_ACCESS_DENIED;
    DWORD length = GetFinalPathNameByHandleW(file, final_path, PATH_CAP + 4,
                                            FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
    if (!length || length >= PATH_CAP + 4 || wcsncmp(final_path, L"\\\\?\\", 4) ||
        _wcsicmp(final_path + 4, seed_path)) return ERROR_BAD_PATHNAME;
    PSID owner = NULL;
    PACL acl = NULL;
    PSECURITY_DESCRIPTOR descriptor = NULL;
    DWORD error = GetSecurityInfo(file, SE_FILE_OBJECT,
                                  OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                                  &owner, NULL, &acl, NULL, &descriptor);
    if (error) return error;
    SECURITY_DESCRIPTOR_CONTROL control;
    DWORD revision;
    unsigned found = 0;
    if (!owner || !EqualSid(owner, user_sid) || !acl || acl->AceCount != 2 ||
        !GetSecurityDescriptorControl(descriptor, &control, &revision) || !(control & SE_DACL_PROTECTED))
        error = ERROR_ACCESS_DENIED;
    for (DWORD i = 0; !error && i < acl->AceCount; ++i) {
        ACCESS_ALLOWED_ACE *ace;
        if (!GetAce(acl, i, (void **)&ace) || ace->Header.AceType != ACCESS_ALLOWED_ACE_TYPE ||
            ace->Header.AceFlags || ace->Mask != FILE_ALL_ACCESS) { error = ERROR_ACCESS_DENIED; break; }
        PSID sid = (PSID)&ace->SidStart;
        unsigned bit = EqualSid(sid, user_sid) ? 1 : EqualSid(sid, system_sid) ? 2 : 0;
        if (!bit || (found & bit)) error = ERROR_ACCESS_DENIED;
        found |= bit;
    }
    if (found != 3) error = ERROR_ACCESS_DENIED;
    LocalFree(descriptor);
    return error;
}
DWORD suite_seed_guard_bind(suite_private_hive *context)
{
    if (active || !suite_hive_ready(context)) return ERROR_INVALID_STATE;
    if (wcslen(suite_hive_seed(context)) >= PATH_CAP) return ERROR_FILENAME_EXCED_RANGE;
    wcscpy_s(seed_path, PATH_CAP, suite_hive_seed(context));
    HANDLE token;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) return GetLastError();
    DWORD size = sizeof(user_information), system_size = sizeof(system_sid);
    DWORD error = GetTokenInformation(token, TokenUser, user_information, size, &size) ?
        ERROR_SUCCESS : GetLastError();
    CloseHandle(token);
    if (error) return error;
    user_sid = ((TOKEN_USER *)user_information)->User.Sid;
    if (!CreateWellKnownSid(WinLocalSystemSid, NULL, system_sid, &system_size)) return GetLastError();
    /* 上游 ANSI 配置无法无损表达的路径必须拒绝，不能猜测其它落点。 */
    char path[PATH_CAP * 4];
    BOOL lossy = FALSE;
    UINT codepage = GetACP();
    if (!WideCharToMultiByte(codepage, codepage == CP_UTF8 ? WC_ERR_INVALID_CHARS : WC_NO_BEST_FIT_CHARS,
                             seed_path, -1, path, sizeof(path), NULL, codepage == CP_UTF8 ? NULL : &lossy) || lossy)
        return ERROR_NO_UNICODE_TRANSLATION;
    HANDLE file = CreateFileW(seed_path, FILE_READ_ATTRIBUTES | READ_CONTROL,
                              FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING,
                              FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (file == INVALID_HANDLE_VALUE) return GetLastError();
    error = check_handle(file);
    if (!CloseHandle(file) && !error) error = GetLastError();
    if (error) return error;
    ZeroMemory(&statistics, sizeof(statistics));
    owner_thread = GetCurrentThreadId();
    active = context;
    return ERROR_SUCCESS;
}
void suite_seed_guard_unbind(void)
{
    require_ready();
    if (seed_handle != INVALID_HANDLE_VALUE) reject(SUITE_SEED_IO);
    active = NULL;
    user_sid = NULL;
    SecureZeroMemory(user_information, sizeof(user_information));
}
suite_seed_statistics suite_seed_guard_statistics(void)
{ require_ready(); return statistics; }
HKEY suite_storage_hkcu(void)
{
    require_ready();
    statistics.registry_accesses++;
    /* 必须使用真实预定义伪句柄，实际映射由 RegOverridePredefKey 完成。 */
    return HKEY_CURRENT_USER;
}
HANDLE WINAPI suite_seed_CreateFileA(LPCSTR path, DWORD access, DWORD sharing,
                                    LPSECURITY_ATTRIBUTES security, DWORD creation,
                                    DWORD flags, HANDLE template_file)
{
    require_ready();
    wchar_t candidate[PATH_CAP];
    if (!path || !MultiByteToWideChar(CP_ACP, MB_ERR_INVALID_CHARS, path, -1, candidate, PATH_CAP) ||
        _wcsicmp(candidate, seed_path)) reject(SUITE_SEED_PATH);
    int writing = access == GENERIC_WRITE;
    if (seed_handle != INVALID_HANDLE_VALUE || security || template_file ||
        (writing ? creation != CREATE_ALWAYS || sharing != 0 || flags != FILE_ATTRIBUTE_NORMAL :
         access != GENERIC_READ || creation != OPEN_EXISTING ||
         sharing != (FILE_SHARE_READ | FILE_SHARE_WRITE) || flags != 0)) reject(SUITE_SEED_IO);
    if (writing) statistics.opens_write++; else statistics.opens_read++;
    /* 不使用 CREATE_ALWAYS：同一句柄权限/实际路径核查完成后才截断已有 seed。 */
    HANDLE file = CreateFileW(seed_path, access | FILE_READ_ATTRIBUTES | READ_CONTROL,
                              sharing, NULL, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    if (file == INVALID_HANDLE_VALUE) reject(SUITE_SEED_OPEN);
    if (check_handle(file)) { CloseHandle(file); reject(SUITE_SEED_PERMISSION); }
    LARGE_INTEGER beginning;
    beginning.QuadPart = 0;
    if (writing && (!SetFilePointerEx(file, beginning, NULL, FILE_BEGIN) || !SetEndOfFile(file))) {
        CloseHandle(file); reject(SUITE_SEED_IO);
    }
    seed_handle = file;
    seed_writing = writing;
    return file;
}
BOOL WINAPI suite_seed_ReadFile(HANDLE file, LPVOID data, DWORD size, LPDWORD actual, LPOVERLAPPED overlapped)
{
    require_ready();
    if (file != seed_handle || overlapped || !actual || !ReadFile(file, data, size, actual, NULL))
        reject(SUITE_SEED_IO);
    statistics.reads++;
    statistics.bytes_read += *actual;
    return TRUE;
}
BOOL WINAPI suite_seed_WriteFile(HANDLE file, LPCVOID data, DWORD size, LPDWORD actual, LPOVERLAPPED overlapped)
{
    require_ready();
    if (file != seed_handle || overlapped || !actual || !WriteFile(file, data, size, actual, NULL) || *actual != size)
        reject(SUITE_SEED_IO);
    statistics.writes++;
    statistics.bytes_written += *actual;
    return TRUE;
}
BOOL WINAPI suite_seed_CloseHandle(HANDLE file)
{
    require_ready();
    if (file != seed_handle) reject(SUITE_SEED_IO);
    if (seed_writing && !FlushFileBuffers(file)) reject(SUITE_SEED_IO);
    if (!CloseHandle(file)) reject(SUITE_SEED_IO);
    seed_handle = INVALID_HANDLE_VALUE;
    seed_writing = 0;
    return TRUE;
}
BOOL WINAPI suite_seed_DeleteFileA(LPCSTR path)
{ (void)path; reject(SUITE_SEED_FALLBACK); return FALSE; }
HMODULE suite_seed_forbid_system_dll(const char *name)
{ (void)name; reject(SUITE_SEED_FALLBACK); return NULL; }
DWORD WINAPI suite_seed_forbid_environment(LPCSTR name, LPSTR buffer, DWORD size)
{ (void)name; (void)buffer; (void)size; reject(SUITE_SEED_FALLBACK); return 0; }
UINT WINAPI suite_seed_forbid_windows_directory(LPSTR buffer, UINT size)
{ (void)buffer; (void)size; reject(SUITE_SEED_FALLBACK); return 0; }
