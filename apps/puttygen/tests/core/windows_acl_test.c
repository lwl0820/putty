#include "putty.h"
#include "pg_core.h"
#include "pg_platform.h"
#include <aclapi.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define REQUIRE(v) do { if (!(v)) { fprintf(stderr, "权限验证失败 %s:%d: %s (%lu, %d)\n", \
    __FILE__, __LINE__, #v, (unsigned long)GetLastError(), errno); exit(1); } } while (0)

static TOKEN_USER *user;
/* Win32 的 SID 和 ACL 缓冲区均按 DWORD 对齐。 */
static DWORD users_sid[(SECURITY_MAX_SID_SIZE + sizeof(DWORD) - 1) / sizeof(DWORD)];

static void wide_path(const char *path, wchar_t *wide, int count)
{ REQUIRE(MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, wide, count)); }

/* 检查实际磁盘 ACL，不能把 Win32 设置成功的返回值当成最终权限。 */
static void require_private_acl(const char *path)
{
    wchar_t wide[2048]; wide_path(path, wide, 2048);
    PSECURITY_DESCRIPTOR descriptor = NULL;
    PSID owner = NULL;
    PACL acl = NULL;
    SECURITY_DESCRIPTOR_CONTROL control;
    DWORD revision;
    REQUIRE(GetNamedSecurityInfoW(wide, SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
        &owner, NULL, &acl, NULL, &descriptor) == ERROR_SUCCESS);
    REQUIRE(owner && EqualSid(owner, user->User.Sid));
    REQUIRE(GetSecurityDescriptorControl(descriptor, &control, &revision));
    REQUIRE(control & SE_DACL_PROTECTED);
    REQUIRE(acl && acl->AceCount == 1);
    ACCESS_ALLOWED_ACE *ace = NULL;
    REQUIRE(GetAce(acl, 0, (void **)&ace));
    REQUIRE(ace->Header.AceType == ACCESS_ALLOWED_ACE_TYPE && ace->Header.AceFlags == 0);
    REQUIRE(ace->Mask == FILE_ALL_ACCESS && EqualSid(&ace->SidStart, user->User.Sid));
    LocalFree(descriptor);
}

static void require_inherited_users(const char *path)
{
    wchar_t wide[2048]; wide_path(path, wide, 2048);
    PSECURITY_DESCRIPTOR descriptor = NULL;
    PACL acl = NULL;
    int found = 0;
    REQUIRE(GetNamedSecurityInfoW(wide, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION,
        NULL, NULL, &acl, NULL, &descriptor) == ERROR_SUCCESS);
    REQUIRE(acl);
    for (DWORD i = 0; i < acl->AceCount; ++i) {
        ACCESS_ALLOWED_ACE *ace = NULL;
        REQUIRE(GetAce(acl, i, (void **)&ace));
        if (ace->Header.AceType == ACCESS_ALLOWED_ACE_TYPE &&
            (ace->Header.AceFlags & INHERITED_ACE) && EqualSid(&ace->SidStart, users_sid)) found = 1;
    }
    REQUIRE(found);
    LocalFree(descriptor);
}

static void write_fixture(const char *path, const char *text)
{
    wchar_t wide[2048]; wide_path(path, wide, 2048);
    /* 仅重建此测试的夹具，故意让它继承 BUILTIN\Users 读权限。 */
    REQUIRE(DeleteFileW(wide) || GetLastError() == ERROR_FILE_NOT_FOUND);
    FILE *file = _wfopen(wide, L"wb"); REQUIRE(file);
    REQUIRE(fwrite(text, 1, strlen(text), file) == strlen(text)); REQUIRE(!fclose(file));
    require_inherited_users(path);
}

static void require_content(const char *path, const char *text)
{
    wchar_t wide[2048]; wide_path(path, wide, 2048);
    FILE *file = _wfopen(wide, L"rb"); REQUIRE(file);
    char buffer[256]; size_t size = fread(buffer, 1, sizeof(buffer), file);
    REQUIRE(size == strlen(text) && !memcmp(buffer, text, size)); REQUIRE(!fclose(file));
}

