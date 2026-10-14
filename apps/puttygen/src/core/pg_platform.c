#include "putty.h"
#include "ssh.h"
#include "pg_platform.h"
#include <errno.h>
#include <stdarg.h>
#ifdef _WIN32
#include <bcrypt.h>
#include <aclapi.h>
#include <fcntl.h>
#include <io.h>
#else
#include <sys/random.h>
#include <fcntl.h>
#include <unistd.h>
#endif

static unsigned char initial_seed[128];
#ifdef PG_TESTING
static int force_random_failure;
void pg_test_random_failure(int fail) { force_random_failure = fail; }
#ifdef _WIN32
static int force_private_security_failure;
void pg_test_private_security_failure(int fail) { force_private_security_failure = fail; }
#endif
#endif
int pg_platform_random(void *data, size_t size)
{
#ifdef PG_TESTING
    if (force_random_failure) return 0;
#endif
#ifdef _WIN32
    return BCryptGenRandom(NULL, data, (ULONG)size, BCRYPT_USE_SYSTEM_PREFERRED_RNG) == 0;
#else
    unsigned char *p = data;
    while (size) {
        ssize_t n = getrandom(p, size, 0);
        if (n < 0) { if (errno == EINTR) continue; return 0; }
        if (!n) return 0;
        p += n; size -= (size_t)n;
    }
    return 1;
#endif
}
int pg_platform_seed(void)
{
    int result = pg_platform_random(initial_seed, sizeof(initial_seed));
    if (!result) smemclr(initial_seed, sizeof(initial_seed));
    return result;
}
void noise_get_heavy(void (*callback)(void *, int))
{
    callback(initial_seed, sizeof(initial_seed));
    smemclr(initial_seed, sizeof(initial_seed));
}
void noise_regular(void) { }
void write_random_seed(void *data, int len) { (void)data; (void)len; }
/* 不接入终端配置或随机种子文件。内存耗尽沿用上游不可恢复语义。 */
void modalfatalbox(const char *fmt, ...)
{
    (void)fmt;
    abort();
}
void nonfatal(const char *fmt, ...) { (void)fmt; }

#ifdef _WIN32
Filename *filename_from_str(const char *path)
{
    Filename *fn = snew(Filename);
    fn->utf8path = dupstr(path); fn->cpath = dupstr(path);
    int n = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, NULL, 0);
    if (!n) { sfree(fn->utf8path); sfree(fn->cpath); sfree(fn); return NULL; }
    fn->wpath = snewn(n, wchar_t);
    MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, fn->wpath, n);
    return fn;
}
Filename *filename_from_utf8(const char *path) { return filename_from_str(path); }
const char *filename_to_str(const Filename *fn) { return fn->utf8path; }
void filename_free(Filename *fn)
{ if (fn) { sfree(fn->utf8path); sfree(fn->cpath); sfree(fn->wpath); sfree(fn); } }

static void file_errno(DWORD error)
{
    switch (error) {
      case ERROR_ACCESS_DENIED: case ERROR_SHARING_VIOLATION: case ERROR_LOCK_VIOLATION:
      case ERROR_PRIVILEGE_NOT_HELD: errno = EACCES; break;
      case ERROR_FILE_NOT_FOUND: case ERROR_PATH_NOT_FOUND: errno = ENOENT; break;
      case ERROR_FILE_EXISTS: case ERROR_ALREADY_EXISTS: errno = EEXIST; break;
      case ERROR_DISK_FULL: errno = ENOSPC; break;
      case ERROR_NOT_ENOUGH_MEMORY: case ERROR_OUTOFMEMORY: errno = ENOMEM; break;
      case ERROR_TOO_MANY_OPEN_FILES: errno = EMFILE; break;
      case ERROR_INVALID_NAME: case ERROR_INVALID_PARAMETER: errno = EINVAL; break;
      default: errno = EIO; break;
    }
}

/* 读回实际权限，既适用于本地 NTFS，也避免卷查询误拒绝支持 ACL 的 SMB 路径。 */
static DWORD verify_private_security(HANDLE handle, PSID user)
{
    PSECURITY_DESCRIPTOR descriptor = NULL;
    PSID owner = NULL;
    PACL acl = NULL;
    SECURITY_DESCRIPTOR_CONTROL control;
    DWORD revision;
    DWORD error = GetSecurityInfo(handle, SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
        &owner, NULL, &acl, NULL, &descriptor);
    if (error != ERROR_SUCCESS) return error;
    ACCESS_ALLOWED_ACE *ace = NULL;
    if (!owner || !EqualSid(owner, user) || !acl || acl->AceCount != 1 ||
        !GetSecurityDescriptorControl(descriptor, &control, &revision) || !(control & SE_DACL_PROTECTED) ||
        !GetAce(acl, 0, (void **)&ace) || ace->Header.AceType != ACCESS_ALLOWED_ACE_TYPE ||
        ace->Header.AceFlags != 0 || ace->Mask != FILE_ALL_ACCESS || !EqualSid(&ace->SidStart, user))
        error = ERROR_ACCESS_DENIED;
    LocalFree(descriptor);
    return error;
}

