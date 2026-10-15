#include "putty.h"
#include "ssh.h"
#include "ssh/bpp.h"
#include "ssh/ppl.h"
#include "ssh/gss.h"
#include "core/pt_terminal.h"

/* 仅保留被测函数读到的宿主字段；包、编码、分配和函数体使用真实源码。 */
struct ssh2_userauth_state {
    ptrlen session_id;
    const char *username;
    PacketProtocolLayer ppl, *successor_layer;
    struct ssh_connection_shared_gss_state *shgss;
};
#include "userauth_mic_function.inc"

static int mode, packets, released;
static const char mic_data[] = "owned-mic";
/* 保留实际前端的链接闭包，但不创建核心或访问个人存储。 */
const pr_module *volatile terminal_link_contract = &pt_terminal_module;
#define CHECK(test) do { if (!(test)) { \
    fprintf(stderr, "MIC 回归失败，第 %d 行：%s\n", __LINE__, #test); \
    return 1; } } while (0)

static PktOut *new_packet(int type)
{
    PktOut *packet = ssh_new_packet();
    packet->type = type;
    ++packets;
    return packet;
}
static Ssh_gss_stat get_mic_fault(struct ssh_gss_library *library,
    Ssh_gss_ctx context, Ssh_gss_buf *input, Ssh_gss_buf *output)
{
    (void)library; (void)context;
    assert(input->length && input->value);
    if (mode == 1) return SSH_GSS_FAILURE; /* 与空 context／SSPI 查询失败一致：不写输出。 */
    if (mode == 2) return SSH_GSS_OK; /* 防止错误库的空成功被当成有效 MIC。 */
    output->length = sizeof(mic_data) - 1;
    output->value = dupstr(mic_data);
    return mode == 3 ? SSH_GSS_FAILURE : SSH_GSS_OK;
}
static Ssh_gss_stat free_mic_fault(struct ssh_gss_library *library, Ssh_gss_buf *buffer)
{
    (void)library;
    assert(buffer->value);
    ++released;
    smemclr(buffer->value, buffer->length);
    sfree(buffer->value);
    SSH_GSS_CLEAR_BUF(buffer);
    return SSH_GSS_OK;
}
int main(void)
{
    struct ssh_gss_library library = {.get_mic = get_mic_fault, .free_mic = free_mic_fault};
    struct ssh_connection_shared_gss_state shared = {.lib = &library};
    BinaryPacketProtocolVtable bpp_vt = {.new_pktout = new_packet};
    BinaryPacketProtocol bpp = {.vt = &bpp_vt};
    PacketProtocolLayerVtable successor_vt = {.name = "ssh-connection"};
    PacketProtocolLayer successor = {.vt = &successor_vt};
    struct ssh2_userauth_state state = {
        .session_id = PTRLEN_LITERAL("session"), .username = "suite",
        .ppl = {.bpp = &bpp}, .successor_layer = &successor, .shgss = &shared,
    };
    for (int method = 0; method < 2; ++method) {
        const char *name = method ? "gssapi-with-mic" : "gssapi-keyex";
        for (mode = 0; mode < 4; ++mode) {
            packets = released = 0;
            PktOut *packet = ssh2_userauth_gss_packet(&state, name);
            CHECK(released == (mode == 0 || mode == 3));
            CHECK(packets == (mode == 0));
            if (mode) { CHECK(packet == NULL); continue; }
            CHECK(packet != NULL);
            CHECK(packet->type == (method ? SSH2_MSG_USERAUTH_GSSAPI_MIC : SSH2_MSG_USERAUTH_REQUEST));
            BinarySource source[1]; BinarySource_BARE_INIT(source, packet->data, packet->length);
            if (!method) {
                CHECK(ptrlen_eq_string(get_string(source), "suite"));
                CHECK(ptrlen_eq_string(get_string(source), "ssh-connection"));
                CHECK(ptrlen_eq_string(get_string(source), name));
            }
            CHECK(ptrlen_eq_string(get_string(source), mic_data));
            CHECK(!get_err(source) && get_avail(source) == 0);
            ssh_free_pktout(packet);
        }
    }
    puts("GSS MIC：两种认证包的成功复制、无输出失败及失败缓冲释放通过");
    return 0;
}
