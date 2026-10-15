#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <time.h>

/* 窄类型／调用夹具，只检验提取的正式函数，不代表原生 SSPI 已调用。 */
typedef int32_t SECURITY_STATUS;
typedef uint32_t ULONG;
typedef struct { uintptr_t lower, upper; } TestHandle;
typedef struct { uint32_t low; int32_t high; } TimeStamp;
typedef struct { ULONG cbBuffer, BufferType; void *pvBuffer; } SecBuffer;
typedef struct { ULONG ulVersion, cBuffers; SecBuffer *pBuffers; } SecBufferDesc;
typedef void *Ssh_gss_ctx;
typedef void *Ssh_gss_name;
typedef struct { size_t length; void *value; } Ssh_gss_buf;
typedef enum { SSH_GSS_OK, SSH_GSS_S_CONTINUE_NEEDED,
               SSH_GSS_FAILURE = 6 } Ssh_gss_stat;
struct ssh_gss_library { int unused; };
typedef struct {
    ULONG maj_stat, min_stat;
    TestHandle cred_handle, context;
    TestHandle *context_handle;
    TimeStamp expiry;
} winSsh_gss_ctx;

#define SEC_E_OK ((SECURITY_STATUS)0)
#define SEC_I_CONTINUE_NEEDED ((SECURITY_STATUS)0x00090312)
#define SEC_I_COMPLETE_NEEDED ((SECURITY_STATUS)0x00090313)
#define SEC_E_INVALID_HANDLE ((SECURITY_STATUS)(-2146893055))
#define SEC_E_NO_CREDENTIALS ((SECURITY_STATUS)(-2146893042))
#define SSH_GSS_S_COMPLETE SSH_GSS_OK
#define GSS_NO_EXPIRATION ((time_t)-1)
#define SECBUFFER_VERSION 0
#define SECBUFFER_TOKEN 2
#define SECURITY_NATIVE_DREP 16
#define ISC_REQ_DELEGATE 1
#define ISC_REQ_MUTUAL_AUTH 2
#define ISC_REQ_REPLAY_DETECT 4
#define ISC_REQ_CONFIDENTIALITY 16
#define ISC_REQ_ALLOCATE_MEMORY 256

static SECURITY_STATUS api_status;
static int api_calls, conversion_calls, delegate_expected;
static winSsh_gss_ctx *expected_context;
static TestHandle *expected_old_context;
static char input_bytes[] = "input", output_bytes[] = "output";

static SECURITY_STATUS p_InitializeSecurityContextA(
    TestHandle *credential, TestHandle *old_context, char *target,
    ULONG flags, ULONG reserved1, ULONG representation, SecBufferDesc *input,
    ULONG reserved2, TestHandle *new_context, SecBufferDesc *output,
    ULONG *attributes, TimeStamp *expiration)
{
    ++api_calls;
    assert(credential == &expected_context->cred_handle);
    assert(old_context == expected_old_context);
    assert(new_context == &expected_context->context);
    assert(!strcmp(target, "host/suite.invalid"));
    assert(!reserved1 && !reserved2 && representation == SECURITY_NATIVE_DREP);
    assert(flags == (ULONG)(ISC_REQ_MUTUAL_AUTH | ISC_REQ_REPLAY_DETECT |
        ISC_REQ_CONFIDENTIALITY | ISC_REQ_ALLOCATE_MEMORY |
        (delegate_expected ? ISC_REQ_DELEGATE : 0)));
    assert(input->ulVersion == SECBUFFER_VERSION && input->cBuffers == 1);
    assert(input->pBuffers[0].BufferType == SECBUFFER_TOKEN);
    assert(input->pBuffers[0].cbBuffer == sizeof(input_bytes));
    assert(input->pBuffers[0].pvBuffer == input_bytes);
    assert(output->ulVersion == SECBUFFER_VERSION && output->cBuffers == 1);
    assert(output->pBuffers[0].BufferType == SECBUFFER_TOKEN);
    assert(!output->pBuffers[0].cbBuffer && !output->pBuffers[0].pvBuffer);
    *attributes = 0;
    output->pBuffers[0].cbBuffer = sizeof(output_bytes);
    output->pBuffers[0].pvBuffer = output_bytes;
    if (api_status == SEC_E_OK || api_status == SEC_I_CONTINUE_NEEDED) {
        expiration->low = 12345;
        expiration->high = 6789;
    }
    /* 错误及本适配不接受的状态完全不写 expiration，暴露旧转换分支。 */
    return api_status;
}

