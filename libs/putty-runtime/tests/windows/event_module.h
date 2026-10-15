#ifndef SUITE_WINDOWS_EVENT_MODULE_H
#define SUITE_WINDOWS_EVENT_MODULE_H
#include "putty_runtime/core.h"
#ifdef __cplusplus
extern "C" {
#endif
extern const pr_module windows_event_module;
unsigned windows_event_thread_errors(void);
unsigned windows_event_destroyed(void);
#ifdef __cplusplus
}
#endif
#endif
