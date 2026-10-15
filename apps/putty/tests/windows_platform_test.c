#include "core/pt_platform.h"
#include <stdio.h>

#define CHECK(value) do { if (!(value)) { \
    fprintf(stderr, "Windows 终端平台回归失败，第 %d 行：%s\n", __LINE__, #value); \
    return 1; } } while (0)

/* 用不同于 CP437 的 OEM 表检查适配层，不受 CI 系统区域设置影响。 */
static int probe_mode;
static void init_unicode_probe(Conf *conf, struct unicode_data *unicode)
{
    probe_mode = conf_get_int(conf, CONF_vtmode);
    unicode->line_codepage = probe_mode == VT_OEMONLY ? 1252 : CP_UTF8;
    if (probe_mode == VT_OEMONLY) unicode->font_codepage = 437;
    for (int i = 0; i < 256; ++i) {
        unicode->unitab_font[i] = 0x400 + i;
        unicode->unitab_oemcp[i] = 0x500 + i;
        unicode->unitab_scoacs[i] = 0x600 + i;
        unicode->unitab_line[i] = CSET_ACP + i;
        unicode->unitab_xterm[i] = CSET_OEMCP + i;
    }
}
#define pt_init_unicode pt_init_unicode_probe
#define init_ucs_generic init_unicode_probe
#include "../src/core/pt_platform.c"
#undef init_ucs_generic
#undef pt_init_unicode

static int check_oem_modes(void)
{
    const int modes[] = {VT_XWINDOWS, VT_OEMANSI, VT_OEMONLY, VT_UNICODE};
    Conf *conf = conf_new();
    conf_set_str(conf, CONF_line_codepage, "UTF-8");
    for (size_t mode = 0; mode < lenof(modes); ++mode) {
        conf_set_int(conf, CONF_vtmode, modes[mode]);
        struct unicode_data unicode = {0};
        pt_init_unicode_probe(conf, &unicode);
        const bool oem = modes[mode] == VT_XWINDOWS || modes[mode] == VT_OEMANSI;
        CHECK(probe_mode == (modes[mode] == VT_OEMONLY ? VT_OEMONLY : VT_UNICODE));
        CHECK(unicode.line_codepage == (modes[mode] == VT_OEMONLY ? 1252 : CP_UTF8));
        CHECK(unicode.font_codepage == (modes[mode] == VT_OEMONLY ? 437 : CP_UTF8));
        CHECK(conf_get_int(conf, CONF_vtmode) == modes[mode]);
        CHECK(!strcmp(conf_get_str(conf, CONF_line_codepage), "UTF-8"));
        for (int i = 0; i < 256; ++i) {
            CHECK(unicode.unitab_line[i] == 0x400 + i);
            CHECK(unicode.unitab_xterm[i] == 0x500 + i);
            CHECK(unicode.unitab_scoacs[i] == (oem ? 0x500 : 0x600) + i);
        }
    }
    conf_free(conf);
    return 0;
}

int main(void)
{
    CHECK(check_oem_modes() == 0);
    /* 只创建所需的显式配置字段，禁止通过 do_defaults 读取注册表。 */
    Conf *conf = conf_new();
    conf_set_str(conf, CONF_line_codepage, "UTF-8");
    conf_set_int(conf, CONF_vtmode, VT_XWINDOWS);
    struct unicode_data unicode = {0};
    pt_init_unicode(conf, &unicode);
    CHECK(unicode.line_codepage == CP_UTF8);
    CHECK(unicode.font_codepage == CP_UTF8 && !unicode.dbcs_screenfont);
    CHECK(conf_get_int(conf, CONF_vtmode) == VT_XWINDOWS);
    for (int i = 32; i < 127; ++i) CHECK(unicode.unitab_line[i] == i);
    CHECK(unicode.unitab_xterm['q'] == 0x2500);
    CHECK(unicode.unitab_xterm['x'] == 0x2502);
    CHECK(!memcmp(unicode.unitab_scoacs, unicode.unitab_oemcp,
                  sizeof(unicode.unitab_scoacs)));
    wchar_t *tables[] = {unicode.unitab_line, unicode.unitab_xterm, unicode.unitab_scoacs};
    for (size_t table = 0; table < lenof(tables); ++table)
        for (size_t i = 0; i < 256; ++i) CHECK(!DIRECT_FONT(tables[table][i]));
    FontSpec *font = platform_default_fontspec("Font");
    CHECK(!strcmp(font->name, "Courier New") && font->height == 10);
    fontspec_free(font);
    Filename *log = platform_default_filename("LogFileName");
    CHECK(!strcmp(filename_to_str(log), "putty.log"));
    filename_free(log);
    char *serial = platform_default_s("SerialLine");
    CHECK(serial && !strcmp(serial, "COM1"));
    sfree(serial);
    /* OEMONLY 仍使用正式 CP437 分支，输入自动值仍回退当前 ACP。 */
    conf_set_int(conf, CONF_vtmode, VT_OEMONLY);
    conf_set_str(conf, CONF_line_codepage, "not-a-codepage");
    pt_init_unicode(conf, &unicode);
    CHECK(unicode.font_codepage == 437 && unicode.line_codepage == (int)GetACP());
    CHECK(conf_get_int(conf, CONF_vtmode) == VT_OEMONLY);
    CHECK(!strcmp(conf_get_str(conf, CONF_line_codepage), "not-a-codepage"));
    conf_free(conf);
    puts("Windows 终端：Unicode 输出表及正式平台默认值通过；未访问存储和 PRNG");
    return 0;
}
