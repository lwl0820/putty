#ifndef PG_PLATFORM_H
#define PG_PLATFORM_H
#include <stddef.h>
int pg_platform_random(void *data, size_t size);
int pg_platform_seed(void);
#ifdef PG_TESTING
void pg_test_random_failure(int fail);
#ifdef _WIN32
void pg_test_private_security_failure(int fail);
#endif
#endif

#ifdef PG_TESTING
struct pg_key;
int pg_test_sign_verify(struct pg_key *key);
int pg_test_make_certificate(struct pg_key *key, struct pg_key *ca, const char *path);
#endif

#endif
