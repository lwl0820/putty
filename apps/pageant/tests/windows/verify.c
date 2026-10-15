/* 只验测试向量签名；不创建客户端 PRNG 或读写设置。 */
#include "putty.h"
#include "ssh.h"
#include "security-api.h"
#include "core/pa_windows.h"
void *pa_test_stalled_pipe(void)
{
    PSECURITY_DESCRIPTOR descriptor = NULL;
    PACL acl = NULL;
    char *error = NULL;
    if (!got_advapi() || !make_private_security_descriptor(
            FILE_GENERIC_READ | FILE_GENERIC_WRITE, &descriptor, &acl, &error)) {
        sfree(error); return INVALID_HANDLE_VALUE;
    }
    SECURITY_ATTRIBUTES attributes = {sizeof(attributes), descriptor, FALSE};
    char *name = pa_windows_pipe_name();
    HANDLE pipe = CreateNamedPipeA(name, PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE,
        PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
        1, 4096, 4096, 0, &attributes);
    sfree(name); LocalFree(descriptor); LocalFree(acl);
    return pipe;
}
int pa_test_remote_query(void)
{
    strbuf *query = strbuf_new_for_agent_query();
    put_byte(query, SSH2_AGENTC_REQUEST_IDENTITIES);
    put_padding(query, 128 * 1024, 0x5a);
    void *response = NULL;
    int size = 0;
    agent_query_synchronous(query, &response, &size);
    const unsigned char expected[] = {0, 0, 0, 5, SSH2_AGENT_IDENTITIES_ANSWER, 0, 0, 0, 0};
    bool valid = size == sizeof(expected) && response && !memcmp(response, expected, sizeof(expected));
    strbuf_free(query); sfree(response);
    return valid;
}
int pa_test_verify(const void *blob, size_t blob_size, const void *signature,
                   size_t signature_size, const void *message, size_t message_size)
{
    ssh_key *key = ssh_key_new_pub(&ssh_ecdsa_ed25519, make_ptrlen(blob, blob_size));
    if (!key) return 0;
    bool valid = ssh_key_verify(key, make_ptrlen(signature, signature_size),
                               make_ptrlen(message, message_size));
    ssh_key_free(key);
    return valid;
}
