#include "pg_core.h"
#include "pg_platform.h"
#include "putty.h"
#include "ssh.h"
#include "sshkeygen.h"
#include <limits.h>
#include <math.h>
#include <time.h>

struct pg_key { int ssh1; RSAKey *rsa1; ssh2_userkey *ssh2; };
static int initialized;
static int fail(pg_error *e, pg_error_code code, const char *detail)
{
    if (e) { e->code = code; snprintf(e->detail, sizeof(e->detail), "%s", detail ? detail : ""); }
    return 0;
}
static int ok(pg_error *e) { if (e) { e->code = PG_OK; e->detail[0] = 0; } return 1; }
static int fresh_random(pg_error *e)
{
    unsigned char seed[128];
    if (!initialized) return fail(e, PG_ERR_RANDOM, "Core is not initialized");
    if (!pg_platform_random(seed, sizeof(seed))) {
        smemclr(seed, sizeof(seed)); return fail(e, PG_ERR_RANDOM, "System random source failed");
    }
    random_reseed(make_ptrlen(seed, sizeof(seed)));
    smemclr(seed, sizeof(seed));
    return 1;
}
int pg_core_init(pg_error *e)
{
    if (initialized) return ok(e);
    if (!pg_platform_seed()) return fail(e, PG_ERR_RANDOM, "System random source failed");
    random_setup_special(); initialized = 1;
    return ok(e);
}
unsigned pg_entropy_bits(void) { return initialized ? (unsigned)random_seed_bits() : 0; }
void pg_core_cleanup(void)
{ if (initialized) { random_clear(); initialized = 0; } }
void pg_secure_clear(void *data, size_t size) { if (data && size) smemclr(data, size); }

static ppk_save_parameters ppk_params(const pg_ppk_params *p)
{
    ppk_save_parameters result = ppk_save_default_parameters;
    if (p) {
        result.fmt_version = p->version;
        result.argon2_flavour = p->argon2 == PG_ARGON2_D ? Argon2d : p->argon2 == PG_ARGON2_I ? Argon2i : Argon2id;
        result.argon2_mem = p->memory_kib;
        result.argon2_passes_auto = p->passes_auto != 0;
        result.argon2_passes = p->passes_or_ms;
        result.argon2_parallelism = p->parallelism;
    }
    return result;
}
void pg_ppk_default_params(pg_ppk_params *p)
{
    const ppk_save_parameters *d = &ppk_save_default_parameters;
    if (!p) return;
    p->version = d->fmt_version; p->argon2 = PG_ARGON2_ID; p->memory_kib = d->argon2_mem;
    p->passes_auto = d->argon2_passes_auto; p->passes_or_ms = d->argon2_milliseconds;
    p->parallelism = d->argon2_parallelism;
}
int pg_ppk_validate(const pg_ppk_params *p, const char *pass, pg_error *e)
{
    if (!p || (p->version != 2 && p->version != 3) || p->argon2 > PG_ARGON2_D || p->argon2 < PG_ARGON2_ID)
        return fail(e, PG_ERR_ARGUMENT, "Invalid PPK parameters");
    ppk_save_parameters options = ppk_params(p);
    char *bad = ppk_params_bad(&options, pass && *pass, pass ? strlen(pass) : 0);
    if (bad) { fail(e, PG_ERR_ARGUMENT, bad); sfree(bad); return 0; }
    return ok(e);
}

