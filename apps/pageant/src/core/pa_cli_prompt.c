#include "putty.h"
#include "pa_cli.h"
#include <errno.h>
#include <unistd.h>
#include <sys/wait.h>

extern void (*pa_old_keyfile_warning_hook)(void);
extern char *(*pa_x_display_hook)(void);
void pa_cli_old_keyfile_warning(void);
char *pa_cli_platform_get_x_display(void);
static bool cli_mode;
void pa_cli_prepare(void)
{
    cli_mode = true;
    pa_old_keyfile_warning_hook = pa_cli_old_keyfile_warning;
    pa_x_display_hook = pa_cli_platform_get_x_display;
}

/* Linux 同程序同时链接原 CLI 空通知和 Qt 事件桥。只路由宿主通知，
 * 原 uxsel 表、网络解析和 CLI poll 循环完全保留。 */
uxsel_id *pa_cli_uxsel_input_add(int fd, int mask);
void pa_cli_uxsel_input_remove(uxsel_id *id);
uxsel_id *__real_uxsel_input_add(int fd, int mask);
void __real_uxsel_input_remove(uxsel_id *id);
uxsel_id *__wrap_uxsel_input_add(int fd, int mask)
{ return cli_mode ? pa_cli_uxsel_input_add(fd, mask) : __real_uxsel_input_add(fd, mask); }
void __wrap_uxsel_input_remove(uxsel_id *id)
{
    if (cli_mode) pa_cli_uxsel_input_remove(id);
    else __real_uxsel_input_remove(id);
}

/* 原 CLI 在任何 Qt 实例/线程出现前运行。需要图形提示时 fork+exec
 * 同一程序的独立 askpass 分支，父 CLI 只读口令管道，之后仍可安全 fork。 */
char *gtk_askpass_main(const char *display, const char *title,
                       const char *prompt, bool *success)
{
    (void)display; (void)title;
    *success = false;
    int descriptors[2];
    if (pipe(descriptors)) return dupstr("Cannot create passphrase pipe");
    pid_t child = fork();
    if (child == 0) {
        close(descriptors[0]);
        if (dup2(descriptors[1], STDOUT_FILENO) < 0) _exit(127);
        close(descriptors[1]);
        execl("/proc/self/exe", "pageant", "--qt-askpass", prompt, (char *)NULL);
        _exit(127);
    }
    close(descriptors[1]);
    if (child < 0) { close(descriptors[0]); return dupstr("Cannot start passphrase dialog"); }
    strbuf *response = strbuf_new_nm();
    bool readable = true;
    char buffer[512];
    while (true) {
        ssize_t count = read(descriptors[0], buffer, sizeof(buffer));
        if (count < 0 && errno == EINTR) continue;
        if (count < 0) { readable = false; break; }
        if (!count) break;
        if (response->len + (size_t)count <= 65536) put_data(response, buffer, (size_t)count);
        else { readable = false; strbuf_clear(response); break; }
        smemclr(buffer, sizeof(buffer));
    }
    smemclr(buffer, sizeof(buffer));
    close(descriptors[0]);
    int status;
    while (waitpid(child, &status, 0) < 0) {
        if (errno == EINTR) continue;
        strbuf_free(response); return dupstr("Cannot wait for passphrase dialog");
    }
    if (!readable || !WIFEXITED(status) || WEXITSTATUS(status)) {
        strbuf_free(response); return dupstr("Passphrase dialog cancelled");
    }
    if (response->len && response->s[response->len - 1] == '\n')
        strbuf_shrink_to(response, response->len - 1);
    char *text = mkstr(ptrlen_from_strbuf(response));
    strbuf_free(response);
    *success = true;
    return text;
}
