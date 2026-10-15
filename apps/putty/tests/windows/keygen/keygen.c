/* 与受测客户端同目标／同配置的独立 PPK 校准工具，只输出公开摘要。 */
#include "pg_core.h"
#include "putty.h"
#include <stdio.h>
#include <string.h>
#ifdef _WIN32
#include <windows.h>
#endif

static int generate(const char *key_path, const char *passphrase_path)
{
    int result = 1;
    pg_error error = {0};
    pg_key *key = NULL;
    pg_key_info info = {0};
    char passphrase[256] = {0};
    /* pg_platform 的公开 Filename 适配在两平台均以 UTF-8 为契约。 */
    Filename *secret_name = filename_from_str(passphrase_path);
    if (!secret_name) return 1;
    FILE *secret = f_open(secret_name, "rb", false);
    filename_free(secret_name);
    if (!secret) return 1;
    size_t size = fread(passphrase, 1, sizeof(passphrase), secret);
    int valid = !ferror(secret) && size > 0 && size < sizeof(passphrase) && !memchr(passphrase, 0, size);
    if (fclose(secret)) valid = 0;
    if (!valid) goto cleanup;
    while (size && (passphrase[size - 1] == '\n' || passphrase[size - 1] == '\r')) passphrase[--size] = 0;
    if (!size || !pg_core_init(&error)) goto cleanup;
    pg_generate_params parameters = {PG_EDDSA, 255, PG_PRIME_PROBABLE, 0};
    if (!pg_generate(&parameters, NULL, 0, NULL, NULL, &key, &error) ||
        !pg_key_set_comment(key, "suite-terminal", &error)) goto cleanup;
    pg_ppk_params ppk;
    pg_ppk_default_params(&ppk);
    ppk.version = 3; ppk.argon2 = PG_ARGON2_ID; ppk.memory_kib = 8192;
    ppk.passes_auto = 1; ppk.passes_or_ms = 1500; ppk.parallelism = 1;
    if (!pg_save(key, key_path, PG_SAVE_PRIVATE, passphrase, &ppk, &error) ||
        !pg_key_get_info(key, &info, &error)) goto cleanup;
    /* 公钥行由固定算法及固定 ASCII 注释组成，不包含口令或私钥。 */
    if (!info.public_text || strncmp(info.public_text, "ssh-ed25519 ", 12) ||
        strpbrk(info.public_text, "\"\\\r\n")) goto cleanup;
    printf("{\"schema\":1,\"kind\":\"terminal-keygen\",\"passed\":true,"
           "\"argon2_target_ms\":1500,\"memory_kib\":8192,\"parallelism\":1,"
           "\"build_config\":\"%s\",\"compiler\":\"%s\",\"public_text\":\"%s\"}\n",
           SUITE_TERMINAL_KEYGEN_CONFIG, SUITE_TERMINAL_KEYGEN_COMPILER, info.public_text);
    result = 0;
cleanup:
    pg_secure_clear(passphrase, sizeof(passphrase));
    pg_key_info_free(&info);
    pg_key_free(key);
    pg_core_cleanup();
    if (result) fputs("测试密钥生成或参数校准失败\n", stderr);
    return result;
}
#ifdef _WIN32
int wmain(int argc, wchar_t **argv)
{
    if (argc != 3) return 1;
    char key[4096], secret[4096];
    if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[1], -1, key, sizeof(key), NULL, NULL) ||
        !WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, argv[2], -1, secret, sizeof(secret), NULL, NULL)) return 1;
    return generate(key, secret);
}
#else
int main(int argc, char **argv)
{ return argc == 3 ? generate(argv[1], argv[2]) : 1; }
#endif