/* 按上游阶段成本与实际尝试次数投影进度，概率阶段只提供估计值。 */
struct pg_progress {
    ProgressReceiver receiver;
    pg_progress_fn callback; void *context;
    struct { double cost, probability, done; unsigned attempts; } phases[64];
    unsigned count, current;
    double total;
};
static void emit_progress(struct pg_progress *p)
{
    double done = 0;
    for (unsigned i = 0; i < p->count; ++i) done += p->phases[i].cost * p->phases[i].done;
    if (p->callback) p->callback(p->context, p->total > 0 ? (unsigned)(999 * done / p->total) : 0,
                               p->total > 0 ? 1000 : 0);
}
static ProgressPhase add_linear(ProgressReceiver *r, double cost)
{
    struct pg_progress *p = container_of(r, struct pg_progress, receiver);
    ProgressPhase phase; phase.n = p->count;
    assert(p->count < 64); p->phases[p->count++].cost = cost; p->total += cost;
    return phase;
}
static ProgressPhase add_probable(ProgressReceiver *r, double cost, double prob)
{
    ProgressPhase phase = add_linear(r, cost / prob);
    struct pg_progress *p = container_of(r, struct pg_progress, receiver);
    p->phases[phase.n].probability = prob; return phase;
}
static void ready(ProgressReceiver *r) { emit_progress(container_of(r, struct pg_progress, receiver)); }
static void start_phase(ProgressReceiver *r, ProgressPhase phase)
{ struct pg_progress *p = container_of(r, struct pg_progress, receiver); p->current = phase.n; emit_progress(p); }
static void report(ProgressReceiver *r, double fraction)
{
    struct pg_progress *p = container_of(r, struct pg_progress, receiver);
    p->phases[p->current].done = fraction; emit_progress(p);
}
static void report_attempt(ProgressReceiver *r)
{
    struct pg_progress *p = container_of(r, struct pg_progress, receiver);
    unsigned i = p->current;
    p->phases[i].done = 1 - pow(1 - p->phases[i].probability, ++p->phases[i].attempts);
    emit_progress(p);
}
static void report_complete(ProgressReceiver *r)
{ struct pg_progress *p = container_of(r, struct pg_progress, receiver); p->phases[p->current].done = 1; emit_progress(p); }
static const ProgressReceiverVtable progress_vtable = {
    add_linear, add_probable, ready, start_phase, report, report_attempt, report_complete
};

