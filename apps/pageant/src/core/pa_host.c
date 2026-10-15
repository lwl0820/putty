#include "putty.h"

const bool share_can_be_downstream = false, share_can_be_upstream = false;
const bool buildinfo_gtk_relevant = false;

/* 与原 Pageant 一致：不初始化客户端 PRNG，也不收集 GUI 按键噪声。
 * random_read 仍由 pageant.c 提供禁止调用的致命错误实现。 */
void random_add_noise(NoiseSourceId source, const void *noise, int length)
{ (void)source; (void)noise; (void)length; }
void random_save_seed(void) { }
void random_destroy_seed(void) { }
#ifdef _WINDOWS
/* 固定原版 windows/pageant.c 明确不使用 PRNG，不能抽入客户端 noise.c。 */
void noise_ultralight(NoiseSourceId source, unsigned long value)
{ (void)source; (void)value; }
#else
char *platform_default_s(const char *name) { (void)name; return NULL; }
bool platform_default_b(const char *name, bool value) { (void)name; return value; }
int platform_default_i(const char *name, int value) { (void)name; return value; }
FontSpec *platform_default_fontspec(const char *name)
{ (void)name; return fontspec_new_default(); }
Filename *platform_default_filename(const char *name)
{ (void)name; return filename_from_str(""); }
char *(*pa_x_display_hook)(void);
char *platform_get_x_display(void)
{ return pa_x_display_hook ? pa_x_display_hook() : NULL; }
#endif
void (*pa_old_keyfile_warning_hook)(void);
void old_keyfile_warning(void)
{ if (pa_old_keyfile_warning_hook) pa_old_keyfile_warning_hook(); }
