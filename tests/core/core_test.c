#include "pg_core.h"
#include "pg_platform.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifndef _WIN32
#include <sys/stat.h>
#endif
#define REQUIRE(v) do { if (!(v)) { fprintf(stderr, "失败 %s:%d: %s (%d %s)\n", __FILE__, __LINE__, #v, e.code, e.detail); exit(1); } } while (0)
static pg_error e;
static const char *root;
static unsigned progress_events;
static void progress(void *context, unsigned done, unsigned total)
{ (void)context; REQUIRE(!total || done <= total); ++progress_events; }
static void path_for(char *path, size_t size, const char *name)
{ snprintf(path, size, "%s/%s", root, name); }
static void same_key(pg_key *a, pg_key *b)
{
    pg_key_info i, j; REQUIRE(pg_key_get_info(a, &i, &e)); REQUIRE(pg_key_get_info(b, &j, &e));
    REQUIRE(!strcmp(i.fingerprint_sha256, j.fingerprint_sha256));
    /* PEM 格式没有注释，因此比对公钥算法和数据而不依赖注释。 */
    char *ia = strchr(i.public_text, ' '), *ja = strchr(j.public_text, ' ');
    REQUIRE(i.ssh1 == j.ssh1 && i.bits == j.bits);
    if (!i.ssh1) { REQUIRE(ia && ja); char *ie = strchr(ia + 1, ' '), *je = strchr(ja + 1, ' ');
        size_t in = ie ? (size_t)(ie - i.public_text) : strlen(i.public_text);
        size_t jn = je ? (size_t)(je - j.public_text) : strlen(j.public_text);
        REQUIRE(in == jn && !memcmp(i.public_text, j.public_text, in)); }
    pg_key_info_free(&i); pg_key_info_free(&j);
}
static void roundtrip(pg_key *key, const char *name, pg_save_format format, const char *pass, pg_ppk_params *save)
{
    char path[2048]; path_for(path, sizeof(path), name);
    REQUIRE(pg_save(key, path, format, pass, save, &e));
#ifndef _WIN32
    struct stat st; REQUIRE(!stat(path, &st)); REQUIRE((st.st_mode & 077) == 0);
#endif
    pg_file_info file; REQUIRE(pg_probe_file(path, &file, &e));
    REQUIRE(file.encrypted == (pass && *pass));
    REQUIRE(file.foreign_format == (format >= PG_EXPORT_OPENSSH_AUTO)); pg_file_info_free(&file);
    pg_key *loaded = NULL;
    if (pass && *pass) { REQUIRE(!pg_load(path, "wrong-only-test", &loaded, &e)); REQUIRE(e.code == PG_ERR_PASSPHRASE && !loaded); }
    REQUIRE(pg_load(path, pass, &loaded, &e)); same_key(key, loaded); pg_key_free(loaded);
}
static void matrix(void)
{
    const pg_generate_params algorithms[] = {
        {PG_RSA,1024,PG_PRIME_PROBABLE,0}, {PG_DSA,1024,PG_PRIME_PROBABLE,0},
        {PG_ECDSA,256,PG_PRIME_PROBABLE,0}, {PG_ECDSA,384,PG_PRIME_PROBABLE,0},
        {PG_ECDSA,521,PG_PRIME_PROBABLE,0}, {PG_EDDSA,255,PG_PRIME_PROBABLE,0},
        {PG_EDDSA,448,PG_PRIME_PROBABLE,0}, {PG_RSA1,1024,PG_PRIME_PROBABLE,0}
    };
    pg_ppk_params save; pg_ppk_default_params(&save); save.memory_kib = 1024; save.passes_auto = 0; save.passes_or_ms = 2;
    const unsigned char entropy[] = {1,7,9,3,5};
    for (unsigned n = 0; n < sizeof(algorithms)/sizeof(*algorithms); ++n) {
        pg_key *key = NULL; REQUIRE(pg_generate(&algorithms[n], entropy, sizeof(entropy), progress, NULL, &key, &e));
        REQUIRE(pg_key_set_comment(key, "测试注释 空格", &e));
        pg_key_info info; REQUIRE(pg_key_get_info(key, &info, &e));
        REQUIRE(info.algorithm == algorithms[n].algorithm && info.bits == algorithms[n].bits);
        REQUIRE(info.can_add_certificate == (!info.ssh1 && n != 6) && !info.certificate);
        printf("验证 %s %u 位\n", info.algorithm_name, info.bits);
#ifdef PG_TESTING
        if (!info.ssh1) REQUIRE(pg_test_sign_verify(key));
#endif
        char name[128];
        snprintf(name, sizeof(name), "key-%u.ppk", n); roundtrip(key, name, PG_SAVE_PRIVATE, "", &save);
        snprintf(name, sizeof(name), "key-%u-encrypted.ppk", n); roundtrip(key, name, PG_SAVE_PRIVATE, "口令 test-only", &save);
        save.version = 2; snprintf(name, sizeof(name), "key-%u-v2.ppk", n); roundtrip(key, name, PG_SAVE_PRIVATE, "test-only", &save); save.version = 3;
        snprintf(name, sizeof(name), "key-%u.pub", n); char path[2048]; path_for(path,sizeof(path),name);
        REQUIRE(pg_save(key, path, PG_SAVE_PUBLIC, "", &save, &e));
        snprintf(name, sizeof(name), "key-%u.openssh.pub", n); path_for(path,sizeof(path),name);
        FILE *f = fopen(path, "w"); REQUIRE(f); REQUIRE(fprintf(f, "%s\n", info.public_text) > 0); REQUIRE(!fclose(f));
        snprintf(name, sizeof(name), "key-%u.fingerprints", n); path_for(path,sizeof(path),name);
        f = fopen(path, "w"); REQUIRE(f); fprintf(f, "%s\n%s\n", info.fingerprint_sha256, info.fingerprint_md5); fclose(f);
        if (info.can_export_openssh) {
            snprintf(name, sizeof(name), "key-%u-auto", n); roundtrip(key, name, PG_EXPORT_OPENSSH_AUTO, "", &save);
            snprintf(name, sizeof(name), "key-%u-new", n); roundtrip(key, name, PG_EXPORT_OPENSSH_NEW, "", &save);
            snprintf(name, sizeof(name), "key-%u-new-encrypted", n); roundtrip(key, name, PG_EXPORT_OPENSSH_NEW, "test-only", &save);
        }
        if (info.can_export_sshcom) {
            snprintf(name, sizeof(name), "key-%u-sshcom", n); roundtrip(key, name, PG_EXPORT_SSHCOM, "", &save);
            snprintf(name, sizeof(name), "key-%u-sshcom-encrypted", n); roundtrip(key, name, PG_EXPORT_SSHCOM, "test-only", &save);
        }
        if (n == 5) {
            roundtrip(key, "密钥 空格.ppk", PG_SAVE_PRIVATE, "测试 passphrase", &save);
            for (unsigned kdf = PG_ARGON2_ID; kdf <= PG_ARGON2_D; ++kdf) {
                save.argon2 = (pg_argon2)kdf; save.parallelism = 2; save.memory_kib = 2048;
                snprintf(name, sizeof(name), "argon2-%u.ppk", kdf); roundtrip(key,name,PG_SAVE_PRIVATE,"test-only",&save);
            }
            save.passes_auto = 1; save.passes_or_ms = 10; roundtrip(key,"argon2-auto.ppk",PG_SAVE_PRIVATE,"test-only",&save);
            pg_ppk_params bad = save; bad.parallelism = 0;
            REQUIRE(!pg_ppk_validate(&bad,"test-only",&e) && e.code == PG_ERR_ARGUMENT);
            REQUIRE(!pg_save(key, "/missing-directory/test.ppk", PG_SAVE_PRIVATE, "", &save, &e)); REQUIRE(e.code == PG_ERR_IO);
#ifndef _WIN32
            REQUIRE(!pg_save(key, "/dev/full", PG_SAVE_PUBLIC, "", &save, &e)); REQUIRE(e.code == PG_ERR_IO);
            REQUIRE(!pg_save(key, "/dev/full", PG_EXPORT_OPENSSH_NEW, "", &save, &e)); REQUIRE(e.code == PG_ERR_IO);
#endif
#ifdef PG_TESTING
            pg_test_random_failure(1);
            pg_key *blocked = NULL; REQUIRE(!pg_generate(&algorithms[n],NULL,0,NULL,NULL,&blocked,&e)); REQUIRE(e.code == PG_ERR_RANDOM && !blocked);
            REQUIRE(!pg_save(key, "unused.ppk", PG_SAVE_PRIVATE, "", &save, &e)); REQUIRE(e.code == PG_ERR_RANDOM);
            pg_test_random_failure(0);
#endif
            pg_ppk_default_params(&save); save.memory_kib = 1024; save.passes_auto = 0; save.passes_or_ms = 2;
        }
        pg_key_info_free(&info); pg_key_free(key);
    }
    REQUIRE(progress_events > 8);
    for (unsigned policy = PG_PRIME_PROBABLE; policy <= PG_PRIME_PROVEN_EVEN; ++policy) {
        for (unsigned strong = 0; strong <= 1; ++strong) {
            pg_generate_params p = {PG_RSA,512,(pg_prime_policy)policy,(int)strong}; pg_key *key = NULL;
            REQUIRE(pg_generate(&p,NULL,0,progress,NULL,&key,&e));
            char name[128]; snprintf(name,sizeof(name),"prime-%u-strong-%u.ppk",policy,strong);
            roundtrip(key,name,PG_SAVE_PRIVATE,"",&save); pg_key_free(key);
        }
        pg_generate_params dsa = {PG_DSA,512,(pg_prime_policy)policy,0}; pg_key *key = NULL;
        REQUIRE(pg_generate(&dsa,NULL,0,progress,NULL,&key,&e)); pg_key_free(key);
    }
    pg_generate_params invalid = {PG_EDDSA,256,PG_PRIME_PROBABLE,0}; pg_key *key = NULL;
    REQUIRE(!pg_generate(&invalid,NULL,0,NULL,NULL,&key,&e)); REQUIRE(e.code == PG_ERR_ARGUMENT && !key);
    char path[2048]; path_for(path,sizeof(path),"invalid.ppk"); FILE *f = fopen(path,"w"); REQUIRE(f); fputs("invalid",f); fclose(f);
    REQUIRE(!pg_load(path,"",&key,&e)); REQUIRE(e.code == PG_ERR_FORMAT && !key);
}
static void certificates(const char *key_path, const char *cert, const char *other_cert)
{
    pg_key *key = NULL; REQUIRE(pg_load(key_path,"",&key,&e)); pg_key_info before;
    REQUIRE(pg_key_get_info(key,&before,&e)); REQUIRE(!before.certificate);
    char badpath[2048]; path_for(badpath,sizeof(badpath),"invalid.ppk");
    REQUIRE(!pg_add_certificate(key,badpath,&e)); REQUIRE(e.code == PG_ERR_CERTIFICATE);
    REQUIRE(!pg_add_certificate(key,other_cert,&e)); REQUIRE(e.code == PG_ERR_CERT_MISMATCH);
    pg_key_info after; REQUIRE(pg_key_get_info(key,&after,&e)); REQUIRE(!after.certificate); pg_key_info_free(&after);
    REQUIRE(pg_add_certificate(key,cert,&e)); REQUIRE(pg_key_get_info(key,&after,&e)); REQUIRE(after.certificate);
    REQUIRE(strlen(after.certificate_info) > 50); REQUIRE(!strcmp(strrchr(before.fingerprint_sha256,' ') + 1,strrchr(after.fingerprint_sha256,' ') + 1));
    char path[2048]; path_for(path,sizeof(path),"certificate.ppk"); pg_ppk_params params; pg_ppk_default_params(&params);
    REQUIRE(pg_save(key,path,PG_SAVE_PRIVATE,"",&params,&e)); pg_key *copy = NULL; REQUIRE(pg_load(path,"",&copy,&e));
    pg_key_info copied; REQUIRE(pg_key_get_info(copy,&copied,&e)); REQUIRE(copied.certificate);
    pg_key_info_free(&copied); pg_key_free(copy);
    pg_save_format formats[] = {PG_EXPORT_OPENSSH_AUTO, PG_EXPORT_OPENSSH_NEW, PG_EXPORT_SSHCOM};
    for (unsigned i = 0; i < 3; ++i) {
        if (formats[i] == PG_EXPORT_SSHCOM && !after.can_export_sshcom) continue;
        char filename[64]; snprintf(filename,sizeof(filename),"certificate-export-%u",i); path_for(path,sizeof(path),filename);
        REQUIRE(pg_save(key,path,formats[i],"",&params,&e)); REQUIRE(pg_load(path,"",&copy,&e));
        REQUIRE(pg_key_get_info(copy,&copied,&e)); REQUIRE(!copied.certificate);
        REQUIRE(!strcmp(before.fingerprint_sha256,copied.fingerprint_sha256));
        pg_key_info_free(&copied); pg_key_free(copy);
    }
    path_for(path,sizeof(path),"certificate.pub"); REQUIRE(pg_save(key,path,PG_SAVE_PUBLIC,"",&params,&e));
    pg_key_info_free(&after);
    REQUIRE(pg_remove_certificate(key,&e)); REQUIRE(pg_key_get_info(key,&after,&e)); REQUIRE(!after.certificate);
    REQUIRE(!strcmp(before.public_text,after.public_text)); pg_key_info_free(&before); pg_key_info_free(&after); pg_key_free(key);
}
int main(int argc, char **argv)
{
    REQUIRE(argc >= 2); root = argv[1];
#ifdef PG_TESTING
    pg_test_random_failure(1); REQUIRE(!pg_core_init(&e)); REQUIRE(e.code == PG_ERR_RANDOM); pg_test_random_failure(0);
#endif
    REQUIRE(pg_core_init(&e)); REQUIRE(pg_entropy_bits() == 912);
    if (argc == 6 && !strcmp(argv[2],"--make-certificate")) {
#ifdef PG_TESTING
        pg_key *key = NULL, *ca = NULL; REQUIRE(pg_load(argv[3],"",&key,&e)); REQUIRE(pg_load(argv[4],"",&ca,&e));
        REQUIRE(pg_test_make_certificate(key,ca,argv[5])); pg_key_free(key); pg_key_free(ca);
#endif
    } else if (argc == 5) certificates(argv[2],argv[3],argv[4]); else matrix();
    pg_core_cleanup(); REQUIRE(pg_core_init(&e)); pg_core_cleanup(); return 0;
}