int pg_generate(const pg_generate_params *params, const void *entropy, size_t size,
    pg_progress_fn callback, void *context, pg_key **result, pg_error *e)
{
    if (result) *result = NULL;
    if (!params || !result || params->algorithm < PG_RSA || params->algorithm > PG_RSA1 ||
        params->prime_policy < PG_PRIME_PROBABLE || params->prime_policy > PG_PRIME_PROVEN_EVEN ||
        params->bits > INT_MAX || (size && !entropy)) return fail(e, PG_ERR_ARGUMENT, "Invalid generation parameters");
    if ((params->algorithm == PG_RSA || params->algorithm == PG_RSA1 || params->algorithm == PG_DSA) && params->bits < 256)
        return fail(e, PG_ERR_ARGUMENT, "Key must have at least 256 bits");
    if (params->algorithm == PG_ECDSA && params->bits != 256 && params->bits != 384 && params->bits != 521)
        return fail(e, PG_ERR_ARGUMENT, "Unsupported ECDSA curve");
    if (params->algorithm == PG_EDDSA && params->bits != 255 && params->bits != 448)
        return fail(e, PG_ERR_ARGUMENT, "Unsupported EdDSA curve");
    if (!fresh_random(e)) return 0;
    if (size) random_reseed(make_ptrlen(entropy, size));
    struct pg_progress progress = { 0 };
    progress.receiver.vt = &progress_vtable; progress.callback = callback; progress.context = context;
    const PrimeGenerationPolicy *policies[] = { &primegen_probabilistic, &primegen_provable_maurer_simple, &primegen_provable_maurer_complex };
    PrimeGenerationContext *pgc = primegen_new_context(policies[params->prime_policy]);
    ssh_key *key = NULL; RSAKey *rsa = NULL;
    if (params->algorithm == PG_RSA || params->algorithm == PG_RSA1) {
        rsa = snew(RSAKey); memset(rsa, 0, sizeof(*rsa));
        rsa_generate(rsa, params->bits, params->strong_rsa != 0, pgc, &progress.receiver); key = &rsa->sshk;
    } else if (params->algorithm == PG_DSA) {
        struct dsa_key *dsa = snew(struct dsa_key); memset(dsa, 0, sizeof(*dsa));
        dsa_generate(dsa, params->bits, pgc, &progress.receiver); key = &dsa->sshk;
    } else if (params->algorithm == PG_ECDSA) {
        struct ecdsa_key *ec = snew(struct ecdsa_key); memset(ec, 0, sizeof(*ec));
        ecdsa_generate(ec, params->bits); key = &ec->sshk;
    } else {
        struct eddsa_key *ed = snew(struct eddsa_key); memset(ed, 0, sizeof(*ed));
        eddsa_generate(ed, params->bits); key = &ed->sshk;
    }
    primegen_free_context(pgc);
    pg_key *out = snew(pg_key); memset(out, 0, sizeof(*out));
    char comment[64]; struct tm date = ltime();
    const char *format = params->algorithm == PG_DSA ? "dsa-key-%Y%m%d" :
        params->algorithm == PG_ECDSA ? "ecdsa-key-%Y%m%d" : params->algorithm == PG_EDDSA ? "eddsa-key-%Y%m%d" : "rsa-key-%Y%m%d";
    strftime(comment, sizeof(comment), format, &date);
    out->ssh1 = params->algorithm == PG_RSA1;
    if (out->ssh1) { out->rsa1 = rsa; rsa->comment = dupstr(comment); }
    else { out->ssh2 = snew(ssh2_userkey); out->ssh2->key = key; out->ssh2->comment = dupstr(comment); }
    *result = out;
    if (callback) callback(context, 1000, 1000);
    return ok(e);
}
void pg_key_free(pg_key *key)
{
    if (!key) return;
    if (key->ssh1) { freersakey(key->rsa1); sfree(key->rsa1); }
    else { ssh_key_free(key->ssh2->key); sfree(key->ssh2->comment); sfree(key->ssh2); }
    smemclr(key, sizeof(*key)); sfree(key);
}
static LoadedFile *read_key(const char *path, Filename **filename, int *type, pg_error *e)
{
    if (!path || !*path) { fail(e, PG_ERR_ARGUMENT, "Empty file path"); return NULL; }
    *filename = filename_from_str(path);
    if (!*filename) { fail(e, PG_ERR_ARGUMENT, "Invalid UTF-8 file path"); return NULL; }
    const char *detail = NULL;
    LoadedFile *lf = lf_load_keyfile(*filename, &detail);
    if (!lf) { filename_free(*filename); *filename = NULL; fail(e, PG_ERR_IO, detail); return NULL; }
    *type = key_type_s(BinarySource_UPCAST(lf)); BinarySource_REWIND(lf);
    if (*type != SSH_KEYTYPE_SSH1 && *type != SSH_KEYTYPE_SSH2 && !import_possible(*type)) {
        fail(e, PG_ERR_FORMAT, key_type_to_str(*type)); lf_free(lf); filename_free(*filename); *filename = NULL; return NULL;
    }
    return lf;
}
int pg_probe_file(const char *path, pg_file_info *info, pg_error *e)
{
    if (!info) return fail(e, PG_ERR_ARGUMENT, "Missing result");
    memset(info, 0, sizeof(*info));
    Filename *fn = NULL; int type;
    LoadedFile *lf = read_key(path, &fn, &type, e); if (!lf) return 0;
    info->obsolete_format = lf->len >= 21 && !memcmp(lf->data, "PuTTY-User-Key-File-1:", 21);
    if (type == SSH_KEYTYPE_SSH1) info->encrypted = rsa1_encrypted_s(BinarySource_UPCAST(lf), &info->comment);
    else if (type == SSH_KEYTYPE_SSH2) info->encrypted = ppk_encrypted_s(BinarySource_UPCAST(lf), &info->comment);
    else { info->foreign_format = 1; info->encrypted = import_encrypted_s(fn, BinarySource_UPCAST(lf), type, &info->comment); }
    if (!info->comment) info->comment = dupstr("");
    info->format_name = dupstr(key_type_to_str(type));
    lf_free(lf); filename_free(fn); return ok(e);
}
void pg_file_info_free(pg_file_info *info)
{ if (info) { sfree(info->comment); sfree(info->format_name); memset(info, 0, sizeof(*info)); } }
int pg_load(const char *path, const char *passphrase, pg_key **result, pg_error *e)
{
    if (result) *result = NULL;
    if (!result) return fail(e, PG_ERR_ARGUMENT, "Missing result");
    Filename *fn = NULL; int type;
    LoadedFile *lf = read_key(path, &fn, &type, e); if (!lf) return 0;
    pg_key *out = snew(pg_key); memset(out, 0, sizeof(*out));
    char *pass = dupstr(passphrase ? passphrase : "");
    const char *detail = NULL; int status;
    if (type == SSH_KEYTYPE_SSH1) {
        out->ssh1 = 1; out->rsa1 = snew(RSAKey); memset(out->rsa1, 0, sizeof(*out->rsa1));
        status = rsa1_load_s(BinarySource_UPCAST(lf), out->rsa1, pass, &detail);
    } else {
        out->ssh2 = type == SSH_KEYTYPE_SSH2 ? ppk_load_s(BinarySource_UPCAST(lf), pass, &detail) :
            import_ssh2_s(BinarySource_UPCAST(lf), type, pass, &detail);
        status = out->ssh2 == SSH2_WRONG_PASSPHRASE ? -1 : out->ssh2 ? 1 : 0;
        if (!status && type == SSH_KEYTYPE_OPENSSH_NEW && detail && !strcmp(detail, "decryption check failed")) {
            BinarySource_REWIND(lf); char *comment = NULL;
            if (import_encrypted_s(fn, BinarySource_UPCAST(lf), type, &comment)) status = -1;
            sfree(comment);
        }
    }
    burnstr(pass); lf_free(lf); filename_free(fn);
    if (status != 1) {
        /* 上游失败接口没有返回可释放密钥，不调用正常密钥销毁路径。 */
        if (out->ssh1) { freersakey(out->rsa1); sfree(out->rsa1); }
        sfree(out); return fail(e, status == -1 ? PG_ERR_PASSPHRASE : PG_ERR_FORMAT, detail);
    }
    *result = out; return ok(e);
}

