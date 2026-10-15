#define SECURITY_WIN32
#include "putty.h"
#include <security.h>
#include "ssh/gss.h"

#include <assert.h>
#include <limits.h>
#include <stdlib.h>
#include <time.h>
#include <wchar.h>

/* 独立系统负例：不调用 acquire，不初始化网络、存储、随机源或 Qt。 */
#include "sspi_context_type.inc"
#include "sspi_time_macros.inc"

static INITIALIZE_SECURITY_CONTEXT_FN_A real_initialize;
static QUERY_CONTEXT_ATTRIBUTES_FN_A real_query;
static MAKE_SIGNATURE_FN real_make;
static VERIFY_SIGNATURE_FN real_verify;
static FREE_CONTEXT_BUFFER_FN real_free_buffer;
static FREE_CREDENTIALS_HANDLE_FN real_free_credentials;
static DELETE_SECURITY_CONTEXT_FN real_delete_context;
static QUERY_SECURITY_PACKAGE_INFO_FN_A real_package_info;
static unsigned initialize_calls, query_calls, make_calls, verify_calls;
static unsigned free_buffer_calls, free_credentials_calls, delete_context_calls;
static unsigned package_info_calls, conversion_calls, allocations, releases;

#ifdef _MSC_VER
#define SUITE_NOINLINE __declspec(noinline)
#else
#define SUITE_NOINLINE __attribute__((noinline))
#endif

/* 透传计数器保留参数、系统状态码和缓冲，不注入模拟返回或延迟。 */
SUITE_NOINLINE SECURITY_STATUS SEC_ENTRY p_InitializeSecurityContextA(
    PCredHandle credentials, PCtxtHandle context, SEC_CHAR *target,
    ULONG flags, ULONG reserved1, ULONG representation, PSecBufferDesc input,
    ULONG reserved2, PCtxtHandle next_context, PSecBufferDesc output,
    PULONG attributes, PTimeStamp expiration)
{
    assert(credentials && !SecIsValidHandle(credentials));
    assert(!context || !SecIsValidHandle(context));
    ++initialize_calls;
    return real_initialize(credentials, context, target, flags, reserved1,
        representation, input, reserved2, next_context, output, attributes, expiration);
}

SUITE_NOINLINE SECURITY_STATUS SEC_ENTRY p_QueryContextAttributesA(
    PCtxtHandle context, ULONG attribute, PVOID output)
{
    assert(context && !SecIsValidHandle(context));
    ++query_calls;
    return real_query(context, attribute, output);
}

SUITE_NOINLINE SECURITY_STATUS SEC_ENTRY p_MakeSignature(
    PCtxtHandle context, ULONG quality, PSecBufferDesc message, ULONG sequence)
{
    assert(context && !SecIsValidHandle(context));
    ++make_calls;
    return real_make(context, quality, message, sequence);
}

SUITE_NOINLINE SECURITY_STATUS SEC_ENTRY p_VerifySignature(
    PCtxtHandle context, PSecBufferDesc message, ULONG sequence, PULONG quality)
{
    assert(context && !SecIsValidHandle(context));
    ++verify_calls;
    return real_verify(context, message, sequence, quality);
}

static SECURITY_STATUS free_system_buffer(void *buffer)
{
    ++free_buffer_calls;
    return real_free_buffer(buffer);
}

SUITE_NOINLINE void *safemalloc(size_t count, size_t width, size_t extra)
{
    assert(!width || count <= (SIZE_MAX - extra) / width);
    void *memory = malloc(count * width + extra);
    assert(memory);
    ++allocations;
    return memory;
}

SUITE_NOINLINE void safefree(void *memory)
{
    if (memory) {
        ++releases;
        free(memory);
    }
}

#define localexp_to_exp_lifetime real_localexp_to_exp_lifetime
#include "sspi_time_function.inc"
#undef localexp_to_exp_lifetime
static void localexp_to_exp_lifetime(
    TimeStamp *expiration, time_t *expiry, unsigned long *lifetime)
{
    ++conversion_calls;
    real_localexp_to_exp_lifetime(expiration, expiry, lifetime);
}

#include "sspi_init_function.inc"
#include "sspi_mic_function.inc"

static struct {
    SECURITY_STATUS package, package_free, mic, make, verify, initialize;
    SECURITY_STATUS credentials_free, context_delete;
} statuses;

#define CHECK_ITERATION(test) do { if (!(test)) { \
    fprintf(stderr, "原生 SSPI 负例失败，第 %d 行：%s\n", __LINE__, #test); \
    return 0; } } while (0)

