/* 直接编译生产终端适配；仅改导出名，由受控 wrapper 接管模块入口。 */
#define pt_terminal_module pt_terminal_real_module
#include "../../src/core/pt_terminal.c"