/* 私钥必须先取得受保护的权限，再截断或写入；全程使用同一个排他句柄。 */
static FILE *private_file_open(const Filename *fn, const char *mode)
{
    HANDLE token = NULL, handle = INVALID_HANDLE_VALUE;
    TOKEN_USER *user = NULL;
    ACL *acl = NULL;
    SECURITY_DESCRIPTOR descriptor;
    SECURITY_ATTRIBUTES attributes = {sizeof(attributes), &descriptor, FALSE};
    DWORD error = ERROR_SUCCESS, size = 0;
    int fd = -1;
    FILE *stream = NULL;
    bool update = strchr(mode, '+') != NULL;

    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) {
        error = GetLastError(); goto cleanup;
    }
    if (GetTokenInformation(token, TokenUser, NULL, 0, &size) ||
        GetLastError() != ERROR_INSUFFICIENT_BUFFER) {
        error = ERROR_INVALID_DATA; goto cleanup;
    }
    user = (TOKEN_USER *)snewn(size, unsigned char);
    if (!GetTokenInformation(token, TokenUser, user, size, &size)) {
        error = GetLastError(); goto cleanup;
    }
    size = sizeof(ACL) + sizeof(ACCESS_ALLOWED_ACE) - sizeof(DWORD) + GetLengthSid(user->User.Sid);
    acl = (ACL *)snewn(size, unsigned char);
    if (!InitializeAcl(acl, size, ACL_REVISION) ||
        !AddAccessAllowedAce(acl, ACL_REVISION, FILE_ALL_ACCESS, user->User.Sid) ||
        !InitializeSecurityDescriptor(&descriptor, SECURITY_DESCRIPTOR_REVISION) ||
        !SetSecurityDescriptorOwner(&descriptor, user->User.Sid, FALSE) ||
        !SetSecurityDescriptorDacl(&descriptor, TRUE, acl, FALSE) ||
        !SetSecurityDescriptorControl(&descriptor, SE_DACL_PROTECTED, SE_DACL_PROTECTED)) {
        error = GetLastError(); goto cleanup;
    }
    /* OPEN_ALWAYS 不截断旧文件；创建时就阻止从父目录继承宽权限。 */
    handle = CreateFileW(fn->wpath, GENERIC_WRITE | READ_CONTROL | WRITE_DAC | WRITE_OWNER |
        (update ? GENERIC_READ : 0), 0, &attributes, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    if (handle == INVALID_HANDLE_VALUE) { error = GetLastError(); goto cleanup; }
    if (GetFileType(handle) != FILE_TYPE_DISK) { error = ERROR_ACCESS_DENIED; goto cleanup; }
    /* 现有文件会忽略创建安全描述符，必须在写入前替换 owner 和 DACL。 */
#ifdef PG_TESTING
    if (force_private_security_failure) { error = ERROR_ACCESS_DENIED; goto cleanup; }
#endif
    error = SetSecurityInfo(handle, SE_FILE_OBJECT,
        OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
        user->User.Sid, NULL, acl, NULL);
    if (error != ERROR_SUCCESS) goto cleanup;
    error = verify_private_security(handle, user->User.Sid);
    if (error != ERROR_SUCCESS) goto cleanup;
    fd = _open_osfhandle((intptr_t)handle, (update ? _O_RDWR : _O_WRONLY) | _O_NOINHERIT |
        (strchr(mode, 'b') ? _O_BINARY : _O_TEXT) | (mode[0] == 'a' ? _O_APPEND : 0));
    if (fd < 0) goto cleanup;
    handle = INVALID_HANDLE_VALUE; /* 文件描述符接管句柄。 */
    stream = _fdopen(fd, mode);
    if (!stream) goto cleanup;
    fd = -1; /* FILE 接管文件描述符。 */
    if (mode[0] == 'w' && _chsize_s(_fileno(stream), 0) != 0) {
        int saved_errno = errno;
        fclose(stream); stream = NULL; errno = saved_errno;
    }
cleanup:
    {
        int saved_errno = errno;
        if (fd >= 0) _close(fd);
        if (handle != INVALID_HANDLE_VALUE) CloseHandle(handle);
        if (token) CloseHandle(token);
        sfree(acl); sfree(user);
        if (error != ERROR_SUCCESS) file_errno(error);
        else errno = saved_errno;
    }
    return stream;
}
FILE *f_open(const Filename *fn, const char *mode, bool is_private)
{
    if (is_private && (mode[0] == 'w' || mode[0] == 'a')) return private_file_open(fn, mode);
    wchar_t wm[16];
    if (!MultiByteToWideChar(CP_UTF8, 0, mode, -1, wm, 16)) return NULL;
    return _wfopen(fn->wpath, wm);
}
#else
Filename *filename_from_str(const char *path)
{ Filename *fn = snew(Filename); fn->path = dupstr(path); return fn; }
const char *filename_to_str(const Filename *fn) { return fn->path; }
void filename_free(Filename *fn) { if (fn) { sfree(fn->path); sfree(fn); } }
FILE *f_open(const Filename *fn, const char *mode, bool is_private)
{
    if (!is_private) return fopen(fn->path, mode);
    int fd = open(fn->path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd < 0) return NULL;
    FILE *f = fdopen(fd, mode);
    if (!f) close(fd);
    return f;
}
#endif
/* GUI 在加载探测时显示旧格式提示；工作线程不弹出窗口。 */
void old_keyfile_warning(void) { }
uint64_t prng_reseed_time_ms(void)
{
#ifdef _WIN32
    return GetTickCount64();
#else
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) abort();
    return (uint64_t)ts.tv_sec * 1000 + (uint64_t)ts.tv_nsec / 1000000;
#endif
}
#ifndef _WIN32
unsigned long getticks(void) { return (unsigned long)prng_reseed_time_ms(); }
#endif
