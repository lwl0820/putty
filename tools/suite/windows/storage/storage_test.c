/* 真实上游存储与 PRNG 的独立宿主；每个进程先建立自己的私有 HKCU 映射。 */
#include "seed_guard.h"
#include "storage.h"
#include <wchar.h>

#ifdef NDEBUG
#error 存储自测必须保留 Release 断言
#endif
#ifndef SUITE_STORAGE_PROJECT_TMP
#error 必须指定项目 .tmp 的绝对路径
#endif
#define WIDE_INNER(value) L##value
#define WIDE(value) WIDE_INNER(value)
#define PATH_CAP 1024
#define CHECK(value) do { if (!(value)) { \
    fprintf(stderr, "存储断言失败：%s，行 %d\n", #value, __LINE__); return 1; } } while (0)
#define OK(value) do { DWORD error_code = (value); if (error_code) { \
    fprintf(stderr, "存储 API 失败：%s，代码 %lu，行 %d\n", #value, (unsigned long)error_code, __LINE__); return 1; } } while (0)

static const wchar_t *temporary = WIDE(SUITE_STORAGE_PROJECT_TMP);
static const char *session_name = "suite storage%probe";
static const wchar_t *session_path = L"Software\\SimonTatham\\PuTTY\\Sessions\\suite%20storage%25probe";
static const char *ca_name = "suite ca%probe";
static const wchar_t *ca_path = L"Software\\SimonTatham\\PuTTY\\SshHostCAs\\suite%20ca%25probe";
static const char *host = "suite-host.example";
static const char *host_key = "0x12345678,0xabcdef01";
static const BYTE seed_payload[] = {0, 0xff, 1, 0x80, 7, 0, 31, 99, 0x42, 0x35, 0xfe, 0x11, 0x28, 0x10, 0x98, 0x73};
static BYTE received[8192];
static size_t received_size;
extern unsigned suite_storage_timer_notifications;

static void consume_seed(void *data, int size)
{
    if (size < 0 || received_size + (size_t)size > sizeof(received)) ExitProcess(98);
    memcpy(received + received_size, data, size);
    received_size += size;
}
static void collect_seed(void)
{
    SecureZeroMemory(received, sizeof(received));
    received_size = 0;
    read_random_seed(consume_seed);
}
static DWORD seed_configuration(suite_private_hive *context)
{
    if (!suite_hive_ready(context)) return ERROR_INVALID_STATE;
    HKEY key;
    DWORD error = RegCreateKeyExW(HKEY_CURRENT_USER, L"Software\\SimonTatham\\PuTTY", 0,
                                  NULL, 0, KEY_READ | KEY_WRITE, NULL, &key, NULL);
    if (!error) {
        const wchar_t *path = suite_hive_seed(context);
        error = RegSetValueExW(key, L"RandSeedFile", 0, REG_SZ, (const BYTE *)path,
                               (DWORD)((wcslen(path) + 1) * sizeof(wchar_t)));
        RegCloseKey(key);
    }
    return error;
}
static DWORD raw_string(suite_private_hive *context, const wchar_t *path,
                        const wchar_t *name, const wchar_t *expected)
{
    HKEY key;
    DWORD error = RegOpenKeyExW(suite_hive_key(context), path, 0, KEY_READ, &key);
    if (!error) {
        wchar_t actual[PATH_CAP];
        DWORD size = sizeof(actual), type;
        error = RegQueryValueExW(key, name, NULL, &type, (BYTE *)actual, &size);
        if (!error && (type != REG_SZ || size != (wcslen(expected) + 1) * sizeof(wchar_t) ||
                       memcmp(actual, expected, size))) error = ERROR_INVALID_DATA;
        RegCloseKey(key);
    }
    return error;
}
static DWORD raw_integer(suite_private_hive *context, const wchar_t *path,
                         const wchar_t *name, DWORD expected)
{
    HKEY key;
    DWORD error = RegOpenKeyExW(suite_hive_key(context), path, 0, KEY_READ, &key);
    if (!error) {
        DWORD actual = 0, type, size = sizeof(actual);
        error = RegQueryValueExW(key, name, NULL, &type, (BYTE *)&actual, &size);
        if (!error && (type != REG_DWORD || size != sizeof(actual) || actual != expected)) error = ERROR_INVALID_DATA;
        RegCloseKey(key);
    }
    return error;
}
static int verify_session(void)
{
    Conf *conf = conf_new();
    CHECK(load_settings(session_name, conf));
    CHECK(!strcmp(conf_get_str(conf, CONF_host), host));
    CHECK(conf_get_int(conf, CONF_port) == 2227);
    CHECK(conf_get_int(conf, CONF_protocol) == PROT_RAW);
    CHECK(!strcmp(conf_get_str(conf, CONF_username), "suite-user"));
    FontSpec *font = conf_get_fontspec(conf, CONF_font);
    CHECK(!strcmp(font->name, "Courier New") && font->height == 17 && font->isbold);
    conf_free(conf);
    return 0;
}
static int session_roundtrip(suite_private_hive *context)
{
    settings_set_default_protocol(PROT_RAW);
    settings_set_default_port(22);
    Conf *conf = conf_new();
    do_defaults(NULL, conf);
    conf_set_str(conf, CONF_host, host);
    conf_set_int(conf, CONF_protocol, PROT_RAW);
    conf_set_int(conf, CONF_port, 2227);
    conf_set_str(conf, CONF_username, "suite-user");
    FontSpec *font = fontspec_new("Courier New", true, 17, ANSI_CHARSET);
    conf_set_fontspec(conf, CONF_font, font);
    fontspec_free(font);
    Filename *filename = filename_from_wstr(suite_hive_seed(context));
    conf_set_filename(conf, CONF_keyfile, filename);
    filename_free(filename);
    char *error = save_settings(session_name, conf);
    CHECK(error == NULL);
    conf_free(conf);
    CHECK(verify_session() == 0);
    OK(raw_string(context, session_path, L"HostName", L"suite-host.example"));
    OK(raw_integer(context, session_path, L"PortNumber", 2227));
    OK(raw_integer(context, session_path, L"FontHeight", 17));
    settings_r *read = open_settings_r(session_name);
    CHECK(read != NULL);
    Filename *loaded = read_setting_filename(read, "PublicKeyFile");
    CHECK(loaded && !_wcsicmp(filename_to_wstr(loaded), suite_hive_seed(context)));
    filename_free(loaded);
    close_settings_r(read);
    settings_e *enumerator = enum_settings_start();
    CHECK(enumerator != NULL);
    strbuf *name = strbuf_new();
    bool found = false;
    while (enum_settings_next(enumerator, name)) {
        if (!strcmp(name->s, session_name)) found = true;
        strbuf_clear(name);
    }
    strbuf_free(name);
    enum_settings_finish(enumerator);
    CHECK(found);
    return 0;
}
static int hostkey_roundtrip(suite_private_hive *context)
{
    CHECK(check_stored_host_key(host, 2227, "ed25519", host_key) == 1);
    store_host_key(NULL, host, 2227, "ed25519", host_key);
    CHECK(check_stored_host_key(host, 2227, "ed25519", host_key) == 0);
    CHECK(check_stored_host_key(host, 2227, "ed25519", "different-public-value") == 2);
    CHECK(have_ssh_host_key(host, 2227, "ed25519"));
    OK(raw_string(context, L"Software\\SimonTatham\\PuTTY\\SshHostKeys",
                   L"ed25519@2227:suite-host.example", L"0x12345678,0xabcdef01"));
    return 0;
}
static int verify_ca(suite_private_hive *context)
{
    host_ca *ca = host_ca_load(ca_name);
    CHECK(ca && ca->ca_public_key && ca->ca_public_key->len == sizeof(seed_payload));
    CHECK(!memcmp(ca->ca_public_key->s, seed_payload, sizeof(seed_payload)));
    CHECK(!strcmp(ca->validity_expression, "host:*.example"));
    CHECK(!ca->opts.permit_rsa_sha1 && ca->opts.permit_rsa_sha256 && !ca->opts.permit_rsa_sha512);
    host_ca_free(ca);
    OK(raw_integer(context, ca_path, L"PermitRSASHA1", 0));
    OK(raw_integer(context, ca_path, L"PermitRSASHA256", 1));
    OK(raw_integer(context, ca_path, L"PermitRSASHA512", 0));
    OK(raw_string(context, ca_path, L"Validity", L"host:*.example"));
    /* 独立句柄读取真实保存的 base64 公共字节，不依赖 host_ca_load 的解码。 */
    strbuf *encoded = base64_encode_sb(make_ptrlen(seed_payload, sizeof(seed_payload)), 0);
    wchar_t wide[256];
    CHECK(MultiByteToWideChar(CP_ACP, 0, encoded->s, -1, wide, 256));
    OK(raw_string(context, ca_path, L"PublicKey", wide));
    strbuf_free(encoded);
    return 0;
}
static int ca_roundtrip(suite_private_hive *context)
{
    /* 尚无 CA 父键时必须成功返回，不能遗留中间打开的键。 */
    DWORD before, after;
    HKEY absent = NULL;
    CHECK(RegOpenKeyExW(suite_hive_key(context), L"Software\\SimonTatham\\PuTTY\\SshHostCAs",
                        0, KEY_READ, &absent) == ERROR_FILE_NOT_FOUND);
    CHECK(GetProcessHandleCount(GetCurrentProcess(), &before));
    char *missing_error = host_ca_delete("not-created");
    CHECK(missing_error == NULL);
    CHECK(GetProcessHandleCount(GetCurrentProcess(), &after) && before == after);
    host_ca *ca = host_ca_new();
    ca->name = dupstr(ca_name);
    ca->ca_public_key = strbuf_new();
    put_data(ca->ca_public_key, seed_payload, sizeof(seed_payload));
    ca->validity_expression = dupstr("host:*.example");
    ca->opts.permit_rsa_sha512 = false;
    char *error = host_ca_save(ca);
    CHECK(error == NULL);
    host_ca_free(ca);
    CHECK(verify_ca(context) == 0);
    host_ca_enum *enumerator = enum_host_ca_start();
    CHECK(enumerator != NULL);
    strbuf *name = strbuf_new();
    bool found = false;
    while (enum_host_ca_next(enumerator, name)) {
        if (!strcmp(name->s, ca_name)) found = true;
        strbuf_clear(name);
    }
    strbuf_free(name);
    enum_host_ca_finish(enumerator);
    CHECK(found);
    return 0;
}
static int legacy_ca_roundtrip(suite_private_hive *context)
{
    /* 旧版 MatchHosts 没有 Validity；读取结果必须独立于被释放的注册表缓冲。 */
    static const wchar_t hosts[] = L"*.example\0legacy.example\0";
    HKEY key;
    OK(RegOpenKeyExW(suite_hive_key(context), ca_path, 0, KEY_READ | KEY_WRITE, &key));
    OK(RegDeleteValueW(key, L"Validity"));
    OK(RegSetValueExW(key, L"MatchHosts", 0, REG_MULTI_SZ, (const BYTE *)hosts, sizeof(hosts)));
    OK(RegCloseKey(key));
    host_ca *ca = host_ca_load(ca_name);
    CHECK(ca && ca->ca_public_key && ca->ca_public_key->len == sizeof(seed_payload));
    CHECK(!memcmp(ca->ca_public_key->s, seed_payload, sizeof(seed_payload)));
    CHECK(ca->validity_expression && !strcmp(ca->validity_expression, "*.example || legacy.example"));
    CHECK(!ca->opts.permit_rsa_sha1 && ca->opts.permit_rsa_sha256 && !ca->opts.permit_rsa_sha512);
    host_ca_free(ca);
    OK(RegOpenKeyExW(suite_hive_key(context), ca_path, 0, KEY_WRITE, &key));
    static const wchar_t validity[] = L"host:*.example";
    OK(RegSetValueExW(key, L"Validity", 0, REG_SZ, (const BYTE *)validity, sizeof(validity)));
    OK(RegDeleteValueW(key, L"MatchHosts"));
    OK(RegCloseKey(key));
    CHECK(verify_ca(context) == 0);
    return 0;
}
static int ca_delete(suite_private_hive *context)
{
    DWORD before, after;
    CHECK(GetProcessHandleCount(GetCurrentProcess(), &before));
    char *error = host_ca_delete(ca_name);
    CHECK(error == NULL && host_ca_load(ca_name) == NULL);
    HKEY key = NULL;
    CHECK(RegOpenKeyExW(suite_hive_key(context), ca_path, 0, KEY_READ, &key) == ERROR_FILE_NOT_FOUND);
    /* 父键仍存在；旧遗漏每次都泄漏一个 HKEY，重复删除必须保持实际句柄数。 */
    for (unsigned i = 0; i < 128; ++i) {
        error = host_ca_delete(ca_name);
        CHECK(error == NULL);
    }
    CHECK(GetProcessHandleCount(GetCurrentProcess(), &after) && before == after);
    return 0;
}
static DWORD child(const wchar_t *mode, const wchar_t *root, DWORD expected)
{
    wchar_t binary[PATH_CAP], command[PATH_CAP * 3];
    DWORD size = GetModuleFileNameW(NULL, binary, PATH_CAP);
    if (!size || size >= PATH_CAP ||
        _snwprintf_s(command, PATH_CAP * 3, _TRUNCATE, L"\"%s\" %s \"%s\"", binary, mode, root) < 0)
        return ERROR_BAD_PATHNAME;
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    ZeroMemory(&startup, sizeof(startup));
    startup.cb = sizeof(startup);
    ZeroMemory(&process, sizeof(process));
    if (!CreateProcessW(binary, command, NULL, NULL, FALSE, 0, NULL, root, &startup, &process))
        return GetLastError();
    DWORD wait = WaitForSingleObject(process.hProcess, 15000), code = 1;
    if (wait != WAIT_OBJECT_0) {
        TerminateProcess(process.hProcess, 99);
        WaitForSingleObject(process.hProcess, 5000);
    } else GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return wait == WAIT_OBJECT_0 && code == expected ? ERROR_SUCCESS : ERROR_PROCESS_ABORTED;
}
static int negative_child(suite_private_hive *context, const wchar_t *mode)
{
    HKEY key;
    OK(RegOpenKeyExW(HKEY_CURRENT_USER, L"Software\\SimonTatham\\PuTTY", 0, KEY_WRITE, &key));
    if (!wcscmp(mode, L"--missing")) {
        OK(RegDeleteValueW(key, L"RandSeedFile"));
    } else if (!wcscmp(mode, L"--corrupt")) {
        DWORD bad = 7;
        OK(RegSetValueExW(key, L"RandSeedFile", 0, REG_DWORD, (const BYTE *)&bad, sizeof(bad)));
    } else if (!wcscmp(mode, L"--outside")) {
        wchar_t invalid[PATH_CAP];
        CHECK(_snwprintf_s(invalid, PATH_CAP, _TRUNCATE, L"%s\\outside.rnd", suite_hive_root(context)) > 0);
        OK(RegSetValueExW(key, L"RandSeedFile", 0, REG_SZ, (const BYTE *)invalid,
                          (DWORD)((wcslen(invalid) + 1) * sizeof(wchar_t))));
    } else if (!wcscmp(mode, L"--read-locked") || !wcscmp(mode, L"--write-locked")) {
        HANDLE lock = CreateFileW(suite_hive_seed(context), GENERIC_READ, 0, NULL,
                                  OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, NULL);
        CHECK(lock != INVALID_HANDLE_VALUE);
        /* 保留真实独占锁，不能靠注入返回值伪造打开失败。 */
    } else return 1;
    OK(RegCloseKey(key));
    OK(RegFlushKey(suite_hive_key(context)));
    if (!wcscmp(mode, L"--write-locked")) write_random_seed((void *)seed_payload, sizeof(seed_payload));
    else collect_seed();
    return 1; /* 到此说明失败闭合门未生效。 */
}
static int prng_roundtrip(void)
{
    BYTE system_noise[32], first[64], second[64];
    CHECK(win_read_random(system_noise, sizeof(system_noise)));
    smemclr(system_noise, sizeof(system_noise));
    suite_seed_statistics before = suite_seed_guard_statistics();
    random_ref();
    CHECK(random_active == 1);
    random_read(first, sizeof(first));
    random_read(second, sizeof(second));
    CHECK(memcmp(first, second, sizeof(first)) != 0);
    CHECK(random_seed_bits() >= 256);
    smemclr(first, sizeof(first));
    smemclr(second, sizeof(second));
    random_save_seed();
    random_clear();
    CHECK(random_active == 0 && suite_storage_timer_notifications > 0);
    unsigned long next;
    CHECK(!run_timers(GetTickCount(), &next));
    suite_seed_statistics after = suite_seed_guard_statistics();
    CHECK(after.opens_read > before.opens_read && after.opens_write >= before.opens_write + 3);
    CHECK(after.bytes_read > before.bytes_read && after.bytes_written > before.bytes_written);
    random_ref();
    random_unref();
    CHECK(random_active == 0 && !run_timers(GetTickCount(), &next));
    collect_seed();
    CHECK(received_size > 0);
    smemclr(received, sizeof(received));
    return 0;
}
int wmain(int argc, wchar_t **argv)
{
    CHECK(argc == 3);
    if (!wcscmp(argv[1], L"--unbound")) {
        collect_seed(); /* 必须在第一次上游 HKCU 获取之前退出。 */
        return 1;
    }
    suite_private_hive *context = NULL;
    if (wcscmp(argv[1], L"--parent")) {
        OK(suite_hive_open(temporary, argv[2], &context));
        OK(suite_seed_guard_bind(context));
        if (!wcscmp(argv[1], L"--verify")) {
            CHECK(verify_session() == 0 && verify_ca(context) == 0);
            CHECK(check_stored_host_key(host, 2227, "ed25519", host_key) == 0);
            collect_seed();
            CHECK(received_size == sizeof(seed_payload) && !memcmp(received, seed_payload, sizeof(seed_payload)));
            char *error = NULL;
            settings_w *write = open_settings_w(session_name, &error);
            CHECK(write && !error);
            write_setting_i(write, "ChildSeen", 1);
            close_settings_w(write);
            suite_seed_guard_unbind();
            OK(suite_hive_close(context));
            return 0;
        }
        return negative_child(context, argv[1]);
    }
    OK(suite_hive_create(temporary, argv[2], &context));
    OK(suite_seed_guard_bind(context));
    wchar_t root[PATH_CAP];
    wcscpy_s(root, PATH_CAP, suite_hive_root(context));
    CHECK(SetEnvironmentVariableW(L"TEMP", root) && SetEnvironmentVariableW(L"TMP", root) &&
          SetEnvironmentVariableW(L"TMPDIR", root));
    OK(seed_configuration(context));
    OK(raw_string(context, L"Software\\SimonTatham\\PuTTY", L"RandSeedFile", suite_hive_seed(context)));
    CHECK(session_roundtrip(context) == 0);
    CHECK(hostkey_roundtrip(context) == 0);
    CHECK(ca_roundtrip(context) == 0);
    CHECK(legacy_ca_roundtrip(context) == 0);
    write_random_seed((void *)seed_payload, sizeof(seed_payload));
    collect_seed();
    CHECK(received_size == sizeof(seed_payload) && !memcmp(received, seed_payload, sizeof(seed_payload)));
    /* 再由未经过上游适配的 Win32 读句柄反查实际二进制内容。 */
    HANDLE raw = CreateFileW(suite_hive_seed(context), GENERIC_READ, FILE_SHARE_READ, NULL,
                             OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    BYTE bytes[sizeof(seed_payload) + 1];
    DWORD size;
    CHECK(raw != INVALID_HANDLE_VALUE && ReadFile(raw, bytes, sizeof(bytes), &size, NULL));
    CHECK(size == sizeof(seed_payload) && !memcmp(bytes, seed_payload, size) && CloseHandle(raw));
    suite_seed_statistics initial = suite_seed_guard_statistics();
    CHECK(initial.opens_read == 1 && initial.opens_write == 1 && initial.registry_accesses > 0);
    suite_seed_guard_unbind();
    OK(suite_hive_pause(context));
    OK(child(L"--verify", root, 0));
    OK(suite_hive_resume(context));
    OK(suite_seed_guard_bind(context));
    OK(raw_integer(context, session_path, L"ChildSeen", 1));
    const struct { const wchar_t *mode; DWORD code; } negatives[] = {
        {L"--unbound", SUITE_SEED_UNBOUND}, {L"--missing", SUITE_SEED_FALLBACK},
        {L"--corrupt", SUITE_SEED_FALLBACK}, {L"--outside", SUITE_SEED_PATH},
        {L"--read-locked", SUITE_SEED_OPEN}, {L"--write-locked", SUITE_SEED_OPEN}
    };
    for (size_t i = 0; i < lenof(negatives); ++i) {
        suite_seed_guard_unbind();
        OK(suite_hive_pause(context));
        OK(child(negatives[i].mode, root, negatives[i].code));
        OK(suite_hive_resume(context));
        OK(suite_seed_guard_bind(context));
        OK(seed_configuration(context));
        collect_seed();
        CHECK(received_size == sizeof(seed_payload) && !memcmp(received, seed_payload, sizeof(seed_payload)));
    }
    CHECK(prng_roundtrip() == 0);
    CHECK(verify_session() == 0 && verify_ca(context) == 0);
    del_settings(session_name);
    CHECK(open_settings_r(session_name) == NULL);
    CHECK(ca_delete(context) == 0);
    suite_seed_guard_unbind();
    OK(suite_hive_close(context));
    OK(suite_hive_cleanup(temporary, root));
    CHECK(GetFileAttributesW(root) == INVALID_FILE_ATTRIBUTES && GetLastError() == ERROR_FILE_NOT_FOUND);
    puts("{\"schema\":1,\"kind\":\"private-storage\",\"passed\":true,"
         "\"checks\":[\"session-settings\",\"session-enumeration\",\"font-filename\",\"hostkey-cache\",\"host-ca\",\"host-ca-delete\","
         "\"hive-handle-readback\",\"child-explicit-init\",\"seed-binary-read-write\",\"seed-private-handle\","
         "\"unbound-rejected\",\"missing-config-rejected\",\"corrupt-config-rejected\",\"outside-seed-rejected\","
         "\"read-open-failure-rejected\",\"write-open-failure-rejected\",\"real-prng\",\"prng-destroy-timers\",\"cleanup\"],"
         "\"storage_variant\":\"guarded-win32\",\"production_fallback\":\"not-run\","
         "\"existing_tests_enabled\":false,\"upstream_cli\":\"not-run\"}");
    return 0;
}