int main(int argc, char **argv)
{
    REQUIRE(argc == 2);
    HANDLE token;
    DWORD size = 0;
    REQUIRE(OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token));
    REQUIRE(!GetTokenInformation(token, TokenUser, NULL, 0, &size));
    REQUIRE(GetLastError() == ERROR_INSUFFICIENT_BUFFER);
    user = malloc(size); REQUIRE(user);
    REQUIRE(GetTokenInformation(token, TokenUser, user, size, &size)); CloseHandle(token);
    size = sizeof(users_sid);
    REQUIRE(CreateWellKnownSid(WinBuiltinUsersSid, NULL, users_sid, &size));

    /* 只修改项目 .tmp 下独立测试目录的 ACL，不碰个人密钥及账户目录。 */
    char directory[2048]; REQUIRE(snprintf(directory, sizeof(directory), "%s/windows-acl", argv[1]) > 0);
    wchar_t wide[2048]; wide_path(directory, wide, 2048);
    DWORD acl_buffer[64]; PACL acl = (PACL)acl_buffer;
    REQUIRE(InitializeAcl(acl, sizeof(acl_buffer), ACL_REVISION));
    REQUIRE(AddAccessAllowedAceEx(acl, ACL_REVISION, OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE,
        FILE_ALL_ACCESS, user->User.Sid));
    REQUIRE(AddAccessAllowedAceEx(acl, ACL_REVISION, OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE,
        FILE_GENERIC_READ, users_sid));
    SECURITY_DESCRIPTOR descriptor;
    REQUIRE(InitializeSecurityDescriptor(&descriptor, SECURITY_DESCRIPTOR_REVISION));
    REQUIRE(SetSecurityDescriptorOwner(&descriptor, user->User.Sid, FALSE));
    REQUIRE(SetSecurityDescriptorDacl(&descriptor, TRUE, acl, FALSE));
    REQUIRE(SetSecurityDescriptorControl(&descriptor, SE_DACL_PROTECTED, SE_DACL_PROTECTED));
    SECURITY_ATTRIBUTES attributes = {sizeof(attributes), &descriptor, FALSE};
    REQUIRE(CreateDirectoryW(wide, &attributes) || GetLastError() == ERROR_ALREADY_EXISTS);
    REQUIRE(SetNamedSecurityInfoW(wide, SE_FILE_OBJECT,
        OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
        user->User.Sid, NULL, acl, NULL) == ERROR_SUCCESS);

    pg_error error;
    REQUIRE(pg_core_init(&error));
    pg_generate_params parameters = {PG_RSA, 1024, PG_PRIME_PROBABLE, 0};
    pg_key *key = NULL; REQUIRE(pg_generate(&parameters, NULL, 0, NULL, NULL, &key, &error));
    pg_ppk_params save; pg_ppk_default_params(&save);
    const pg_save_format formats[] = {PG_SAVE_PRIVATE, PG_EXPORT_OPENSSH_AUTO, PG_EXPORT_OPENSSH_NEW, PG_EXPORT_SSHCOM};
    for (unsigned i = 0; i < sizeof(formats) / sizeof(*formats); ++i) {
        char path[2048]; REQUIRE(snprintf(path, sizeof(path), "%s/密钥 空格-%u", directory, i) > 0);
        wide_path(path, wide, 2048);
        REQUIRE(DeleteFileW(wide) || GetLastError() == ERROR_FILE_NOT_FOUND);

        /* 新文件权限失败也不能写入私钥，已创建的空文件仍须具有安全 ACL。 */
        pg_test_private_security_failure(1);
        REQUIRE(!pg_save(key, path, formats[i], "", &save, &error));
        REQUIRE(error.code == PG_ERR_IO && errno == EACCES);
        pg_test_private_security_failure(0);
        require_content(path, ""); require_private_acl(path);
        REQUIRE(DeleteFileW(wide));
        REQUIRE(pg_save(key, path, formats[i], "", &save, &error)); require_private_acl(path);

        /* 覆盖宽权限旧文件必须收紧 owner/DACL，并仍可重新加载。 */
        write_fixture(path, "existing-test-content");
        REQUIRE(pg_save(key, path, formats[i], "", &save, &error)); require_private_acl(path);
        pg_key *loaded = NULL; REQUIRE(pg_load(path, "", &loaded, &error)); pg_key_free(loaded);

        /* 注入权限 API 失败，验证所有写出路径均不截断现有文件。 */
        write_fixture(path, "must-not-truncate-test-content");
        pg_test_private_security_failure(1);
        REQUIRE(!pg_save(key, path, formats[i], "", &save, &error));
        REQUIRE(error.code == PG_ERR_IO && errno == EACCES);
        pg_test_private_security_failure(0);
        require_content(path, "must-not-truncate-test-content"); require_inherited_users(path);
    }

    /* 确保 CRT 文本换行、二进制、追加、读写模式在句柄转换后保持语义。 */
    char modes_path[2048]; REQUIRE(snprintf(modes_path, sizeof(modes_path), "%s/modes", directory) > 0);
    Filename *filename = filename_from_str(modes_path); REQUIRE(filename);
    FILE *file = f_open(filename, "w", true); REQUIRE(file);
    REQUIRE(fputs("first\n", file) >= 0); REQUIRE(!fclose(file)); require_content(modes_path, "first\r\n");
    file = f_open(filename, "ab", true); REQUIRE(file);
    REQUIRE(fputs("second\n", file) >= 0); REQUIRE(!fclose(file)); require_content(modes_path, "first\r\nsecond\n");
    file = f_open(filename, "w+b", true); REQUIRE(file);
    REQUIRE(fputs("third\n", file) >= 0); REQUIRE(!fseek(file, 0, SEEK_SET));
    REQUIRE(fgetc(file) == 't'); REQUIRE(!fclose(file)); require_content(modes_path, "third\n");
    file = f_open(filename, "rb", true); REQUIRE(file); REQUIRE(fgetc(file) == 't'); REQUIRE(!fclose(file));
    require_private_acl(modes_path); filename_free(filename);

    /* 公钥仍沿用普通文件写入，不应被私钥失败 hook 或 ACL 规则影响。 */
    char public_path[2048]; REQUIRE(snprintf(public_path, sizeof(public_path), "%s/public.pub", directory) > 0);
    wide_path(public_path, wide, 2048); REQUIRE(DeleteFileW(wide) || GetLastError() == ERROR_FILE_NOT_FOUND);
    pg_test_private_security_failure(1);
    REQUIRE(pg_save(key, public_path, PG_SAVE_PUBLIC, "", &save, &error));
    pg_test_private_security_failure(0); require_inherited_users(public_path);

    pg_key_free(key); pg_core_cleanup(); free(user);
    puts("Windows 私钥新建、覆盖、权限失败、中文路径及文件模式验证通过");
    return 0;
}
