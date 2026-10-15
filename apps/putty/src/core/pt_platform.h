#ifndef PT_PLATFORM_H
#define PT_PLATFORM_H
#include "putty.h"

/* Qt 绘制接收 Unicode，不接收原 Win32 GDI 字形标签。 */
void pt_init_unicode(Conf *conf, struct unicode_data *unicode);
#endif
