#ifndef PT_TERMINAL_H
#define PT_TERMINAL_H
#include "putty_runtime/core.h"
#include <wchar.h>
#ifdef __cplusplus
extern "C" {
#endif

/* 创建选项只用于进程内传递固定绘制入口，不可持久化或跨进程；
 * 所有入口严格在核心所有者线程调用，消息载荷不含上游或 Qt 对象。 */
typedef struct pt_render_config {
    uint64_t generation;
    int columns, rows, font_pixels, dpi, dpr_milli;
    char font_family[256];
} pt_render_config;
enum pt_style_flags {
    PT_BOLD = 1, PT_UNDERLINE = 2, PT_WIDE = 4, PT_NARROW = 8,
    PT_STRIKE = 16, PT_COMBINING = 32, PT_CURSOR_ACTIVE = 64
};
typedef struct pt_style {
    uint32_t foreground, background, flags;
    int line_mode;
} pt_style;
typedef struct pt_draw_ops {
    void *(*create)(pr_core *, const pt_render_config *);
    void (*destroy)(void *);
    int (*configure)(void *, const pt_render_config *);
    int (*begin)(void *);
    void (*text)(void *, int, int, const wchar_t *, int, pt_style);
    void (*cursor)(void *, int, int, const wchar_t *, int, pt_style);
    void (*sigil)(void *, int, int);
    int (*width)(void *, uint32_t);
    void (*end)(void *);
    void (*drained)(void *);
    void (*pixels)(void *, int *, int *);
    uint32_t (*revision)(void *);
} pt_draw_ops;
typedef struct pt_options {
    pt_draw_ops draw;
    pt_render_config render;
} pt_options;
typedef struct pt_connection {
    char host[256], user[128], keyfile[2048], command[4096];
    int port, request_pty;
} pt_connection;
/* 自有帧与状态使用固定头，尾部 length 字节；不含上游结构或敏感输入。 */
enum pt_packet_type {
    PT_FRAME = 1, PT_TITLE, PT_LOG, PT_ERROR, PT_AUTH_PROMPT,
    PT_HOST_KEY, PT_WEAK_CRYPTO, PT_SESSION_STARTED, PT_REMOTE_EXIT,
    PT_DISCONNECTED, PT_TRUST, PT_UNSUPPORTED, PT_SCREEN, PT_METRICS,
    PT_AUTH_WORK_BEGIN, PT_AUTH_WORK_END, PT_LOG_APPEND
};
typedef struct pt_packet {
    uint32_t magic, type;
    uint64_t render_generation;
    int32_t a, b, c, d;
    uint32_t length, revision;
} pt_packet;
#define PT_PACKET_MAGIC UINT32_C(0x50545431)
enum pt_custom_command { PT_SET_RENDER = 1, PT_GET_SCREEN, PT_KEY, PT_FOCUS };
enum pt_key { PT_KEY_UP = 1, PT_KEY_DOWN, PT_KEY_LEFT, PT_KEY_RIGHT };
extern const pr_module pt_terminal_module;
/* 渲染器和适配层共用的有界事件入口，载荷仅在调用期间有效。 */
pr_status pt_publish(pr_core *, pt_packet, const void *, size_t, uint64_t request);
#ifdef __cplusplus
}
#endif
#endif
