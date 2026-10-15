#ifndef SUITE_TERMINAL_HIVE_BOOTSTRAP_H
#define SUITE_TERMINAL_HIVE_BOOTSTRAP_H
#include "private_hive.h"
struct suite_seed_statistics;
#ifdef __cplusplus
extern "C" {
#endif
DWORD suite_terminal_prepare(void);
DWORD suite_terminal_claim_main(void);
DWORD suite_terminal_finish(int passed);
suite_private_hive *suite_terminal_hive(void);
void suite_terminal_core_created(void);
void suite_terminal_core_destroyed(struct suite_seed_statistics statistics);
#ifdef __cplusplus
}
#endif
#endif
