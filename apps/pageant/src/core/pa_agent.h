#ifndef PA_AGENT_H
#define PA_AGENT_H
#include "putty_runtime/core.h"
#include <stdbool.h>
#ifdef __cplusplus
extern "C" {
#endif

/* 进程内消息只含定长字段和尾随字节；上游指针及解密材料不出核心线程。 */
#define PA_MAGIC UINT32_C(0x50414731)
#define PA_MAX_PUBLIC_BYTES (2u * 1024u * 1024u)
typedef struct pa_options {
    /* Linux 为原路径字节；Windows 可选 AF_UNIX 收上游 ACP 原字节，空串禁用。
     * Windows 内核按 UTF-8 解读，非法编码会被拒绝，不保证非 ASCII ACP 路径。 */
    char socket_path[108];
#ifdef _WIN32
    /* 二次启动仅向现有原版/新版代理添加密钥，不建立另一份密钥库。 */
    bool client_only;
#endif
} pa_options;
typedef enum pa_action {
    PA_REFRESH = 1, PA_ADD, PA_DELETE, PA_DELETE_ALL,
    PA_REENCRYPT, PA_REENCRYPT_ALL, PA_REPLY
} pa_action;
enum pa_flags { PA_ENCRYPTED = 1, PA_CANCEL = 2 };
typedef struct pa_input {
    uint32_t magic, action, flags, ssh_version;
    uint32_t path_size, passphrase_size, blob_size, reserved;
    /* 顺序：UTF-8 路径、口令、公钥 blob；均不含 NUL。 */
} pa_input;
typedef enum pa_packet_kind {
    PA_ENDPOINT = 1, PA_SNAPSHOT, PA_PROMPT, PA_RESULT, PA_DISMISS
} pa_packet_kind;
typedef enum pa_result { PA_SUCCESS, PA_FAILURE, PA_WARNING, PA_CANCELLED } pa_result;
typedef struct pa_packet {
    uint32_t magic, kind, action, result, count, length;
} pa_packet;
typedef struct pa_public_key {
    uint32_t ssh_version, flags, blob_size, comment_size, fingerprint_size;
    /* 顺序：公钥 blob、UTF-8 注释、可展示的指纹。身份是协议版本和完整 blob。 */
} pa_public_key;
extern const pr_module pa_agent_module;
#ifdef __cplusplus
}
#endif
#endif
