/* 此入口先于 Qt 创建映射；整个 QApplication 生命周期均不恢复个人 HKCU。 */
#include "hive_bootstrap.h"
#include "seed_guard.h"
#include <wchar.h>

#define WIDE_INNER(value) L##value
#define WIDE(value) WIDE_INNER(value)
#define PATH_CAP 1024
static suite_private_hive *hive;
static wchar_t root[PATH_CAP];
static DWORD main_thread;
static unsigned created, destroyed;
static suite_seed_statistics totals;
static const wchar_t *temporary = WIDE(PUTTY_TERMINAL_PROJECT_TMP);

suite_private_hive *suite_terminal_hive(void) { return hive; }
DWORD suite_terminal_prepare(void)
{
    wchar_t parent[PATH_CAP];
    DWORD length = GetEnvironmentVariableW(L"SUITE_TERMINAL_HIVE_PARENT", parent, PATH_CAP);
    if (!length || length >= PATH_CAP || hive) return ERROR_INVALID_PARAMETER;
    main_thread = GetCurrentThreadId();
    DWORD error = suite_hive_create(temporary, parent, &hive);
    if (error) return error;
    wcscpy_s(root, PATH_CAP, suite_hive_root(hive));
    HKEY key;
    error = RegCreateKeyExW(HKEY_CURRENT_USER, L"Software\\SimonTatham\\PuTTY", 0,
                            NULL, 0, KEY_READ | KEY_WRITE, NULL, &key, NULL);
    if (!error) {
        const wchar_t *seed = suite_hive_seed(hive);
        error = RegSetValueExW(key, L"RandSeedFile", 0, REG_SZ, (const BYTE *)seed,
                               (DWORD)((wcslen(seed) + 1) * sizeof(wchar_t)));
        DWORD closed = RegCloseKey(key);
        if (!error) error = closed;
    }
    if (!error) error = suite_hive_release_owner(hive);
    return error;
}
DWORD suite_terminal_claim_main(void)
{
    if (GetCurrentThreadId() != main_thread) return ERROR_INVALID_STATE;
    return suite_hive_claim_owner(hive);
}
void suite_terminal_core_created(void)
{
    if (GetCurrentThreadId() == main_thread || !suite_hive_ready(hive)) ExitProcess(92);
    created++;
}
void suite_terminal_core_destroyed(suite_seed_statistics statistics)
{
    if (GetCurrentThreadId() == main_thread || !suite_hive_ready(hive)) ExitProcess(92);
    destroyed++;
    totals.reads += statistics.reads; totals.writes += statistics.writes;
    totals.opens_read += statistics.opens_read; totals.opens_write += statistics.opens_write;
    totals.registry_accesses += statistics.registry_accesses;
    totals.bytes_read += statistics.bytes_read; totals.bytes_written += statistics.bytes_written;
}
DWORD suite_terminal_finish(int passed)
{
    if (!hive || GetCurrentThreadId() != main_thread) return ERROR_INVALID_STATE;
    if (!suite_hive_ready(hive)) {
        DWORD error = suite_hive_claim_owner(hive);
        if (error) return error;
    }
    DWORD error = ERROR_SUCCESS;
    if (passed) {
        HKEY key = NULL;
        DWORD count = 0;
        error = RegOpenKeyExW(suite_hive_key(hive), L"Software\\SimonTatham\\PuTTY\\SshHostKeys", 0, KEY_READ, &key);
        if (!error) error = RegQueryInfoKeyW(key, NULL, NULL, NULL, NULL, NULL, NULL,
                                           &count, NULL, NULL, NULL, NULL);
        if (key) { DWORD closed = RegCloseKey(key); if (!error) error = closed; }
        if (!error && (!count || created != 5 || destroyed != 5 ||
            !totals.reads || !totals.writes || totals.opens_read != 5 ||
            totals.opens_write != 10 || !totals.registry_accesses || !totals.bytes_written))
            error = ERROR_INVALID_DATA;
    }
    DWORD closed = suite_hive_close(hive);
    if (!closed) hive = NULL;
    if (!error) error = closed;
    if (!closed) {
        DWORD removed = suite_hive_cleanup(temporary, root);
        if (!error) error = removed;
    }
    if (!error && passed)
        printf("{\"schema\":1,\"kind\":\"terminal-hive\",\"passed\":true,"
               "\"continuous_mapping\":true,\"cleanup\":true,\"cores_created\":%u,"
               "\"cores_destroyed\":%u,\"seed_reads\":%u,\"seed_writes\":%u,"
               "\"registry_accesses\":%u,\"host_key_in_private_hive\":true}\n",
               created, destroyed, totals.reads, totals.writes, totals.registry_accesses);
    return error;
}
