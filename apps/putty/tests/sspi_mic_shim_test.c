#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Linux 上的窄 SSPI 类型／调用夹具；不代表 Windows 原生 API 已执行。 */
typedef int32_t Ssh_gss_stat;
typedef void *Ssh_gss_ctx;
typedef struct { size_t length; void *value; } Ssh_gss_buf;
struct ssh_gss_library { int unused; };
typedef struct { uint32_t cbMaxToken, cbMaxSignature, cbBlockSize, cbSecurityTrailer; } SecPkgContext_Sizes;
typedef struct { uint32_t cbBuffer, BufferType; void *pvBuffer; } SecBuffer;
typedef struct { uint32_t ulVersion, cBuffers; SecBuffer *pBuffers; } SecBufferDesc;
typedef struct { Ssh_gss_stat maj_stat; int context; } winSsh_gss_ctx;
#define SEC_E_OK 0
#define SSH_GSS_OK 0
#define SSH_GSS_FAILURE 6
#define SECPKG_ATTR_SIZES 0
#define SECBUFFER_DATA 1
#define SECBUFFER_TOKEN 2
#define SECBUFFER_VERSION 0
#define SSH_GSS_CLEAR_BUF(buffer) do { (buffer)->length = 0; (buffer)->value = NULL; } while (0)
static int mode, allocations, make_calls;
static void *allocate(size_t count) { ++allocations; void *p = malloc(count); assert(p); return p; }
static void release(void *pointer) { assert(pointer); --allocations; free(pointer); }
#define snewn(count, type) ((type *)allocate((count) * sizeof(type)))
#define sfree(pointer) release(pointer)
static Ssh_gss_stat p_QueryContextAttributesA(int *context, int attribute, SecPkgContext_Sizes *sizes)
{
    (void)context; (void)attribute;
    sizes->cbMaxSignature = mode == 2 ? 0 : 32;
    return mode == 1 ? -1 : SEC_E_OK;
}
static Ssh_gss_stat p_MakeSignature(int *context, int quality, SecBufferDesc *descriptor, int sequence)
{
    (void)context; (void)quality; (void)sequence; ++make_calls;
    assert(descriptor->cBuffers == 2 && descriptor->pBuffers[1].cbBuffer == 32);
    if (mode == 3) return -2;
    memset(descriptor->pBuffers[1].pvBuffer, 'm', 7);
    descriptor->pBuffers[1].cbBuffer = 7;
    return SEC_E_OK;
}
#include "sspi_mic_function.inc"
#define CHECK(test) do { if (!(test)) { \
    fprintf(stderr, "SSPI MIC 夹具失败，第 %d 行：%s\n", __LINE__, #test); \
    return 1; } } while (0)
int main(void)
{
    winSsh_gss_ctx context = {0};
    Ssh_gss_buf input = {4, "data"}, output = {99, (void *)(uintptr_t)1};
    CHECK(ssh_sspi_get_mic(NULL, NULL, &input, &output) != SSH_GSS_OK);
    CHECK(!output.length && !output.value);
    for (mode = 0; mode < 4; ++mode) {
        output.length = 99; output.value = (void *)(uintptr_t)1; make_calls = 0;
        Ssh_gss_stat result = ssh_sspi_get_mic(NULL, &context, &input, &output);
        CHECK(make_calls == (mode == 0 || mode == 3));
        if (mode) {
            CHECK(result != SSH_GSS_OK && !output.length && !output.value && !allocations);
        } else {
            CHECK(result == SSH_GSS_OK && output.length == 7 && allocations == 1);
            CHECK(!memcmp(output.value, "mmmmmmm", 7));
            release(output.value);
        }
    }
    puts("SSPI MIC：真实函数的空上下文、查询失败、零长度、签名失败及成功所有权通过（窄夹具）");
    return 0;
}
