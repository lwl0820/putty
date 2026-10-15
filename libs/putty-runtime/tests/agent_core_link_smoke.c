#include "putty.h"
#include "pageant.h"
#include "putty_runtime/core.h"

#ifndef _WINDOWS
/* 对应原版 unix/pageant.c 的产品策略，禁止客户端后端进入此宿主。 */
const bool share_can_be_downstream = false;
const bool share_can_be_upstream = false;
const bool buildinfo_gtk_relevant = false;

/* 原版 unix/noaskpass.c 在没有 keypress_prng 时也不收集轻量噪声。
 * 此方法不提供随机字节；random_read 保持 pageant.c 的致命错误分支。 */
void random_add_noise(NoiseSourceId source, const void *noise, int length)
{ (void)source; (void)noise; (void)length; }
void random_save_seed(void) { }
void random_destroy_seed(void) { }

char *platform_default_s(const char *name)
{ (void)name; return NULL; }
bool platform_default_b(const char *name, bool value)
{ (void)name; return value; }
int platform_default_i(const char *name, int value)
{ (void)name; return value; }
FontSpec *platform_default_fontspec(const char *name)
{ (void)name; return fontspec_new_default(); }
Filename *platform_default_filename(const char *name)
{ (void)name; return filename_from_str(""); }
char *platform_get_x_display(void) { return NULL; }
void old_keyfile_warning(void) { }
void keylist_update(void) { }

static void *create(pr_core *core, const void *options, size_t size)
{
    (void)options; (void)size;
    pageant_init();
    return core;
}
static pr_status command(void *context, const pr_command *value)
{ (void)context; (void)value; return PR_OK; }
static void destroy(void *context)
{
    (void)context;
    pageant_delete_all();
    pageant_forget_passphrases();
}
static int is_owner(void *context) { (void)context; return 1; }
static void wake(void *context) { (void)context; }
static void timer(void *context, unsigned long value)
{ (void)context; (void)value; }
static void watch(void *context, int fd, int mask, uint64_t registration)
{ (void)context; (void)fd; (void)mask; (void)registration; }
static pr_status event(void *context, const pr_event *value)
{ (void)context; (void)value; return PR_OK; }

int main(void)
{
    /* 只验证原代理核心与公共运行时的链接及初始化/关闭，不建立代理端点。 */
    const pr_host host = {is_owner, wake, timer, watch, event};
    const pr_module module = {create, command, destroy, NULL};
    pr_core *core = NULL;
    assert(pr_core_create(&host, NULL, 1, &module, NULL, 0, &core) == PR_OK);
    assert(pageant_count_ssh1_keys() == 0 && pageant_count_ssh2_keys() == 0);
    pr_core_begin_close(core);
    pr_core_destroy(core);
    return 0;
}
#else
int main(void)
{
    fputs("Windows 代理核心测试需要逐进程注册表及原生事件隔离探针\n", stderr);
    return 77;
}
#endif
