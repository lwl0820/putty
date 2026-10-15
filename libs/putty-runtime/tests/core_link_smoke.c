#include "putty.h"
#include "storage.h"

#ifndef _WINDOWS
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef PUTTY_RUNTIME_PROJECT_TMP
#error "必须指定项目 .tmp 路径，禁止测试使用个人配置"
#endif

/* 保留终端的真实链接闭包；本用例不把链接成功视为渲染验证。 */
void (*volatile core_link_terminal_free)(Terminal *) = term_free;

/* 以下均为产品前端所属策略，不替换存储、日志、SSH 或密码实现。 */
const bool share_can_be_downstream = true;
const bool share_can_be_upstream = true;
const bool buildinfo_gtk_relevant = false;

char *platform_default_s(const char *name)
{ (void)name; return NULL; }
bool platform_default_b(const char *name, bool value)
{ (void)name; return value; }
int platform_default_i(const char *name, int value)
{ (void)name; return value; }
FontSpec *platform_default_fontspec(const char *name)
{ (void)name; return fontspec_new_default(); }
Filename *platform_default_filename(const char *name)
{ (void)name; return filename_from_str(""); }
char *platform_get_x_display(void) { return NULL; }
char *x_get_default(const char *key) { (void)key; return NULL; }
void old_keyfile_warning(void) { }
void nonfatal(const char *format, ...) { (void)format; abort(); }
void modalfatalbox(const char *format, ...) { (void)format; abort(); }
void timer_change_notify(unsigned long next) { (void)next; }
uxsel_id *uxsel_input_add(int fd, int mask)
{ (void)fd; (void)mask; return NULL; }
void uxsel_input_remove(uxsel_id *id) { (void)id; }

static int event_count, error_count;

static void eventlog(LogPolicy *policy, const char *event)
{ (void)policy; (void)event; event_count++; }
static int askappend(LogPolicy *policy, Filename *filename,
                     void (*callback)(void *, int), void *context)
{
    (void)policy; (void)filename; (void)callback; (void)context;
    return 2;
}
static void logging_error(LogPolicy *policy, const char *event)
{ (void)policy; (void)event; error_count++; }
static bool verbose(LogPolicy *policy) { (void)policy; return true; }
static const LogPolicyVtable policy_vtable = {
    eventlog, askappend, logging_error, verbose
};

static bool matches_path(const char *root, const char *variable,
                         const char *relative)
{
    char *expected = dupcat(root, "/", relative);
    const char *value = getenv(variable);
    bool matches = value && !strcmp(value, expected);
    sfree(expected);
    return matches;
}

static bool isolated(const char *root)
{
    char *canonical = realpath(root, NULL);
    char *temporary = realpath(PUTTY_RUNTIME_PROJECT_TMP, NULL);
    bool valid = canonical && temporary && !strcmp(root, canonical) &&
        !strncmp(canonical, temporary, strlen(temporary)) &&
        canonical[strlen(temporary)] == '/';
    free(canonical);
    free(temporary);
    if (!valid)
        return false;

    /* 调用方先执行公共隔离探针；本进程在任何上游读取前再核对实际覆盖值。 */
    valid = matches_path(root, "PUTTYDIR", "putty") &&
        matches_path(root, "PUTTYSESSIONS", "putty/sessions") &&
        matches_path(root, "PUTTYSSHHOSTKEYS", "putty/sshhostkeys") &&
        matches_path(root, "PUTTYSSHHOSTCAS", "putty/sshhostcas") &&
        matches_path(root, "PUTTYRANDOMSEED", "putty/randomseed");
    const char *agent_variables[] = {
        "SSH_AUTH_SOCK", "SSH_AGENT_PID", "SSH_AGENT_LAUNCHER", "GPG_AGENT_INFO",
        "GNOME_KEYRING_CONTROL", "GNOME_KEYRING_PID", "SSH_ASKPASS",
        "SSH_ASKPASS_REQUIRE", "SSH_ASKPASS_PROMPT", "SSH_SK_HELPER",
        "SSH_PKCS11_HELPER"
    };
    for (size_t i = 0; i < lenof(agent_variables); i++)
        valid = valid && !getenv(agent_variables[i]);
    return valid;
}

int main(int argc, char **argv)
{
    if (argc != 2 || !isolated(argv[1])) {
        fputs("需要项目 .tmp 内已验证的隔离根及完整存储覆盖\n", stderr);
        return 1;
    }

    Conf *conf = conf_new(), *restored = conf_new();
    do_defaults(NULL, conf);
    conf_set_str(conf, CONF_host, "suite-review.example");
    conf_set_int(conf, CONF_port, 2227);
    char *error = save_settings("core-link-smoke", conf);
    assert(error == NULL);
    assert(load_settings("core-link-smoke", restored));
    assert(!strcmp(conf_get_str(restored, CONF_host), "suite-review.example"));
    assert(conf_get_int(restored, CONF_port) == 2227);

    char *log_path = dupcat(argv[1], "/logs/core-link.log");
    Filename *filename = filename_from_str(log_path);
    conf_set_filename(conf, CONF_logfilename, filename);
    filename_free(filename);
    conf_set_int(conf, CONF_logtype, LGTYP_ASCII);
    conf_set_bool(conf, CONF_logheader, false);
    LogPolicy policy = { &policy_vtable };
    LogContext *log = log_init(&policy, conf);
    const char *payload = "suite-log-\xe4\xb8\xad\xe6\x96\x87\n";
    for (const unsigned char *c = (const unsigned char *)payload; *c; c++)
        logtraffic(log, *c, LGTYP_ASCII);
    logevent(log, "core link smoke");
    logflush(log);
    log_free(log);
    assert(event_count >= 1 && error_count == 0);

    FILE *file = fopen(log_path, "rb");
    assert(file != NULL);
    char data[256] = {0};
    assert(fread(data, 1, sizeof(data) - 1, file) == strlen(payload));
    assert(fclose(file) == 0);
    assert(!strcmp(data, payload));
    sfree(log_path);
    conf_free(conf);
    conf_free(restored);
    return 0;
}
#else
int main(void)
{
    fputs("Windows 真实存储测试需要逐进程注册表隔离探针\n", stderr);
    return 77;
}
#endif
