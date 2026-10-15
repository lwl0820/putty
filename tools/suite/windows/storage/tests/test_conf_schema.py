"""按上游配置主表核对宿主访问器；不代替 Windows 存储往返。"""
from pathlib import Path
import re
import unittest


STORAGE = Path(__file__).resolve().parents[1]
ROOT = STORAGE.parents[3]


def without_comments(source):
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", source, flags=re.DOTALL)


def schema_errors(source):
    master = without_comments((ROOT / "vendor/putty/conf.h").read_text(encoding="utf-8"))
    options = {}
    for option in re.finditer(r"\bCONF_OPTION\s*\(\s*(\w+)\s*,(.*?)(?=\bCONF_OPTION\s*\(|\Z)",
                              master, flags=re.DOTALL):
        kind = re.search(r"\bVALUE_TYPE\s*\(\s*(\w+)\s*\)", option[2])
        if kind is None:
            raise AssertionError(f"上游配置 {option[1]} 缺少类型")
        subkey = re.search(r"\bSUBKEY_TYPE\s*\(", option[2]) is not None
        options[option[1]] = (kind[1], subkey)

    # 这里核对实际 Conf API 的类型契约，不维护第二份字段类型表。
    allowed = {
        "conf_get_str": {"STR"}, "conf_set_str": {"STR", "STR_AMBI"},
        "conf_get_utf8": {"UTF8"}, "conf_set_utf8": {"UTF8", "STR_AMBI"},
        "conf_get_str_ambi": {"STR", "UTF8", "STR_AMBI"},
        "conf_get_int": {"INT"}, "conf_set_int": {"INT"},
        "conf_get_bool": {"BOOL"}, "conf_set_bool": {"BOOL"},
        "conf_get_filename": {"FILENAME"}, "conf_set_filename": {"FILENAME"},
        "conf_get_fontspec": {"FONT"}, "conf_set_fontspec": {"FONT"},
    }
    source = without_comments(source)
    calls = list(re.finditer(r"\b(conf_(?:get|set)_\w+)\s*\(\s*\w+\s*,\s*CONF_(\w+)\b", source))
    if not calls or len(calls) != len(re.findall(r"\bconf_(?:get|set)_\w+\s*\(", source)):
        raise AssertionError("宿主出现无法核对字段的配置访问器")
    errors = []
    for call in calls:
        accessor, field = call[1], call[2]
        if accessor not in allowed or field not in options:
            errors.append(f"未知配置调用：{accessor}/CONF_{field}")
            continue
        kind, subkey = options[field]
        if subkey or kind not in allowed[accessor]:
            errors.append(f"{accessor}/CONF_{field} 与上游类型 {kind} 不匹配")
    return errors


class StorageConfSchemaTest(unittest.TestCase):
    def test_actual_host_accessors_match_upstream_schema(self):
        source = (STORAGE / "storage_test.c").read_text(encoding="utf-8")
        self.assertEqual(schema_errors(source), [])

    def test_previous_username_getter_is_rejected(self):
        source = (STORAGE / "storage_test.c").read_text(encoding="utf-8")
        corrected = "conf_get_str_ambi(conf, CONF_username, NULL)"
        self.assertIn(corrected, source)
        previous = source.replace(corrected, "conf_get_str(conf, CONF_username)")
        self.assertEqual(schema_errors(previous),
                         ["conf_get_str/CONF_username 与上游类型 STR_AMBI 不匹配"])

    def test_ambiguous_strings_accept_both_encodings_and_reject_wrong_type(self):
        self.assertEqual(schema_errors("conf_set_str(conf, CONF_username, value);"
                                       "conf_set_utf8(conf, CONF_username, value);"
                                       "conf_get_str_ambi(conf, CONF_username, NULL);"), [])
        self.assertEqual(schema_errors("conf_get_int(conf, CONF_host);"),
                         ["conf_get_int/CONF_host 与上游类型 STR 不匹配"])


if __name__ == "__main__":
    unittest.main()
