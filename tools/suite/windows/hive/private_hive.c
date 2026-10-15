/* 独立 Win32 私有 app hive；不链接上游、Qt 或客户端随机实现。 */
#include "private_hive.h"
#include <aclapi.h>
#include <bcrypt.h>
#include <wchar.h>
#include <stdlib.h>
#include <string.h>

#define PATH_CAP 1024
#define PIN_CAP 128
struct suite_private_hive {
    wchar_t temporary[PATH_CAP], root[PATH_CAP], hive[PATH_CAP], seed[PATH_CAP];
    DWORD thread;
    void *token_user;
    PSID user;
    BYTE system[SECURITY_MAX_SID_SIZE];
    HANDLE pins[PIN_CAP];
    size_t npins;
    HKEY key;
    int mapped, reserved;
};
static volatile LONG mapping_slot;

static DWORD canonical(const wchar_t *input, wchar_t output[PATH_CAP])
{
    if (!input || wcslen(input) < 4 || wcslen(input) >= PATH_CAP ||
        !((input[0] >= L'A' && input[0] <= L'Z') || (input[0] >= L'a' && input[0] <= L'z')) ||
        input[1] != L':' || (input[2] != L'\\' && input[2] != L'/'))
        return ERROR_BAD_PATHNAME;
    wcscpy_s(output, PATH_CAP, input);
    for (size_t i = 0; output[i]; ++i) if (output[i] == L'/') output[i] = L'\\';
    size_t begin = 3;
    for (size_t i = 3; ; ++i) {
        if (output[i] && wcschr(L"<>:\"|?*", output[i])) return ERROR_BAD_PATHNAME;
        if (!output[i] || output[i] == L'\\') {
            size_t length = i - begin;
            if (!length || output[i - 1] == L'.' || output[i - 1] == L' ' ||
                (length == 1 && output[begin] == L'.') ||
                (length == 2 && output[begin] == L'.' && output[begin + 1] == L'.'))
                return ERROR_BAD_PATHNAME;
            if (!output[i]) break;
            begin = i + 1;
        }
    }
    return ERROR_SUCCESS;
}
static int beneath(const wchar_t *path, const wchar_t *root)
{
    size_t n = wcslen(root);
    return wcslen(path) > n && !_wcsnicmp(path, root, n) && path[n] == L'\\';
}
static DWORD append(wchar_t output[PATH_CAP], const wchar_t *root, const wchar_t *name)
{
    return _snwprintf_s(output, PATH_CAP, _TRUNCATE, L"%s\\%s", root, name) < 0 ?
        ERROR_FILENAME_EXCED_RANGE : ERROR_SUCCESS;
}
static DWORD identity(suite_private_hive *context)
{
    HANDLE token;
    DWORD size = 0, system_size = sizeof(context->system);
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) return GetLastError();
    GetTokenInformation(token, TokenUser, NULL, 0, &size);
    context->token_user = calloc(1, size);
    DWORD error = ERROR_NOT_ENOUGH_MEMORY;
    if (context->token_user && GetTokenInformation(token, TokenUser, context->token_user, size, &size)) {
        context->user = ((TOKEN_USER *)context->token_user)->User.Sid;
        error = CreateWellKnownSid(WinLocalSystemSid, NULL, context->system, &system_size) ?
            ERROR_SUCCESS : GetLastError();
        if (!error && EqualSid(context->user, context->system)) error = ERROR_ACCESS_DENIED;
    } else if (context->token_user) error = GetLastError();
    CloseHandle(token);
    return error;
}
static DWORD security(suite_private_hive *context, int directory, SECURITY_DESCRIPTOR *descriptor, PACL *acl)
{
    EXPLICIT_ACCESSW entries[2];
    memset(entries, 0, sizeof(entries));
    for (unsigned i = 0; i < 2; ++i) {
        entries[i].grfAccessPermissions = FILE_ALL_ACCESS;
        entries[i].grfAccessMode = SET_ACCESS;
        entries[i].grfInheritance = directory ? SUB_CONTAINERS_AND_OBJECTS_INHERIT : NO_INHERITANCE;
        entries[i].Trustee.TrusteeForm = TRUSTEE_IS_SID;
        entries[i].Trustee.TrusteeType = TRUSTEE_IS_USER;
        entries[i].Trustee.ptstrName = (LPWSTR)(i ? (PSID)context->system : context->user);
    }
    DWORD error = SetEntriesInAclW(2, entries, NULL, acl);
    if (error) return error;
    if (!InitializeSecurityDescriptor(descriptor, SECURITY_DESCRIPTOR_REVISION) ||
        !SetSecurityDescriptorOwner(descriptor, context->user, FALSE) ||
        !SetSecurityDescriptorDacl(descriptor, TRUE, *acl, FALSE) ||
        !SetSecurityDescriptorControl(descriptor, SE_DACL_PROTECTED, SE_DACL_PROTECTED))
        return GetLastError();
    return ERROR_SUCCESS;
}
static DWORD shape(HANDLE handle, int directory)
{
    BY_HANDLE_FILE_INFORMATION info;
    if (!GetFileInformationByHandle(handle, &info)) return GetLastError();
    if (GetFileType(handle) != FILE_TYPE_DISK || (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) ||
        !!(info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != !!directory ||
        (!directory && info.nNumberOfLinks != 1)) return ERROR_ACCESS_DENIED;
    return ERROR_SUCCESS;
}
static DWORD private_acl(suite_private_hive *context, HANDLE handle, int directory)
{
    PSID owner = NULL;
    PACL acl = NULL;
    PSECURITY_DESCRIPTOR descriptor = NULL;
    DWORD error = shape(handle, directory);
    if (error) return error;
    error = GetSecurityInfo(handle, SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                            &owner, NULL, &acl, NULL, &descriptor);
    if (error) return error;
    SECURITY_DESCRIPTOR_CONTROL control;
    DWORD revision;
    unsigned found = 0;
    if (!owner || !EqualSid(owner, context->user) || !acl || acl->AceCount != 2 ||
        !GetSecurityDescriptorControl(descriptor, &control, &revision) || !(control & SE_DACL_PROTECTED))
        error = ERROR_ACCESS_DENIED;
    for (DWORD i = 0; !error && i < acl->AceCount; ++i) {
        ACCESS_ALLOWED_ACE *ace;
        if (!GetAce(acl, i, (void **)&ace) || ace->Header.AceType != ACCESS_ALLOWED_ACE_TYPE ||
            ace->Header.AceFlags != (directory ? OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE : 0) ||
            ace->Mask != FILE_ALL_ACCESS) { error = ERROR_ACCESS_DENIED; break; }
        PSID sid = (PSID)&ace->SidStart;
        unsigned bit = EqualSid(sid, context->user) ? 1 : EqualSid(sid, context->system) ? 2 : 0;
        if (!bit || (found & bit)) error = ERROR_ACCESS_DENIED;
        found |= bit;
    }
    if (found != 3) error = ERROR_ACCESS_DENIED;
    LocalFree(descriptor);
    return error;
}
static HANDLE open_path(const wchar_t *path, DWORD access, int directory)
{
    return CreateFileW(path, access, FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING,
                       FILE_FLAG_OPEN_REPARSE_POINT | (directory ? FILE_FLAG_BACKUP_SEMANTICS : 0), NULL);
}
static DWORD pin_chain(suite_private_hive *context, const wchar_t *path)
{
    wchar_t prefix[PATH_CAP], final[PATH_CAP + 4];
    wcscpy_s(prefix, PATH_CAP, path);
    size_t n = wcslen(path);
    for (size_t i = 3; i <= n; ++i) if (i == n || path[i] == L'\\') {
        if (context->npins == PIN_CAP) return ERROR_BUFFER_OVERFLOW;
        prefix[i] = 0;
        HANDLE handle = open_path(prefix, FILE_READ_ATTRIBUTES | READ_CONTROL, 1);
        prefix[i] = path[i];
        if (handle == INVALID_HANDLE_VALUE) return GetLastError();
        context->pins[context->npins++] = handle;
        DWORD error = shape(handle, 1);
        if (error) return error;
        DWORD length = GetFinalPathNameByHandleW(handle, final, PATH_CAP + 4, FILE_NAME_NORMALIZED | VOLUME_NAME_DOS);
        prefix[i] = 0;
        if (!length || length >= PATH_CAP + 4 || wcsncmp(final, L"\\\\?\\", 4) || _wcsicmp(final + 4, prefix))
            return ERROR_BAD_PATHNAME;
        prefix[i] = path[i];
    }
    return ERROR_SUCCESS;
}
static DWORD make_directory(suite_private_hive *context, const wchar_t *path)
{
    SECURITY_DESCRIPTOR descriptor;
    PACL acl = NULL;
    DWORD error = security(context, 1, &descriptor, &acl);
    if (!error) {
        SECURITY_ATTRIBUTES attributes = {sizeof(attributes), &descriptor, FALSE};
        if (!CreateDirectoryW(path, &attributes) && GetLastError() != ERROR_ALREADY_EXISTS) error = GetLastError();
    }
    if (acl) LocalFree(acl);
    if (error) return error;
    HANDLE handle = open_path(path, FILE_READ_ATTRIBUTES | READ_CONTROL, 1);
    if (handle == INVALID_HANDLE_VALUE) return GetLastError();
    error = private_acl(context, handle, 1);
    CloseHandle(handle);
    return error;
}
static DWORD file_check(suite_private_hive *context, const wchar_t *path, int harden)
{
    HANDLE handle = open_path(path, FILE_READ_ATTRIBUTES | READ_CONTROL |
                              (harden ? WRITE_DAC | WRITE_OWNER : 0), 0);
    if (handle == INVALID_HANDLE_VALUE) return GetLastError();
    DWORD error = shape(handle, 0);
    PACL acl = NULL;
    SECURITY_DESCRIPTOR descriptor;
    if (!error && harden) {
        error = security(context, 0, &descriptor, &acl);
        if (!error) error = SetSecurityInfo(handle, SE_FILE_OBJECT,
            OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
            context->user, NULL, acl, NULL);
    }
    if (!error) error = private_acl(context, handle, 0);
    if (acl) LocalFree(acl);
    CloseHandle(handle);
    return error;
}
static DWORD all_files(suite_private_hive *context, int harden, int remove)
{
    wchar_t pattern[PATH_CAP], path[PATH_CAP];
    DWORD error = append(pattern, context->root, L"*");
    if (error) return error;
    WIN32_FIND_DATAW entry;
    HANDLE search = FindFirstFileW(pattern, &entry);
    if (search == INVALID_HANDLE_VALUE) return GetLastError();
    do {
        if (!wcscmp(entry.cFileName, L".") || !wcscmp(entry.cFileName, L"..")) continue;
        if ((error = append(path, context->root, entry.cFileName)) != 0 ||
            (error = file_check(context, path, harden)) != 0) break;
        if (remove && !DeleteFileW(path)) { error = GetLastError(); break; }
    } while (FindNextFileW(search, &entry));
    if (!error && GetLastError() != ERROR_NO_MORE_FILES) error = GetLastError();
    FindClose(search);
    return error;
}
static void dispose(suite_private_hive *context)
{
    if (!context) return;
    for (size_t i = context->npins; i > 0; --i) CloseHandle(context->pins[i - 1]);
    free(context->token_user);
    if (context->reserved) InterlockedExchange(&mapping_slot, 0);
    SecureZeroMemory(context, sizeof(*context));
    free(context);
}
static DWORD allocate(const wchar_t *temporary, suite_private_hive **result, int reserve)
{
    if (!result) return ERROR_INVALID_PARAMETER;
    *result = NULL;
    if (reserve && InterlockedCompareExchange(&mapping_slot, 1, 0)) return ERROR_BUSY;
    suite_private_hive *context = calloc(1, sizeof(*context));
    if (!context) {
        if (reserve) InterlockedExchange(&mapping_slot, 0);
        return ERROR_NOT_ENOUGH_MEMORY;
    }
    context->reserved = reserve;
    context->thread = GetCurrentThreadId();
    DWORD error = canonical(temporary, context->temporary);
    const wchar_t *last = wcsrchr(context->temporary, L'\\');
    if (!error && (!last || _wcsicmp(last + 1, L".tmp"))) error = ERROR_BAD_PATHNAME;
    if (!error) error = identity(context);
    if (!error) error = pin_chain(context, context->temporary);
    if (error) { dispose(context); return error; }
    *result = context;
    return ERROR_SUCCESS;
}
static DWORD prepare_root(suite_private_hive *context, const wchar_t *root)
{
    DWORD error = canonical(root, context->root);
    if (!error && !beneath(context->root, context->temporary)) error = ERROR_BAD_PATHNAME;
    if (error) return error;
    const wchar_t *name = wcsrchr(context->root, L'\\');
    if (!name || wcslen(name + 1) != 36 || wcsncmp(name + 1, L"run-", 4)) return ERROR_BAD_PATHNAME;
    for (const wchar_t *p = name + 5; *p; ++p)
        if (!wcschr(L"0123456789abcdef", *p)) return ERROR_BAD_PATHNAME;
    error = pin_chain(context, context->root);
    if (!error) error = private_acl(context, context->pins[context->npins - 1], 1);
    if (!error) error = append(context->hive, context->root, L"registry.hive");
    if (!error) error = append(context->seed, context->root, L"seed.bin");
    return error;
}
static DWORD seed_file(suite_private_hive *context)
{
    SECURITY_DESCRIPTOR descriptor;
    PACL acl = NULL;
    DWORD error = security(context, 0, &descriptor, &acl);
    HANDLE file = INVALID_HANDLE_VALUE;
    if (!error) {
        SECURITY_ATTRIBUTES attributes = {sizeof(attributes), &descriptor, FALSE};
        file = CreateFileW(context->seed, GENERIC_READ | GENERIC_WRITE | READ_CONTROL,
                            0, &attributes, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, NULL);
        if (file == INVALID_HANDLE_VALUE) error = GetLastError();
    }
    if (!error) error = private_acl(context, file, 0);
    const BYTE marker[] = {0x73, 0x75, 0x69, 0x74, 0x65, 0, 0xff};
    BYTE readback[sizeof(marker)];
    DWORD size;
    if (!error && (!WriteFile(file, marker, sizeof(marker), &size, NULL) || size != sizeof(marker) ||
        !FlushFileBuffers(file) || SetFilePointer(file, 0, NULL, FILE_BEGIN) == INVALID_SET_FILE_POINTER ||
        !ReadFile(file, readback, sizeof(readback), &size, NULL) || size != sizeof(marker) ||
        memcmp(marker, readback, sizeof(marker)))) error = ERROR_WRITE_FAULT;
    if (file != INVALID_HANDLE_VALUE) CloseHandle(file);
    if (acl) LocalFree(acl);
    return error;
}
int suite_hive_ready(const suite_private_hive *context)
{ return context && context->thread == GetCurrentThreadId() && context->mapped && context->key; }
const wchar_t *suite_hive_root(const suite_private_hive *context) { return context ? context->root : NULL; }
const wchar_t *suite_hive_seed(const suite_private_hive *context) { return context ? context->seed : NULL; }
HKEY suite_hive_key(const suite_private_hive *context) { return suite_hive_ready(context) ? context->key : NULL; }

static DWORD prove_mapping(suite_private_hive *context)
{
    wchar_t path[PATH_CAP];
    const wchar_t *name = wcsrchr(context->root, L'\\');
    if (!name || _snwprintf_s(path, PATH_CAP, _TRUNCATE,
                              L"Software\\SuiteHiveProbe\\%s", name + 1) < 0)
        return ERROR_BAD_PATHNAME;
    HKEY key;
    DWORD value = GetCurrentProcessId(), error;
    error = RegCreateKeyExW(HKEY_CURRENT_USER, path, 0, NULL, 0, KEY_READ | KEY_WRITE, NULL, &key, NULL);
    if (!error) {
        error = RegSetValueExW(key, L"ProcessProof", 0, REG_DWORD, (const BYTE *)&value, sizeof(value));
        DWORD closed = RegCloseKey(key);
        if (!error) error = closed;
    }
    if (!error) {
        error = RegOpenKeyExW(context->key, path, 0, KEY_READ, &key);
        if (!error) {
            DWORD actual = 0, size = sizeof(actual), type = 0;
            error = RegQueryValueExW(key, L"ProcessProof", NULL, &type, (BYTE *)&actual, &size);
            if (!error && (actual != value || type != REG_DWORD || size != sizeof(actual))) error = ERROR_INVALID_DATA;
            DWORD closed = RegCloseKey(key);
            if (!error) error = closed;
        }
    }
    return error;
}

DWORD suite_hive_resume(suite_private_hive *context)
{
    if (!context || context->thread != GetCurrentThreadId()) return ERROR_INVALID_PARAMETER;
    if (context->mapped || context->key) return ERROR_ALREADY_INITIALIZED;
    if (!context->reserved) {
        if (InterlockedCompareExchange(&mapping_slot, 1, 0)) return ERROR_BUSY;
        context->reserved = 1;
    }
    DWORD error = all_files(context, 0, 0);
    if (!error) error = file_check(context, context->hive, 0);
    if (!error) error = file_check(context, context->seed, 0);
    if (!error) error = RegLoadAppKeyW(context->hive, &context->key, KEY_ALL_ACCESS, REG_PROCESS_APPKEY, 0);
    /* 加载过程中若新增日志，其实际 owner/受保护 DACL 也必须满足同一契约。 */
    if (!error) error = all_files(context, 0, 0);
    if (!error) error = RegOverridePredefKey(HKEY_CURRENT_USER, context->key);
    if (!error) {
        context->mapped = 1;
        error = prove_mapping(context);
        if (!error) return ERROR_SUCCESS;
        /* 证明失败后不能留下可继续使用的未知映射；恢复失败必须终止测试进程。 */
        if (suite_hive_pause(context)) abort();
        return error;
    }
    if (context->key) {
        /* 键关闭失败时不能遗失句柄或允许另一映射继续初始化。 */
        if (RegCloseKey(context->key)) abort();
        context->key = NULL;
    }
    context->reserved = 0;
    InterlockedExchange(&mapping_slot, 0);
    return error;
}
DWORD suite_hive_pause(suite_private_hive *context)
{
    if (!context || context->thread != GetCurrentThreadId()) return ERROR_INVALID_PARAMETER;
    if (!context->mapped && !context->key) return ERROR_SUCCESS;
    DWORD error = ERROR_SUCCESS;
    if (context->mapped) {
        error = RegFlushKey(context->key);
        if (error) return error;
        error = RegOverridePredefKey(HKEY_CURRENT_USER, NULL);
        if (error) return error;
        context->mapped = 0;
    }
    error = RegCloseKey(context->key);
    if (error) return error;
    context->key = NULL;
    if (!error) error = all_files(context, 1, 0);
    context->reserved = 0;
    InterlockedExchange(&mapping_slot, 0);
    return error;
}
DWORD suite_hive_close(suite_private_hive *context)
{
    if (!context) return ERROR_SUCCESS;
    DWORD error = suite_hive_pause(context);
    if (error) return error;
    dispose(context);
    return ERROR_SUCCESS;
}
DWORD suite_hive_open(const wchar_t *temporary, const wchar_t *root, suite_private_hive **result)
{
    if (!result) return ERROR_INVALID_PARAMETER;
    *result = NULL;
    suite_private_hive *context = NULL;
    DWORD error = allocate(temporary, &context, 1);
    if (!error) error = prepare_root(context, root);
    if (!error) error = suite_hive_resume(context);
    if (error) { dispose(context); *result = NULL; return error; }
    *result = context;
    return ERROR_SUCCESS;
}
DWORD suite_hive_create(const wchar_t *temporary, const wchar_t *parent, suite_private_hive **result)
{
    if (!result) return ERROR_INVALID_PARAMETER;
    *result = NULL;
    suite_private_hive *context = NULL;
    DWORD error = allocate(temporary, &context, 1);
    wchar_t parent_path[PATH_CAP], root[PATH_CAP], name[37] = L"run-";
    BYTE random[16];
    if (!error) error = canonical(parent, parent_path);
    if (!error && (!beneath(parent_path, context->temporary) ||
        wcschr(parent_path + wcslen(context->temporary) + 1, L'\\'))) error = ERROR_BAD_PATHNAME;
    if (!error) error = make_directory(context, parent_path);
    /* 创建运行根和文件前固定父目录，避免核查后被替换成其它路径。 */
    if (!error) error = pin_chain(context, parent_path);
    if (!error) error = private_acl(context, context->pins[context->npins - 1], 1);
    if (!error && BCryptGenRandom(NULL, random, sizeof(random), BCRYPT_USE_SYSTEM_PREFERRED_RNG) != 0)
        error = ERROR_GEN_FAILURE;
    for (unsigned i = 0; !error && i < sizeof(random); ++i) {
        name[4 + 2 * i] = L"0123456789abcdef"[random[i] >> 4];
        name[5 + 2 * i] = L"0123456789abcdef"[random[i] & 15];
    }
    if (!error) error = append(root, parent_path, name);
    if (!error && GetFileAttributesW(root) != INVALID_FILE_ATTRIBUTES) error = ERROR_ALREADY_EXISTS;
    if (!error) error = make_directory(context, root);
    if (!error) error = prepare_root(context, root);
    if (!error) error = seed_file(context);
    HKEY initial = NULL;
    /* lpFile 必须不存在；让系统生成合法空 hive，不复制任何个人/default hive。 */
    if (!error && GetFileAttributesW(context->hive) != INVALID_FILE_ATTRIBUTES) error = ERROR_ALREADY_EXISTS;
    if (!error) error = RegLoadAppKeyW(context->hive, &initial, KEY_ALL_ACCESS, REG_PROCESS_APPKEY, 0);
    if (initial) {
        DWORD flush = RegFlushKey(initial), closed = RegCloseKey(initial);
        if (!error) error = flush ? flush : closed;
    }
    /* 首次关闭后收紧系统生成的 hive/事务日志，再加载并映射；父目录始终只授两 SID。 */
    if (!error) error = all_files(context, 1, 0);
    if (!error) error = suite_hive_resume(context);
    if (error) { dispose(context); if (result) *result = NULL; return error; }
    *result = context;
    return ERROR_SUCCESS;
}
DWORD suite_hive_cleanup(const wchar_t *temporary, const wchar_t *root)
{
    suite_private_hive *context = NULL;
    /* 清理同样占用进程槽，活动映射期间不得先删种子或日志再因 hive 占用失败。 */
    DWORD error = allocate(temporary, &context, 1);
    if (!error) error = prepare_root(context, root);
    if (!error) error = all_files(context, 0, 1);
    if (!error) {
        /* 只释放最后的运行根 pin；其父路径仍禁止删除/替换。 */
        CloseHandle(context->pins[--context->npins]);
        if (!RemoveDirectoryW(context->root)) error = GetLastError();
    }
    dispose(context);
    return error;
}