static int run_iteration(void)
{
    PSecPkgInfoA package_info = NULL;
    ++package_info_calls;
    statuses.package = real_package_info("Kerberos", &package_info);
    bool package_valid = statuses.package == SEC_E_OK && package_info &&
        package_info->cbMaxToken > 0 && package_info->cbMaxToken <= 16 * 1024 * 1024;
    statuses.package_free = package_info ? free_system_buffer(package_info) : SEC_E_INVALID_HANDLE;
    CHECK_ITERATION(package_valid && statuses.package_free == SEC_E_OK);

    winSsh_gss_ctx context;
    memset(&context, 0, sizeof(context));
    SecInvalidateHandle(&context.cred_handle);
    SecInvalidateHandle(&context.context);
    CHECK_ITERATION(!SecIsValidHandle(&context.cred_handle) && !SecIsValidHandle(&context.context));

    char message[] = "suite-sspi-invalid-context";
    unsigned char signature[64] = {0};
    Ssh_gss_buf input = {sizeof(message), message};
    Ssh_gss_buf mic = {99, (void *)(uintptr_t)1};
    statuses.mic = (SECURITY_STATUS)ssh_sspi_get_mic(NULL, &context, &input, &mic);
    bool mic_empty = !mic.value && !mic.length;
    if (mic.value)
        safefree(mic.value);
    CHECK_ITERATION(statuses.mic == SEC_E_INVALID_HANDLE && mic_empty);
    CHECK_ITERATION(!allocations && !releases);

    SecBuffer buffers[] = {
        {sizeof(message), SECBUFFER_DATA, message},
        {sizeof(signature), SECBUFFER_TOKEN, signature},
    };
    SecBufferDesc descriptor = {SECBUFFER_VERSION, 2, buffers};
    statuses.make = p_MakeSignature(&context.context, 0, &descriptor, 0);
    buffers[0] = (SecBuffer){sizeof(message), SECBUFFER_DATA, message};
    buffers[1] = (SecBuffer){sizeof(signature), SECBUFFER_TOKEN, signature};
    ULONG quality = 0;
    statuses.verify = p_VerifySignature(&context.context, &descriptor, 0, &quality);
    CHECK_ITERATION(statuses.make == SEC_E_INVALID_HANDLE && statuses.verify == SEC_E_INVALID_HANDLE);

    Ssh_gss_ctx opaque_context = &context;
    Ssh_gss_buf empty_input = {0, NULL}, output = {0, NULL};
    time_t expiry = (time_t)42;
    unsigned long lifetime = 42;
    unsigned conversions_before = conversion_calls;
    Ssh_gss_stat result = ssh_sspi_init_sec_context(NULL, &opaque_context,
        "host/suite.invalid", 0, &empty_input, &output, &expiry, &lifetime);
    statuses.initialize = (SECURITY_STATUS)context.maj_stat;
    bool unexpected_output = output.value != NULL || output.length != 0;
    SECURITY_STATUS output_free = output.value ? free_system_buffer(output.value) : SEC_E_OK;
    bool unexpected_context = SecIsValidHandle(&context.context);
    ++delete_context_calls;
    statuses.context_delete = real_delete_context(&context.context);
    ++free_credentials_calls;
    statuses.credentials_free = real_free_credentials(&context.cred_handle);
    CHECK_ITERATION(result == SSH_GSS_FAILURE && statuses.initialize == SEC_E_INVALID_HANDLE);
    CHECK_ITERATION(expiry == GSS_NO_EXPIRATION && lifetime == 0);
    CHECK_ITERATION(conversion_calls == conversions_before && !unexpected_output && !unexpected_context);
    CHECK_ITERATION(output_free == SEC_E_OK);
    CHECK_ITERATION(statuses.context_delete == SEC_E_INVALID_HANDLE &&
        statuses.credentials_free == SEC_E_INVALID_HANDLE);
    return 1;
}

static void print_json_string(const char *text)
{
    putchar('"');
    for (const unsigned char *p = (const unsigned char *)text; *p; ++p) {
        if (*p == '"' || *p == '\\') {
            putchar('\\');
            putchar(*p);
        } else if (*p < 32) {
            printf("\\u%04x", *p);
        } else {
            putchar(*p);
        }
    }
    putchar('"');
}

#define CHECK_MAIN(test) do { if (!(test)) { \
    fprintf(stderr, "原生 SSPI 宿主失败，第 %d 行：%s\n", __LINE__, #test); \
    goto cleanup; } } while (0)
#define LOAD_REAL(variable, type, name) do { \
    variable = (type)GetProcAddress(module, name); \
    CHECK_MAIN(variable != NULL); \
} while (0)