int pg_key_set_comment(pg_key *key, const char *comment, pg_error *e)
{
    if (!key || !comment || strchr(comment, '\n') || strchr(comment, '\r')) return fail(e, PG_ERR_ARGUMENT, "Invalid comment");
    char **old = key->ssh1 ? &key->rsa1->comment : &key->ssh2->comment;
    char *copy = dupstr(comment); sfree(*old); *old = copy; return ok(e);
}
int pg_key_get_info(const pg_key *key, pg_key_info *info, pg_error *e)
{
    if (!key || !info) return fail(e, PG_ERR_ARGUMENT, "Missing key");
    memset(info, 0, sizeof(*info));
    info->ssh1 = key->ssh1;
    if (key->ssh1) {
        info->algorithm = PG_RSA1; info->bits = key->rsa1->bits;
        info->algorithm_name = dupstr("SSH-1 RSA"); info->comment = dupstr(key->rsa1->comment ? key->rsa1->comment : "");
        info->public_text = ssh1_pubkey_str(key->rsa1);
        char *saved = key->rsa1->comment; key->rsa1->comment = NULL;
        info->fingerprint_md5 = rsa_ssh1_fingerprint(key->rsa1); key->rsa1->comment = saved;
        info->fingerprint_sha256 = dupstr(info->fingerprint_md5);
        info->certificate_info = dupstr(""); return ok(e);
    }
    ssh_key *k = key->ssh2->key;
    const ssh_keyalg *alg = ssh_key_alg(k), *basealg = ssh_key_alg(ssh_key_base_key(k));
    const char *name = basealg->ssh_id;
    info->algorithm = strstr(name, "rsa") ? PG_RSA : strstr(name, "dss") ? PG_DSA : strstr(name, "ecdsa") ? PG_ECDSA : PG_EDDSA;
    strbuf *blob = strbuf_new(); ssh_key_public_blob(ssh_key_base_key(k), BinarySink_UPCAST(blob));
    info->bits = ssh_key_public_bits(basealg, ptrlen_from_strbuf(blob)); strbuf_free(blob);
    /* 上游 Ed25519 的公开位数是 255，Ed448 为 448。 */
    info->algorithm_name = dupstr(alg->ssh_id); info->comment = dupstr(key->ssh2->comment ? key->ssh2->comment : "");
    info->public_text = ssh2_pubkey_openssh_str(key->ssh2);
    info->fingerprint_sha256 = ssh2_fingerprint(k, SSH_FPTYPE_SHA256);
    info->fingerprint_md5 = ssh2_fingerprint(k, SSH_FPTYPE_MD5);
    info->certificate = alg->is_certificate;
    for (size_t i = 0; i < n_keyalgs; ++i)
        if (all_keyalgs[i]->base_alg == basealg) info->can_add_certificate = 1;
    info->can_export_openssh = 1; info->can_export_sshcom = alg == &ssh_rsa || alg == &ssh_dsa;
    strbuf *cert = strbuf_new();
    if (info->certificate) {
        SeatDialogText *text = ssh_key_cert_info(k);
        if (text) {
            for (size_t i = 0; i < text->nitems; ++i) {
                if (text->items[i].type == SDT_MORE_INFO_KEY) put_fmt(cert, "%s: ", text->items[i].text);
                else put_fmt(cert, "%s\n", text->items[i].text);
            }
            seat_dialog_text_free(text);
        }
    }
    info->certificate_info = strbuf_to_str(cert); return ok(e);
}
void pg_key_info_free(pg_key_info *info)
{
    if (!info) return;
    sfree(info->algorithm_name); sfree(info->comment); sfree(info->public_text);
    sfree(info->fingerprint_sha256); sfree(info->fingerprint_md5); sfree(info->certificate_info);
    memset(info, 0, sizeof(*info));
}

