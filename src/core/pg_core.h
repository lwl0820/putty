#ifndef PG_CORE_H
#define PG_CORE_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* 所有接口由同一个工作线程串行调用；所有字符串均为 UTF-8。 */
typedef struct pg_key pg_key;
typedef enum { PG_OK, PG_ERR_ARGUMENT, PG_ERR_IO, PG_ERR_FORMAT,
    PG_ERR_PASSPHRASE, PG_ERR_RANDOM, PG_ERR_CERTIFICATE,
    PG_ERR_CERT_MISMATCH, PG_ERR_UNSUPPORTED } pg_error_code;
typedef struct { pg_error_code code; char detail[512]; } pg_error;
typedef enum { PG_RSA, PG_DSA, PG_ECDSA, PG_EDDSA, PG_RSA1 } pg_algorithm;
typedef enum { PG_PRIME_PROBABLE, PG_PRIME_PROVEN, PG_PRIME_PROVEN_EVEN } pg_prime_policy;
typedef struct { pg_algorithm algorithm; unsigned bits; pg_prime_policy prime_policy;
    int strong_rsa; } pg_generate_params;
typedef enum { PG_SAVE_PRIVATE, PG_SAVE_PUBLIC, PG_EXPORT_OPENSSH_AUTO,
    PG_EXPORT_OPENSSH_NEW, PG_EXPORT_SSHCOM } pg_save_format;
typedef enum { PG_ARGON2_ID, PG_ARGON2_I, PG_ARGON2_D } pg_argon2;
typedef struct { unsigned version; pg_argon2 argon2; uint32_t memory_kib;
    int passes_auto; uint32_t passes_or_ms; uint32_t parallelism; } pg_ppk_params;
typedef struct { int encrypted; int foreign_format; int obsolete_format; char *comment; char *format_name; } pg_file_info;
typedef struct { pg_algorithm algorithm; unsigned bits; int ssh1; int certificate;
    int can_add_certificate; int can_export_openssh; int can_export_sshcom; char *algorithm_name;
    char *comment; char *public_text; char *fingerprint_sha256; char *fingerprint_md5;
    char *certificate_info; } pg_key_info;
/* 进度为真实生成步骤，total 为 0 时表示该步骤无法预估完成比例。 */
typedef void (*pg_progress_fn)(void *context, unsigned completed, unsigned total);
int pg_core_init(pg_error *error);
void pg_core_cleanup(void);
unsigned pg_entropy_bits(void);
void pg_ppk_default_params(pg_ppk_params *params);
int pg_ppk_validate(const pg_ppk_params *params, const char *passphrase, pg_error *error);
int pg_generate(const pg_generate_params *params, const void *entropy, size_t entropy_size,
    pg_progress_fn progress, void *context, pg_key **result, pg_error *error);
int pg_probe_file(const char *path, pg_file_info *info, pg_error *error);
void pg_file_info_free(pg_file_info *info);
int pg_load(const char *path, const char *passphrase, pg_key **result, pg_error *error);
void pg_key_free(pg_key *key);
int pg_key_get_info(const pg_key *key, pg_key_info *info, pg_error *error);
void pg_key_info_free(pg_key_info *info);
int pg_key_set_comment(pg_key *key, const char *comment, pg_error *error);
int pg_save(pg_key *key, const char *path, pg_save_format format,
    const char *passphrase, const pg_ppk_params *params, pg_error *error);
int pg_add_certificate(pg_key *key, const char *path, pg_error *error);
int pg_remove_certificate(pg_key *key, pg_error *error);
/* 清除调用方持有的一次性敏感缓冲区。 */
void pg_secure_clear(void *data, size_t size);
#ifdef __cplusplus
}
#endif
#endif
