/* 每个核心先接收同一 hive 的操作权，再允许真实 Conf／PRNG 初始化。 */
/* PuTTY 平台头必须先建立 Winsock2 声明，随后才能引入 hive 的 windows.h。 */
#include "putty.h"
#include "hive_bootstrap.h"
#include "seed_guard.h"
#include "core/pt_terminal.h"
extern const pr_module pt_terminal_real_module;

static void *guarded_create(pr_core *core, const void *options, size_t size)
{
    suite_private_hive *hive = suite_terminal_hive();
    if (suite_hive_claim_owner(hive) || suite_seed_guard_bind(hive)) ExitProcess(90);
    void *context = pt_terminal_real_module.create(core, options, size);
    if (!context) {
        suite_seed_guard_unbind();
        if (suite_hive_release_owner(hive)) ExitProcess(90);
        return NULL;
    }
    suite_terminal_core_created();
    return context;
}
static pr_status guarded_command(void *context, const pr_command *command)
{ return pt_terminal_real_module.command(context, command); }
static void guarded_drained(void *context)
{ pt_terminal_real_module.drained(context); }
static void guarded_destroy(void *context)
{
    pt_terminal_real_module.destroy(context);
    /* 正式 random_unref 在最后一引用时保存种子并释放 PRNG／其计时器。 */
    if (random_active != 0) ExitProcess(91);
    suite_terminal_core_destroyed(suite_seed_guard_statistics());
    suite_seed_guard_unbind();
    if (suite_hive_release_owner(suite_terminal_hive())) ExitProcess(90);
}
const pr_module pt_terminal_module = {guarded_create, guarded_command, guarded_destroy, guarded_drained};