int pg_save(pg_key *key, const char *path, pg_save_format format,
    const char *passphrase, const pg_ppk_params *options, pg_error *e)
{
    if (!key || !path || !*path || format < PG_SAVE_PRIVATE || format > PG_EXPORT_SSHCOM)
        return fail(e, PG_ERR_ARGUMENT, "Invalid save request");
    if (key->ssh1 && format >= PG_EXPORT_OPENSSH_AUTO) return fail(e, PG_ERR_UNSUPPORTED, "SSH-1 cannot be exported to SSH-2 formats");
    if (!key->ssh1 && format == PG_SAVE_PRIVATE && options && !pg_ppk_validate(options, passphrase, e)) return 0;
    if (format != PG_SAVE_PUBLIC && !fresh_random(e)) return 0;
    Filename *fn = filename_from_str(path);
    if (!fn) return fail(e, PG_ERR_ARGUMENT, "Invalid UTF-8 file path");
    char *pass = passphrase && *passphrase ? dupstr(passphrase) : NULL;
    int saved = 0;
    if (format == PG_SAVE_PUBLIC) {
        FILE *file = f_open(fn, "w", false);
        if (file) {
            if (key->ssh1) ssh1_write_pubkey(file, key->rsa1);
            else {
                strbuf *pub = strbuf_new(); ssh_key_public_blob(key->ssh2->key, BinarySink_UPCAST(pub));
                ssh2_write_pubkey(file, key->ssh2->comment, pub->u, pub->len, SSH_KEYTYPE_SSH2_PUBLIC_RFC4716); strbuf_free(pub);
            }
            saved = !ferror(file); if (fclose(file) != 0) saved = 0;
        }
    } else if (format == PG_SAVE_PRIVATE) {
        if (key->ssh1) saved = rsa1_save_f(fn, key->rsa1, pass);
        else { ppk_save_parameters params = ppk_params(options); saved = ppk_save_f(fn, key->ssh2, pass, &params); }
    } else {
        if (format == PG_EXPORT_SSHCOM) {
            const ssh_keyalg *alg = ssh_key_alg(key->ssh2->key);
            if (alg != &ssh_rsa && alg != &ssh_dsa) {
                burnstr(pass); filename_free(fn); return fail(e, PG_ERR_UNSUPPORTED, "ssh.com supports uncertified RSA and DSA only");
            }
        }
        int type = format == PG_EXPORT_OPENSSH_AUTO ? SSH_KEYTYPE_OPENSSH_AUTO :
            format == PG_EXPORT_OPENSSH_NEW ? SSH_KEYTYPE_OPENSSH_NEW : SSH_KEYTYPE_SSHCOM;
        saved = export_ssh2(fn, type, key->ssh2, pass);
    }
    burnstr(pass); filename_free(fn);
    return saved ? ok(e) : fail(e, PG_ERR_IO, "Could not write key file");
}
int pg_add_certificate(pg_key *key, const char *path, pg_error *e)
{
    if (!key || key->ssh1 || !path || !*path) return fail(e, PG_ERR_UNSUPPORTED, "Certificate requires an SSH-2 key");
    Filename *fn = filename_from_str(path); if (!fn) return fail(e, PG_ERR_ARGUMENT, "Invalid UTF-8 file path");
    const char *detail = NULL; LoadedFile *lf = lf_load_keyfile(fn, &detail); filename_free(fn);
    if (!lf) return fail(e, PG_ERR_IO, detail);
    int type = key_type_s(BinarySource_UPCAST(lf)); BinarySource_REWIND(lf);
    if (type != SSH_KEYTYPE_SSH2_PUBLIC_RFC4716 && type != SSH_KEYTYPE_SSH2_PUBLIC_OPENSSH) {
        lf_free(lf); return fail(e, PG_ERR_CERTIFICATE, "Not an SSH-2 certificate file");
    }
    char *algorithm = NULL, *comment = NULL;
    strbuf *pub = strbuf_new();
    int loaded = ppk_loadpub_s(BinarySource_UPCAST(lf), &algorithm, BinarySink_UPCAST(pub), &comment, &detail);
    lf_free(lf); sfree(comment);
    const ssh_keyalg *alg = loaded ? find_pubkey_alg(algorithm) : NULL; sfree(algorithm);
    if (!alg || !alg->is_certificate) { strbuf_free(pub); return fail(e, PG_ERR_CERTIFICATE, detail ? detail : "Not a certificate"); }
    ssh_key *pubkey = ssh_key_new_pub(alg, ptrlen_from_strbuf(pub));
    if (!pubkey) { strbuf_free(pub); return fail(e, PG_ERR_CERTIFICATE, "Invalid certificate"); }
    strbuf *oldbase = strbuf_new(), *newbase = strbuf_new();
    ssh_key_public_blob(ssh_key_base_key(key->ssh2->key), BinarySink_UPCAST(oldbase));
    ssh_key_public_blob(ssh_key_base_key(pubkey), BinarySink_UPCAST(newbase)); ssh_key_free(pubkey);
    int match = ptrlen_eq_ptrlen(ptrlen_from_strbuf(oldbase), ptrlen_from_strbuf(newbase));
    strbuf_free(oldbase); strbuf_free(newbase);
    if (!match) { strbuf_free(pub); return fail(e, PG_ERR_CERT_MISMATCH, "Certificate is for a different public key"); }
    strbuf *priv = strbuf_new_nm(); ssh_key_private_blob(key->ssh2->key, BinarySink_UPCAST(priv));
    ssh_key *replacement = ssh_key_new_priv(alg, ptrlen_from_strbuf(pub), ptrlen_from_strbuf(priv));
    strbuf_free(priv); strbuf_free(pub);
    if (!replacement) return fail(e, PG_ERR_CERTIFICATE, "Could not combine certificate with key");
    ssh_key_free(key->ssh2->key); key->ssh2->key = replacement; return ok(e);
}
int pg_remove_certificate(pg_key *key, pg_error *e)
{
    if (!key || key->ssh1) return fail(e, PG_ERR_UNSUPPORTED, "Certificate requires an SSH-2 key");
    ssh_key *replacement = ssh_key_clone(ssh_key_base_key(key->ssh2->key));
    if (!replacement) return fail(e, PG_ERR_CERTIFICATE, "Could not remove certificate");
    ssh_key_free(key->ssh2->key); key->ssh2->key = replacement; return ok(e);
}
#ifdef PG_TESTING
/* 仅测试编译包含这些入口，覆盖现代 OpenSSH 已移除的 DSA 等格式。 */
int pg_test_sign_verify(pg_key *key)
{
    if (!key || key->ssh1) return 0;
    const char message[] = "PuTTYgen test-only message";
    ptrlen data = make_ptrlen(message, sizeof(message) - 1);
    strbuf *signature = strbuf_new();
    ssh_key_sign(key->ssh2->key, data, 0, BinarySink_UPCAST(signature));
    strbuf *pub = strbuf_new(); ssh_key_public_blob(key->ssh2->key, BinarySink_UPCAST(pub));
    ssh_key *public_key = ssh_key_new_pub(ssh_key_alg(key->ssh2->key), ptrlen_from_strbuf(pub));
    int valid = public_key && ssh_key_verify(public_key, ptrlen_from_strbuf(signature), data);
    if (public_key) ssh_key_free(public_key);
    strbuf_free(pub); strbuf_free(signature); return valid;
}
int pg_test_make_certificate(pg_key *key, pg_key *ca, const char *path)
{
    if (!key || key->ssh1 || !ca || ca->ssh1) return 0;
    const ssh_keyalg *basealg = ssh_key_alg(key->ssh2->key), *certalg = NULL;
    for (size_t i = 0; i < n_keyalgs; ++i) if (all_keyalgs[i]->base_alg == basealg) { certalg = all_keyalgs[i]; break; }
    if (!certalg) return 0;
    strbuf *base = strbuf_new(); ssh_key_public_blob(key->ssh2->key, BinarySink_UPCAST(base));
    BinarySource src[1]; BinarySource_BARE_INIT_PL(src, ptrlen_from_strbuf(base)); get_string(src);
    strbuf *blob = strbuf_new();
    put_stringz(blob, certalg->ssh_id); unsigned char nonce[32]; random_read(nonce,sizeof(nonce));
    put_string(blob,nonce,sizeof(nonce)); put_datapl(blob,get_data(src,get_avail(src)));
    put_uint64(blob,1); put_uint32(blob,1); put_stringz(blob,"test-certificate");
    strbuf *principals = strbuf_new(); put_stringz(principals,"test-user"); put_stringsb(blob,principals);
    put_uint64(blob,0); put_uint64(blob,UINT64_MAX);
    put_stringz(blob,""); put_stringz(blob,""); put_stringz(blob,"");
    strbuf *capub = strbuf_new(); ssh_key_public_blob(ca->ssh2->key, BinarySink_UPCAST(capub)); put_stringsb(blob,capub);
    strbuf *sig = strbuf_new(); ssh_key_sign(ca->ssh2->key,ptrlen_from_strbuf(blob),0,BinarySink_UPCAST(sig));
    int valid = ssh_key_verify(ca->ssh2->key,ptrlen_from_strbuf(sig),ptrlen_from_strbuf(blob)); put_stringsb(blob,sig);
    Filename *fn = filename_from_str(path); FILE *file = fn ? f_open(fn,"w",false) : NULL;
    int result = 0;
    if (file) { ssh2_write_pubkey(file,"test",blob->u,blob->len,SSH_KEYTYPE_SSH2_PUBLIC_OPENSSH); result = !ferror(file); if(fclose(file)) result=0; }
    if (fn) filename_free(fn);
    strbuf_free(blob); strbuf_free(base);
    return valid && result;
}
#endif
