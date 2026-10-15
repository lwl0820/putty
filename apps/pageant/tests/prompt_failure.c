#include "putty_runtime/core.h"
#include "putty.h"
#include "ssh.h"
#include "pageant.h"

/* 使用真实上游客户端 vtable 注入“第二次提示创建失败”，不替换签名实现。 */
typedef struct test_client {
    PageantClient pc;
    PageantClientDialogId *dialog;
    unsigned asks, response;
    bool reject_repeat;
} test_client;
typedef struct test_state { test_client a, b; } test_state;
static const char *filenames[2];
static test_state *state;
static void got_response(PageantClient *pc, PageantClientRequestId *id, ptrlen reply)
{
    (void)id;
    test_client *client = container_of(pc, test_client, pc);
    assert(reply.len);
    client->response = *(const unsigned char *)reply.ptr;
}
static bool ask(PageantClient *pc, PageantClientDialogId *dialog, const char *comment)
{
    (void)comment;
    test_client *client = container_of(pc, test_client, pc);
    client->asks++;
    if (client->reject_repeat && client->asks > 1) return false;
    client->dialog = dialog;
    return true;
}
static const PageantClientVtable client_vt = {NULL, got_response, ask};
void keylist_update(void) { }
static void *create(pr_core *core, const void *options, size_t size)
{
    (void)core; (void)options; (void)size;
    pageant_init();
    state = snew(test_state);
    memset(state, 0, sizeof(*state));
    state->a.pc.vt = state->b.pc.vt = &client_vt;
    state->a.pc.suppress_logging = state->b.pc.suppress_logging = true;
    state->a.reject_repeat = true;
    pageant_register_client(&state->a.pc);
    pageant_register_client(&state->b.pc);
    for (int i = 0; i < 2; i++) {
        Filename *file = filename_from_str(filenames[i]);
        char *error = NULL;
        assert(pageant_add_keyfile(file, NULL, &error, true) == PAGEANT_ACTION_OK);
        filename_free(file); sfree(error);
    }
    return state;
}
static pr_status command(void *ctx, const pr_command *cmd)
{ (void)ctx; (void)cmd; return PR_OK; }
static void destroy(void *ctx)
{
    test_state *s = ctx;
    pageant_unregister_client(&s->a.pc);
    pageant_unregister_client(&s->b.pc);
    pageant_delete_all();
    pageant_forget_passphrases();
    sfree(s);
}
static int owner(void *ctx) { (void)ctx; return 1; }
static void wake(void *ctx) { (void)ctx; }
static void timer(void *ctx, unsigned long when) { (void)ctx; (void)when; }
static void watch(void *ctx, int fd, int mask, uint64_t id)
{ (void)ctx; (void)fd; (void)mask; (void)id; }
static pr_status event(void *ctx, const pr_event *evt)
{ (void)ctx; (void)evt; return PR_OK; }
static void request(test_client *client, const char *path, PageantClientRequestId *id)
{
    Filename *file = filename_from_str(path);
    strbuf *blob = strbuf_new(), *packet = strbuf_new();
    const char *error = NULL;
    assert(ppk_loadpub_f(file, NULL, BinarySink_UPCAST(blob), NULL, &error));
    put_byte(packet, SSH2_AGENTC_SIGN_REQUEST);
    put_stringpl(packet, ptrlen_from_strbuf(blob));
    put_stringz(packet, "真实签名请求");
    put_uint32(packet, 0);
    pageant_handle_msg(&client->pc, id, ptrlen_from_strbuf(packet));
    strbuf_free(packet); strbuf_free(blob); filename_free(file);
}
int main(int argc, char **argv)
{
    if (argc != 3) return 2;
    filenames[0] = argv[1]; filenames[1] = argv[2];
    const pr_host host = {owner, wake, timer, watch, event};
    const pr_module module = {create, command, destroy, NULL};
    pr_core *core = NULL;
    assert(pr_core_create(&host, NULL, 1, &module, NULL, 0, &core) == PR_OK);
    PageantClientRequestId a, b;
    request(&state->a, filenames[0], &a);
    request(&state->b, filenames[1], &b);
    while (pr_core_callbacks(core, 64)) { }
    assert(state->a.dialog && state->a.asks == 1 && !state->b.asks);
    pageant_passphrase_request_success(state->a.dialog, PTRLEN_LITERAL("wrong"));
    while (pr_core_callbacks(core, 64)) { }
    assert(state->a.asks == 2 && state->a.response == SSH_AGENT_FAILURE);
    assert(state->b.dialog && state->b.asks == 1);
    pageant_passphrase_request_success(state->b.dialog, PTRLEN_LITERAL("test-passphrase"));
    while (pr_core_callbacks(core, 64)) { }
    assert(state->b.response == SSH2_AGENT_SIGN_RESPONSE);
    pr_core_destroy(core);
    puts("重复提示创建失败后，另一把密钥完成真实签名");
    return 0;
}
