#include "hive_bootstrap.h"
#include "../test_diagnostics.h"
#include <cstdio>
int terminal_test_entry(int argc, char **argv);
int main(int argc, char **argv)
{
    terminal_test_diagnostics();
    terminal_test_stage("私有 hive 创建");
    DWORD error = suite_terminal_prepare();
    if (error) { std::fprintf(stderr, "终端 hive 初始化失败，代码 %lu\n", (unsigned long)error); return 1; }
    terminal_test_stage("Qt 终端入口");
    const int result = terminal_test_entry(argc, argv);
    terminal_test_stage("私有 hive 核验与清理");
    error = suite_terminal_finish(result == 0);
    if (error) std::fprintf(stderr, "终端 hive 清理或实际存储检查失败，代码 %lu\n", (unsigned long)error);
    if (!result && !error) terminal_test_stage("全部完成");
    return result || error ? 1 : 0;
}
