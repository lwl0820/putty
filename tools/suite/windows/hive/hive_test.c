/* 只证明独立 hive 与文件隔离；不链接上游或启动客户端 PRNG。 */
#include "private_hive.h"
#include <aclapi.h>
#include <stdio.h>
#include <wchar.h>

#ifdef NDEBUG
#error 私有 hive 自测必须保留 Release 断言
#endif
#ifndef SUITE_HIVE_PROJECT_TMP
#error 必须由构建定义项目 .tmp 绝对路径
#endif
#define WIDE_INNER(value) L##value
#define WIDE(value) WIDE_INNER(value)
#define PATH_CAP 1024
#define CHECK(value) do { if (!(value)) { \
    fprintf(stderr, "私有 hive 断言失败：%s，行 %d\n", #value, __LINE__); return 1; } } while (0)
#define OK(value) do { DWORD error_code = (value); if (error_code) { \
    fprintf(stderr, "私有 hive API 失败：%s，代码 %lu，行 %d\n", #value, (unsigned long)error_code, __LINE__); return 1; } } while (0)

static const wchar_t *temporary = WIDE(SUITE_HIVE_PROJECT_TMP);

static int valid_marker(const wchar_t *marker)
{
    const wchar_t *prefix = L"Software\\SuiteHiveProbe\\run-";
    size_t n = wcslen(prefix);
    if (wcsncmp(marker, prefix, n) || wcslen(marker + n) != 32) return 0;
    for (const wchar_t *p = marker + n; *p; ++p)
        if (!wcschr(L"0123456789abcdef", *p)) return 0;
    return 1;
}

static DWORD write_marker(HKEY base, const wchar_t *path, const wchar_t *value, DWORD marker)
{
    HKEY key;
    DWORD error = RegCreateKeyExW(base, path, 0, NULL, 0, KEY_READ | KEY_WRITE, NULL, &key, NULL);
    if (!error) {
        error = RegSetValueExW(key, value, 0, REG_DWORD, (const BYTE *)&marker, sizeof(marker));
        RegCloseKey(key);
    }
    return error;
}
static DWORD read_marker(HKEY base, const wchar_t *path, const wchar_t *value, DWORD expected)
{
    HKEY key;
    DWORD marker = 0, type = 0, size = sizeof(marker);
    DWORD error = RegOpenKeyExW(base, path, 0, KEY_READ, &key);
    if (!error) {
        error = RegQueryValueExW(key, value, NULL, &type, (BYTE *)&marker, &size);
        if (!error && (type != REG_DWORD || size != sizeof(marker) || marker != expected))
            error = ERROR_INVALID_DATA;
        RegCloseKey(key);
    }
    return error;
}
static DWORD seed_configuration(suite_private_hive *context)
{
    if (!suite_hive_ready(context)) return ERROR_INVALID_STATE;
    HKEY key;
    const wchar_t *seed = suite_hive_seed(context);
    DWORD error = RegCreateKeyExW(HKEY_CURRENT_USER, L"Software\\SimonTatham\\PuTTY", 0,
                                  NULL, 0, KEY_READ | KEY_WRITE, NULL, &key, NULL);
    if (!error) {
        error = RegSetValueExW(key, L"RandSeedFile", 0, REG_SZ, (const BYTE *)seed,
                               (DWORD)((wcslen(seed) + 1) * sizeof(wchar_t)));
        RegCloseKey(key);
    }
    if (!error) {
        error = RegOpenKeyExW(suite_hive_key(context), L"Software\\SimonTatham\\PuTTY", 0, KEY_READ, &key);
        if (!error) {
            wchar_t actual[PATH_CAP];
            DWORD size = sizeof(actual), type;
            error = RegQueryValueExW(key, L"RandSeedFile", NULL, &type, (BYTE *)actual, &size);
            if (!error && (type != REG_SZ || size != (wcslen(seed) + 1) * sizeof(wchar_t) || wcscmp(actual, seed)))
                error = ERROR_INVALID_DATA;
            RegCloseKey(key);
        }
    }
    return error;
}
static DWORD child(const wchar_t *mode, const wchar_t *root, const wchar_t *marker)
{
    wchar_t binary[PATH_CAP], command[PATH_CAP * 4];
    DWORD size = GetModuleFileNameW(NULL, binary, PATH_CAP);
    if (!size || size >= PATH_CAP) return ERROR_FILENAME_EXCED_RANGE;
    if (_snwprintf_s(command, PATH_CAP * 4, _TRUNCATE, L"\"%s\" %s \"%s\" \"%s\"",
                     binary, mode, root, marker) < 0) return ERROR_FILENAME_EXCED_RANGE;
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    ZeroMemory(&startup, sizeof(startup));
    startup.cb = sizeof(startup);
    ZeroMemory(&process, sizeof(process));
    if (!CreateProcessW(binary, command, NULL, NULL, FALSE, 0, NULL, root, &startup, &process))
        return GetLastError();
    DWORD wait = WaitForSingleObject(process.hProcess, 15000), exit_code = 1;
    if (wait != WAIT_OBJECT_0) {
        TerminateProcess(process.hProcess, 1);
        WaitForSingleObject(process.hProcess, 5000);
    } else GetExitCodeProcess(process.hProcess, &exit_code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return wait == WAIT_OBJECT_0 && !exit_code ? ERROR_SUCCESS : ERROR_PROCESS_ABORTED;
}

static int reject_unprotected_seed(suite_private_hive *context)
{
    HANDLE file = CreateFileW(suite_hive_seed(context), READ_CONTROL | WRITE_DAC,
                              FILE_SHARE_READ | FILE_SHARE_WRITE, NULL, OPEN_EXISTING,
                              FILE_FLAG_OPEN_REPARSE_POINT, NULL);
    CHECK(file != INVALID_HANDLE_VALUE);
    PACL acl = NULL;
    PSECURITY_DESCRIPTOR saved = NULL;
    OK(GetSecurityInfo(file, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
                       NULL, NULL, &acl, NULL, &saved));
    /* 只取消保护，不授予任何额外 SID；实际恢复前，隔离初始化必须拒绝。 */
    OK(SetSecurityInfo(file, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION | UNPROTECTED_DACL_SECURITY_INFORMATION,
                       NULL, NULL, acl, NULL));
    CHECK(suite_hive_resume(context) == ERROR_ACCESS_DENIED);
    CHECK(!suite_hive_ready(context) && suite_hive_key(context) == NULL);
    OK(SetSecurityInfo(file, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                       NULL, NULL, acl, NULL));
    LocalFree(saved);
    CHECK(CloseHandle(file));
    return 0;
}

int wmain(int argc, wchar_t **argv)
{
    if (argc == 4 && !wcscmp(argv[1], L"--unmapped")) {
        /* 只查询随机探针键，不读取任何个人会话，也绝不向真实 HKCU 写入。
         * 即使父进程当前已覆盖 HKCU，子进程仍必须看不到父标记。 */
        HKEY key = NULL;
        CHECK(valid_marker(argv[3]));
        DWORD error = RegOpenKeyExW(HKEY_CURRENT_USER, argv[3], 0, KEY_READ, &key);
        if (key) RegCloseKey(key);
        CHECK(error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND);
        CHECK(!suite_hive_ready(NULL));
        return 0;
    }
    if (argc == 4 && !wcscmp(argv[1], L"--open")) {
        CHECK(valid_marker(argv[3]));
        suite_private_hive *context = NULL;
        OK(suite_hive_open(temporary, argv[2], &context));
        CHECK(suite_hive_ready(context));
        OK(read_marker(HKEY_CURRENT_USER, argv[3], L"Parent", 0x51a7));
        OK(write_marker(HKEY_CURRENT_USER, argv[3], L"Child", 0xc11d));
        OK(read_marker(suite_hive_key(context), argv[3], L"Child", 0xc11d));
        OK(suite_hive_close(context));
        return 0;
    }
    CHECK(argc == 3 && !wcscmp(argv[1], L"--parent"));
    suite_private_hive *context = NULL, *second = NULL;
    OK(suite_hive_create(temporary, argv[2], &context));
    CHECK(suite_hive_ready(context));
    wchar_t root[PATH_CAP], marker[PATH_CAP];
    wcscpy_s(root, PATH_CAP, suite_hive_root(context));
    CHECK(SetEnvironmentVariableW(L"TEMP", root));
    CHECK(SetEnvironmentVariableW(L"TMP", root));
    CHECK(SetEnvironmentVariableW(L"TMPDIR", root));
    const wchar_t *name = wcsrchr(root, L'\\');
    CHECK(name != NULL);
    CHECK(_snwprintf_s(marker, PATH_CAP, _TRUNCATE, L"Software\\SuiteHiveProbe\\%s", name + 1) > 0);
    /* 第二个上下文不得覆盖全进程 HKCU，也不能产生第二个运行根。 */
    CHECK(suite_hive_create(temporary, argv[2], &second) == ERROR_BUSY && second == NULL);
    /* 清理不能在活动映射期间部分删除仍需使用的种子和日志。 */
    CHECK(suite_hive_cleanup(temporary, root) == ERROR_BUSY);
    CHECK(suite_hive_ready(context));
    CHECK(GetFileAttributesW(suite_hive_seed(context)) != INVALID_FILE_ATTRIBUTES);
    OK(write_marker(HKEY_CURRENT_USER, marker, L"Parent", 0x51a7));
    OK(read_marker(suite_hive_key(context), marker, L"Parent", 0x51a7));
    OK(seed_configuration(context));
    OK(child(L"--unmapped", root, marker));
    OK(suite_hive_pause(context));
    CHECK(!suite_hive_ready(context) && suite_hive_key(context) == NULL);
    CHECK(reject_unprotected_seed(context) == 0);
    /* REG_PROCESS_APPKEY 不并发共享；父进程解除映射并关闭全部键后子进程显式加载。 */
    OK(child(L"--open", root, marker));
    OK(suite_hive_resume(context));
    OK(read_marker(HKEY_CURRENT_USER, marker, L"Parent", 0x51a7));
    OK(read_marker(HKEY_CURRENT_USER, marker, L"Child", 0xc11d));
    OK(suite_hive_pause(context));
    /* 明确越界输入必须拒绝，拒绝时不得映射任何 hive。 */
    wchar_t invalid[PATH_CAP];
    CHECK(_snwprintf_s(invalid, PATH_CAP, _TRUNCATE, L"%s\\..\\outside", root) > 0);
    CHECK(suite_hive_open(temporary, invalid, &second) == ERROR_BAD_PATHNAME && second == NULL);
    CHECK(suite_hive_open(temporary, root, NULL) == ERROR_INVALID_PARAMETER);
    OK(suite_hive_close(context));
    OK(suite_hive_cleanup(temporary, root));
    CHECK(GetFileAttributesW(root) == INVALID_FILE_ATTRIBUTES && GetLastError() == ERROR_FILE_NOT_FOUND);
    puts("{\"schema\":1,\"kind\":\"private-hive\",\"passed\":true,"
         "\"checks\":[\"private-acl\",\"app-hive-create\",\"hkcu-handle-marker\",\"reload-persistence\","
         "\"child-not-inherited\",\"child-explicit-init\",\"private-seed-file\",\"permissions-closed\",\"single-mapping-and-path-guard\",\"cleanup\"],"
         "\"upstream_storage\":\"not-run\",\"upstream_seed\":\"not-run\",\"prng\":\"not-run\"}");
    return 0;
}
