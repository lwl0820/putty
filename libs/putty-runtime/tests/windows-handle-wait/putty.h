#ifndef PUTTY_RUNTIME_HANDLE_WAIT_TEST_SHIM_H
#define PUTTY_RUNTIME_HANDLE_WAIT_TEST_SHIM_H

/* 此窄适配仅验证原等待注册算法，不提供 Windows 信号或消息语义。 */
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include "tree234.h"
typedef uintptr_t HANDLE;
#define MAXIMUM_WAIT_OBJECTS 64
#define snew(type) ((type *)malloc(sizeof(type)))
#define sfree(pointer) free(pointer)
#define container_of(pointer, type, member) ((type *)((char *)(pointer) - offsetof(type, member)))
#include "handle-wait.h"

#endif
