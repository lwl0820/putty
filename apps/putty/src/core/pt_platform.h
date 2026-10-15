#ifndef PT_PLATFORM_H
#define PT_PLATFORM_H
#include "putty.h"

/* Qt 绘制接收 Unicode，不接收原 Win32 GDI 字形标签。 */
void pt_init_unicode(Conf *conf, struct unicode_data *unicode);
/* 自有 C 边界携带 Qt 的 UTF-8 路径，Windows 不按当前 ANSI 代码页解释。 */
Filename *pt_filename_from_utf8(const char *path);
#endif
