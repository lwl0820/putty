#include "putty.h"
#include "ssh.h"
#include "pg_platform.h"
#include <errno.h>
#include <stdarg.h>
#ifdef _WIN32
#include <bcrypt.h>
#else
#include <sys/random.h>
#include <fcntl.h>
#include <unistd.h>
#endif

static unsigned char initial_seed[128];
#ifdef PG_TESTING
static int force_random_failure;
void pg_test_random_failure(int fail) { force_random_failure = fail; }
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
FILE *f_open(const Filename *fn, const char *mode, bool is_private)
{
    (void)is_private;
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
