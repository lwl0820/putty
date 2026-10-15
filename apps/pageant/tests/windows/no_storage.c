/* 此原生 IPC 宿主不接原设置或 PRNG；任何持久化入口都立即失败。 */
#include "putty.h"
#include "storage.h"
static void forbidden(const char *operation)
{ fprintf(stderr, "Pageant IPC 宿主禁止持久化：%s\n", operation); abort(); }
void read_random_seed(noise_consumer_t consumer)
{ (void)consumer; forbidden("read_random_seed"); }
void write_random_seed(void *data, int size)
{ (void)data; (void)size; forbidden("write_random_seed"); }
settings_w *open_settings_w(const char *name, char **error)
{ (void)name; (void)error; forbidden("open_settings_w"); return NULL; }
settings_r *open_settings_r(const char *name)
{ (void)name; forbidden("open_settings_r"); return NULL; }