static void localexp_to_exp_lifetime(
    TimeStamp *expiration, time_t *expiry, unsigned long *lifetime)
{
    ++conversion_calls;
    /* 先验证是否允许转换，再读取时间戳，旧函数即使未初始化也稳定失败。 */
    assert(api_status == SEC_E_OK || api_status == SEC_I_CONTINUE_NEEDED);
    assert(expiration->low == 12345 && expiration->high == 6789);
    if (expiry)
        *expiry = (time_t)1700000000;
    if (lifetime)
        *lifetime = 300;
}

#include "sspi_init_function.inc"

#define CHECK(test) do { if (!(test)) { \
    fprintf(stderr, "SSPI 初始化窄夹具失败，第 %d 行：%s\n", __LINE__, #test); \
    return 1; } } while (0)

int main(void)
{
    static const SECURITY_STATUS statuses[] = {
        SEC_E_OK, SEC_I_CONTINUE_NEEDED, SEC_E_INVALID_HANDLE,
        SEC_E_NO_CREDENTIALS, SEC_I_COMPLETE_NEEDED,
    };
    unsigned cases = 0;
    for (size_t status = 0; status < sizeof(statuses) / sizeof(statuses[0]); ++status) {
        for (unsigned outputs = 0; outputs < 4; ++outputs) {
            winSsh_gss_ctx context;
            memset(&context, 0, sizeof(context));
            context.context_handle = outputs & 1 ? &context.context : NULL;
            Ssh_gss_ctx opaque_context = &context;
            Ssh_gss_buf input = {sizeof(input_bytes), input_bytes}, output = {0, NULL};
            time_t expiry = (time_t)42;
            unsigned long lifetime = 42;
            api_status = statuses[status];
            api_calls = conversion_calls = 0;
            delegate_expected = (outputs & 2) != 0;
            expected_context = &context;
            expected_old_context = context.context_handle;
            Ssh_gss_stat result = ssh_sspi_init_sec_context(
                NULL, &opaque_context, "host/suite.invalid", delegate_expected,
                &input, &output, outputs & 1 ? &expiry : NULL,
                outputs & 2 ? &lifetime : NULL);
            int accepted = api_status == SEC_E_OK || api_status == SEC_I_CONTINUE_NEEDED;
            CHECK(api_calls == 1 && conversion_calls == accepted);
            CHECK(result == (api_status == SEC_E_OK ? SSH_GSS_S_COMPLETE :
                api_status == SEC_I_CONTINUE_NEEDED ? SSH_GSS_S_CONTINUE_NEEDED : SSH_GSS_FAILURE));
            CHECK(expiry == (outputs & 1 ? accepted ? (time_t)1700000000 : GSS_NO_EXPIRATION : (time_t)42));
            CHECK(lifetime == (outputs & 2 ? accepted ? 300ul : 0ul : 42ul));
            CHECK(opaque_context == &context && context.context_handle == &context.context);
            CHECK(context.maj_stat == (ULONG)api_status);
            CHECK(output.length == sizeof(output_bytes) && output.value == output_bytes);
            ++cases;
        }
    }
    printf("SSPI 初始化：%u 项错误时间输出、成功转换、可选输出及原有令牌语义通过（窄夹具）\n", cases);
    return 0;
}
