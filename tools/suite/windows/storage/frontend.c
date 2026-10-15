/* 独立存储宿主只提供前端策略，不替换 storage/settings/noise/PRNG。 */
#include "putty.h"
unsigned suite_storage_timer_notifications;
void timer_change_notify(unsigned long next)
{ (void)next; suite_storage_timer_notifications++; }
void nonfatal(const char *format, ...)
{ (void)format; ExitProcess(96); }
void modalfatalbox(const char *format, ...)
{ (void)format; ExitProcess(97); }

/* settings.c 需要协议标识表；本宿主不创建任何网络后端或连接。 */
static const BackendVtable metadata = {
    .id = "suite-storage", .protocol = PROT_RAW, .default_port = 22
};
const struct BackendVtable *const backends[] = { &metadata, NULL };
const int be_default_protocol = PROT_RAW;
const char *const appname = "SuiteStorage";
const size_t n_ui_backends = 0;
