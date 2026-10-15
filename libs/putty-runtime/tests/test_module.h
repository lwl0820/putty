#ifndef PUTTY_RUNTIME_TEST_MODULE_H
#define PUTTY_RUNTIME_TEST_MODULE_H
#include "putty_runtime/core.h"
#ifdef __cplusplus
extern "C" {
#endif
extern const pr_module runtime_test_module;
unsigned runtime_test_destroyed(void);
unsigned runtime_test_oversize_done(void);
unsigned runtime_test_late_callbacks(void);
unsigned runtime_test_cancelled(void);
unsigned runtime_test_thread_errors(void);
#ifndef _WIN32
int runtime_test_fd_close(void);
#endif
#ifdef __cplusplus
}
#endif
#endif