int main(void)
{
    int exit_code = 1;
    HMODULE module = NULL;
    wchar_t system_directory[MAX_PATH], requested_path[MAX_PATH], loaded_path[MAX_PATH];
    char path_utf8[MAX_PATH * 4];
    SYSTEM_INFO system_info;
    GetNativeSystemInfo(&system_info);
    DWORD count = GetSystemDirectoryW(system_directory, MAX_PATH);
    CHECK_MAIN(count > 0 && count < MAX_PATH - 13);
    CHECK_MAIN(swprintf(requested_path, MAX_PATH, L"%ls\\secur32.dll", system_directory) > 0);
    module = LoadLibraryExW(requested_path, NULL, LOAD_LIBRARY_SEARCH_SYSTEM32);
    CHECK_MAIN(module != NULL);
    count = GetModuleFileNameW(module, loaded_path, MAX_PATH);
    CHECK_MAIN(count > 0 && count < MAX_PATH);
    CHECK_MAIN(CompareStringOrdinal(requested_path, -1, loaded_path, -1, TRUE) == CSTR_EQUAL);
    CHECK_MAIN(WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, loaded_path, -1,
        path_utf8, sizeof(path_utf8), NULL, NULL) > 0);

    LOAD_REAL(real_initialize, INITIALIZE_SECURITY_CONTEXT_FN_A, "InitializeSecurityContextA");
    LOAD_REAL(real_query, QUERY_CONTEXT_ATTRIBUTES_FN_A, "QueryContextAttributesA");
    LOAD_REAL(real_make, MAKE_SIGNATURE_FN, "MakeSignature");
    LOAD_REAL(real_verify, VERIFY_SIGNATURE_FN, "VerifySignature");
    LOAD_REAL(real_free_buffer, FREE_CONTEXT_BUFFER_FN, "FreeContextBuffer");
    LOAD_REAL(real_free_credentials, FREE_CREDENTIALS_HANDLE_FN, "FreeCredentialsHandle");
    LOAD_REAL(real_delete_context, DELETE_SECURITY_CONTEXT_FN, "DeleteSecurityContext");
    /* 只确认正式绑定所需的第八个导出存在，绝不调用默认凭据入口。 */
    CHECK_MAIN(GetProcAddress(module, "AcquireCredentialsHandleA") != NULL);
    LOAD_REAL(real_package_info, QUERY_SECURITY_PACKAGE_INFO_FN_A, "QuerySecurityPackageInfoA");

    for (unsigned i = 0; i < 8; ++i)
        CHECK_MAIN(run_iteration());
    DWORD handles_before = 0, handles_after = 0;
    CHECK_MAIN(GetProcessHandleCount(GetCurrentProcess(), &handles_before));
    for (unsigned i = 0; i < 128; ++i)
        CHECK_MAIN(run_iteration());
    CHECK_MAIN(GetProcessHandleCount(GetCurrentProcess(), &handles_after));
    CHECK_MAIN(handles_after == handles_before);
    CHECK_MAIN(initialize_calls == 136 && query_calls == 136 && make_calls == 136 && verify_calls == 136);
    CHECK_MAIN(package_info_calls == 136 && free_buffer_calls == 136 &&
        free_credentials_calls == 136 && delete_context_calls == 136);
    CHECK_MAIN(!conversion_calls && !allocations && !releases);
    CHECK_MAIN(FreeLibrary(module));
    module = NULL;

    printf("{\"schema\":1,\"kind\":\"sspi-negative\",\"passed\":true,"
        "\"storage\":\"not-run\",\"prng\":\"not-run\","
        "\"checks\":[\"system-library\",\"eight-exports\",\"kerberos-package\","
        "\"mic-invalid-context\",\"make-invalid-context\",\"verify-invalid-context\","
        "\"init-invalid-credential\",\"failure-time-outputs\",\"no-default-credentials\","
        "\"failure-cleanup\",\"repeated-handle-balance\"],\"secur32_path\":");
    print_json_string(path_utf8);
    printf(",\"native_api\":true,\"kerberos_authentication_verified\":false,"
        "\"pointer_bits\":%u,\"processor_architecture\":%u,"
        "\"warmup_iterations\":8,\"measured_iterations\":128,"
        "\"handles_before\":%lu,\"handles_after\":%lu,"
        "\"statuses\":{\"package\":%ld,\"package_free\":%ld,\"mic\":%ld,"
        "\"make\":%ld,\"verify\":%ld,\"initialize\":%ld,\"credentials_free\":%ld,\"context_delete\":%ld},"
        "\"calls\":{\"acquire\":0,\"package\":%u,\"initialize\":%u,\"query\":%u,"
        "\"make\":%u,\"verify\":%u,\"free_buffer\":%u,\"free_credentials\":%u,"
        "\"delete_context\":%u,\"time_conversion\":%u,\"allocations\":%u,\"releases\":%u}}\n",
        (unsigned)(sizeof(void *) * CHAR_BIT), (unsigned)system_info.wProcessorArchitecture,
        (unsigned long)handles_before, (unsigned long)handles_after,
        (long)statuses.package, (long)statuses.package_free, (long)statuses.mic,
        (long)statuses.make, (long)statuses.verify, (long)statuses.initialize,
        (long)statuses.credentials_free, (long)statuses.context_delete,
        package_info_calls, initialize_calls, query_calls, make_calls, verify_calls,
        free_buffer_calls, free_credentials_calls, delete_context_calls, conversion_calls, allocations, releases);
    exit_code = 0;
cleanup:
    if (module && !FreeLibrary(module))
        exit_code = 1;
    return exit_code;
}
