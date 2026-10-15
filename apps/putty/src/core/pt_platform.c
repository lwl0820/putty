#include "pt_platform.h"

#ifdef _MSC_VER
/* 未声明函数的隐式 int 会截断 x64 指针，必须在此适配层编译时拒绝。 */
#pragma warning(error: 4013)
#endif

#ifndef _WINDOWS
/* Unix 保持此纵切原有的离屏默认值，不从 Xresources 覆盖。 */
char *platform_default_s(const char *name) { (void)name; return NULL; }
bool platform_default_b(const char *name, bool value) { (void)name; return value; }
int platform_default_i(const char *name, int value) { (void)name; return value; }
FontSpec *platform_default_fontspec(const char *name)
{ (void)name; return fontspec_new_default(); }
Filename *platform_default_filename(const char *name)
{ (void)name; return filename_from_str(""); }
char *platform_get_x_display(void) { return dupstr(getenv("DISPLAY")); }
#endif

Filename *pt_filename_from_utf8(const char *path)
{
#ifdef _WINDOWS
    /* filename_from_utf8 仅在上游实现文件内使用，没有公共声明。 */
    wchar_t *wide_path = decode_utf8_to_wide_string(path);
    Filename *filename = filename_from_wstr(wide_path);
    sfree(wide_path);
    return filename;
#else
    return filename_from_str(path);
#endif
}

void pt_init_unicode(Conf *conf, struct unicode_data *unicode)
{
#ifdef _WINDOWS
    /* 这是 Unicode 绘图设备，不能以 ACP／OEM 的 GDI 字体绘制标签。 */
    unicode->font_codepage = CP_UTF8;
    unicode->dbcs_screenfont = false;
    const int vtmode = conf_get_int(conf, CONF_vtmode);
    Conf *unicode_conf = conf_copy(conf);
    /* OEMONLY 的 CP437 字形与自动输入代码页仍由正式平台分支选择。 */
    if (vtmode != VT_OEMONLY)
        conf_set_int(unicode_conf, CONF_vtmode, VT_UNICODE);
    init_ucs_generic(unicode_conf, unicode);
    conf_free(unicode_conf);
    /* Unicode 绘制能力不能把会话选用的系统 OEM 字符集改成 CP437。 */
    if (vtmode == VT_XWINDOWS || vtmode == VT_OEMANSI)
        memcpy(unicode->unitab_scoacs, unicode->unitab_oemcp,
               sizeof(unicode->unitab_scoacs));
#else
    init_ucs_generic(conf, unicode);
#endif
#ifdef _WINDOWS
    wchar_t *tables[] = {unicode->unitab_line, unicode->unitab_xterm, unicode->unitab_scoacs};
    for (size_t table = 0; table < lenof(tables); ++table) {
        for (size_t i = 0; i < 256; ++i) {
            const unsigned long character = tables[table][i];
            if ((character & CSET_MASK) == CSET_ACP)
                tables[table][i] = unicode->unitab_font[character & 0xff];
            else if ((character & CSET_MASK) == CSET_OEMCP)
                tables[table][i] = unicode->unitab_oemcp[character & 0xff];
        }
    }
#endif
}
