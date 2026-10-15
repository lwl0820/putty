/* 仅供无存储事件宿主：网络噪声不启用 PRNG，持久化入口一律致命失败。
 * 不定义 random_read/ref，避免与客户端真实随机对象产生双符号。
 * 只读静态链接审计必须确认 sshrand/storage 对象未被此宿主带入。 */
#include "putty.h"
#include "storage.h"
#include <stdio.h>

static void forbidden(const char *operation)
{
    fprintf(stderr, "无存储 Windows 宿主禁止调用：%s\n", operation);
    abort();
}

void noise_ultralight(NoiseSourceId source, unsigned long value)
{
    (void)source;
    (void)value;
}

void read_random_seed(noise_consumer_t consumer)
{
    (void)consumer;
    forbidden("read_random_seed");
}
void write_random_seed(void *data, int size)
{
    (void)data;
    (void)size;
    forbidden("write_random_seed");
}
settings_w *open_settings_w(const char *name, char **error)
{
    (void)name;
    (void)error;
    forbidden("open_settings_w");
    return NULL;
}
settings_r *open_settings_r(const char *name)
{
    (void)name;
    forbidden("open_settings_r");
    return NULL;
}
