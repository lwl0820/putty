#ifndef PT_TEST_DIAGNOSTICS_H
#define PT_TEST_DIAGNOSTICS_H

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

/* 仅记录故障代码与模块偏移，不改变 Windows 对未处理异常的处置。 */
static LONG WINAPI terminal_test_exception(EXCEPTION_POINTERS *exception)
{
    const void *address = exception->ExceptionRecord->ExceptionAddress;
    HMODULE module = NULL;
    char path[MAX_PATH] = {0};
    const char *name = "未知模块";
    uintptr_t offset = 0;
    if (GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                          GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                          (LPCWSTR)address, &module) &&
        GetModuleFileNameA(module, path, sizeof(path) - 1)) {
        const char *separator = strrchr(path, '\\');
        name = separator ? separator + 1 : path;
        offset = (uintptr_t)address - (uintptr_t)module;
    }
    fprintf(stderr, "终端测试未处理异常：代码=0x%08lx，模块=%s，偏移=0x%llx\n",
            (unsigned long)exception->ExceptionRecord->ExceptionCode,
            name, (unsigned long long)offset);
    fflush(stderr);
    return EXCEPTION_CONTINUE_SEARCH;
}
#endif

static void terminal_test_diagnostics(void)
{
#ifdef _WIN32
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
    SetUnhandledExceptionFilter(terminal_test_exception);
#endif
}

static void terminal_test_stage(const char *stage)
{
    fprintf(stderr, "终端测试阶段：%s\n", stage);
    fflush(stderr);
}

#endif
