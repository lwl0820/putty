"""Windows 终端宿主拒绝门回归；本机结果不能替代原生 SSH 认证。"""
import base64
import copy
import json
import os
from pathlib import Path
import subprocess
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import run_ssh_test as runner


def keygen_report():
    return {"schema": 1, "kind": "terminal-keygen", "passed": True, "argon2_target_ms": 1500,
            "memory_kib": 8192, "parallelism": 1, "build_config": "Release", "compiler": "MSVC",
            "public_text": "ssh-ed25519 " + base64.b64encode(b"public-test-blob" * 4).decode() + " suite-terminal"}


def terminal_report():
    return {"passed": True, "qt": "6.8.3", "windows_guarded_ppk": True, "non_bmp_screen_verified": True,
            "gss_verified": False, "windows_native_verified": False, "host_key_rejection": True,
            "prompt_cancellation": True, "client_build_config": "Release", "client_compiler": "MSVC",
            "platform": "offscreen", "workload": {"duration_ms": 12500, "samples": 240,
                "maximum_heartbeat_ms": 70, "real_auth_work_ms": 1700, "frame_generation": 2,
                "metric_queries": 25, "font_families": ["Consolas", "Segoe UI Emoji"],
                "event_high_water": 4, "command_high_water": 3, "input_high_water": 22,
                "pixel_width": 1760, "pixel_height": 1400},
            "authentication_close": {"close_call_ms": 0, "safe_cleanup_ms": 1600, "heartbeats_while_closing": 30},
            "ordered_input": {"accepted_wire_bytes": 642, "command_high_water": 16,
                "capacity_refusal_observed": True, "order_verified_by_remote": True, "input_backpressure_retries": 100}}


class SSHRunnerTest(unittest.TestCase):
    def test_failure_diagnostics_only_allow_fixed_stages_and_numeric_locations(self):
        stages = "终端测试阶段：主机密钥拒绝\n终端测试检查失败：行=421\n"
        unsafe = ("口令=保密\n终端纵切失败：任意上游日志\n终端测试阶段：任意命令\n"
                  "终端测试检查失败：行=421 私钥\n终端测试阶段：/个人路径\n")
        exception = "终端测试未处理异常：代码=0xc0000005，模块=不可导出.dll，偏移=0x123\n"
        selected = runner.safe_diagnostics((stages + unsafe + exception).encode())
        self.assertEqual(selected, ["终端测试阶段：主机密钥拒绝", "终端测试检查失败：行=421",
                                   "终端测试未处理异常：代码=0xc0000005，偏移=0x123"])
        self.assertEqual(runner.safe_diagnostics(stages.encode(), keygen=True), [])
        self.assertEqual(runner.safe_diagnostics(b"x" * 16384 + stages.encode()), [])
        self.assertEqual(runner.safe_diagnostics((unsafe + "测试密钥生成或参数校准失败\n").encode(), keygen=True),
                         ["测试密钥生成或参数校准失败"])
        counts = ["终端 hive 生命周期计数：创建=5，销毁=5，主机键=1",
                  "终端 hive 种子计数：读取=10，写入=5，读打开=5，写打开=5，注册表=20，读取字节=1024，写入字节=160",
                  "终端 hive 收尾代码：核验=13，关闭=0，清理=0，清理执行=1"]
        self.assertEqual(runner.safe_diagnostics(("\n".join(counts) + "\n").encode()), counts)
        for line in counts:
            for suffix in ("，路径=/私有/hive", "，口令=保密"):
                self.assertEqual(runner.safe_diagnostics((line + suffix).encode()), [])
        self.assertEqual(runner.safe_diagnostics("\n".join(counts).encode(), keygen=True), [])
        for failure in (subprocess.CompletedProcess(["禁止导出命令"], 7, b"", (stages + unsafe).encode()),
                        subprocess.TimeoutExpired(["禁止导出命令"], 70, stderr=(stages + unsafe).encode())):
            with self.subTest(failure=type(failure)), patch.object(runner.subprocess, "run") as child:
                if isinstance(failure, Exception):
                    child.side_effect = failure
                else:
                    child.return_value = failure
                with self.assertRaises(runner.ValidationError) as caught:
                    runner._run_child(["禁止导出命令"], cwd=runner.ROOT, environment={}, timeout=70)
                self.assertIn("行=421", str(caught.exception))
                for forbidden in ("禁止导出命令", "保密", "任意", "私钥", "个人路径"):
                    self.assertNotIn(forbidden, str(caught.exception))

    def test_native_platform_cannot_be_overridden_by_ci_marker(self):
        with patch.object(runner.sys, "platform", "linux"), patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), \
                patch.object(runner.subprocess, "run") as child:
            with self.assertRaises(runner.ValidationError):
                runner.run(runner.TEMPORARY_ROOT / "putty-terminal-windows-tests.exe",
                           runner.TEMPORARY_ROOT / "suite-terminal-keygen.exe", runner.TEMPORARY_ROOT / "terminal")
            child.assert_not_called()

    def test_rejects_original_cli_and_unguarded_test_binary(self):
        parent = runner.TEMPORARY_ROOT / "windows-terminal-runner-tests"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as folder:
            folder = Path(folder)
            keygen = folder / "suite-terminal-keygen.exe"
            keygen.write_bytes(b"never-executed")
            for name in ("plink.exe", "putty-terminal-tests.exe", "putty-qt.exe"):
                binary = folder / name
                binary.write_bytes(b"never-executed")
                with self.subTest(name=name), patch.object(runner.sys, "platform", "win32"), \
                        patch.object(runner.subprocess, "run") as child:
                    with self.assertRaises(runner.ValidationError):
                        runner.run(binary, keygen, parent)
                    child.assert_not_called()

    def test_maps_require_real_guarded_objects_and_reject_production_objects(self):
        parent = runner.TEMPORARY_ROOT / "windows-terminal-runner-tests"
        parent.mkdir(parents=True, exist_ok=True)
        required = "guarded_terminal guarded_module hive_bootstrap storage_variant seed_guard settings noise sshrand prng".split()
        symbols = ("pt_terminal_real_module pt_terminal_module pt_publish random_ref random_unref random_clear prng_free "
                   "read_random_seed write_random_seed noise_get_heavy suite_seed_CreateFileA "
                   "suite_hive_release_owner suite_hive_claim_owner")
        content = "\n".join(name + ".c.obj" for name in required) + "\n" + symbols
        with tempfile.TemporaryDirectory(dir=parent) as folder:
            path = Path(folder) / "terminal.map"
            path.write_text(content, encoding="utf-8")
            self.assertTrue(runner.audit_map(path)["guarded_variant_only"])
            for extra in ("storage.c.obj", "suite-putty.lib:storage.obj", "putty-terminal.lib:pt_terminal.c.obj"):
                path.write_text(content + "\n" + extra, encoding="utf-8")
                with self.subTest(extra=extra), self.assertRaises(runner.ValidationError):
                    runner.audit_map(path)
            for missing in required + ["suite_hive_claim_owner", "random_clear"]:
                path.write_text(content.replace(missing, "missing"), encoding="utf-8")
                with self.subTest(missing=missing), self.assertRaises(runner.ValidationError):
                    runner.audit_map(path)

    def test_keygen_map_requires_compiled_rng_function_instead_of_macro_name(self):
        parent = runner.TEMPORARY_ROOT / "windows-terminal-runner-tests"
        parent.mkdir(parents=True, exist_ok=True)
        content = ("pg_core.c.obj\npg_platform.c.obj\nsshrand.c.obj\n"
                   "pg_generate pg_save random_setup_custom BCryptGenRandom")
        with tempfile.TemporaryDirectory(dir=parent) as folder:
            path = Path(folder) / "keygen.map"
            path.write_text(content, encoding="utf-8")
            runner.audit_map(path, keygen=True)
            # 只有宏名不能证明真实初始化函数已链接。
            path.write_text(content.replace("random_setup_custom", "random_setup_special"), encoding="utf-8")
            with self.assertRaises(runner.ValidationError):
                runner.audit_map(path, keygen=True)

    def test_keygen_requires_actual_parameters_and_matching_build_configuration(self):
        report = keygen_report()
        result, blob = runner.decode_keygen(json.dumps(report).encode())
        self.assertTrue(blob)
        self.assertEqual(result, report)
        for fields in ({"argon2_target_ms": 100}, {"memory_kib": 1024}, {"compiler": "GNU"},
                       {"build_config": ""}, {"parallelism": 0}, {"public_text": "secret"}, {"passed": False}):
            with self.subTest(fields=fields), self.assertRaises(runner.ValidationError):
                runner.decode_keygen(json.dumps({**report, **fields}).encode())
        terminal = terminal_report()
        terminal["client_build_config"] = "Debug"
        with self.assertRaises(runner.ValidationError):
            runner.validate_terminal(terminal, report)

    def test_rejects_missing_or_relaxed_performance_and_ordering_evidence(self):
        report = terminal_report()
        runner.validate_terminal(report, keygen_report())
        cases = [("workload", "maximum_heartbeat_ms", 251), ("workload", "real_auth_work_ms", 100),
                 ("workload", "samples", 179), ("workload", "duration_ms", 9999),
                 ("workload", "maximum_heartbeat_ms", float("nan")),
                 ("authentication_close", "close_call_ms", 25), ("authentication_close", "safe_cleanup_ms", 0),
                 ("ordered_input", "accepted_wire_bytes", 641), ("ordered_input", "input_backpressure_retries", 0),
                 ("ordered_input", "order_verified_by_remote", False), ("workload", "event_high_water", 5)]
        for group, field, value in cases:
            changed = copy.deepcopy(report)
            changed[group][field] = value
            with self.subTest(group=group, field=field), self.assertRaises(runner.ValidationError):
                runner.validate_terminal(changed, keygen_report())
        for field in ("gss_verified", "windows_native_verified"):
            changed = {**report, field: True}
            with self.subTest(field=field), self.assertRaises(runner.ValidationError):
                runner.validate_terminal(changed, keygen_report())

    def test_hive_requires_continuous_mapping_private_hostkey_and_all_five_cores(self):
        report = {"schema": 1, "kind": "terminal-hive", "passed": True, "continuous_mapping": True,
                  "cleanup": True, "host_key_in_private_hive": True, "cores_created": 5,
                  "cores_destroyed": 5, "seed_reads": 10, "seed_writes": 10, "registry_accesses": 100,
                  "seed_opens_read": 5, "seed_opens_write": 10,
                  "seed_bytes_read": 1024, "seed_bytes_written": 320}
        runner.validate_hive(json.dumps(report).encode())
        for fields in ({"continuous_mapping": False}, {"cores_destroyed": 4}, {"cleanup": False},
                       {"host_key_in_private_hive": False}, {"seed_writes": 0},
                       {"seed_opens_write": 5}, {"seed_opens_read": 4},
                       {"seed_bytes_read": 0}, {"seed_bytes_written": 0},
                       {"seed_opens_write": 10.0}, {"seed_bytes_read": True},
                       {"seed_bytes_written": 10 ** 1000}):
            with self.subTest(fields=fields), self.assertRaises(runner.ValidationError):
                runner.validate_hive(json.dumps({**report, **fields}).encode())
        for missing in ("seed_opens_read", "seed_opens_write", "seed_bytes_read", "seed_bytes_written"):
            changed = {key: value for key, value in report.items() if key != missing}
            with self.subTest(missing=missing), self.assertRaises(runner.ValidationError):
                runner.validate_hive(json.dumps(changed).encode())
        summary = runner.summarize_hive({**report, "private_path": "/私有/hive", "seed_data": "私有内容"})
        self.assertEqual(summary, report)
        self.assertNotIn("私有", json.dumps(summary, ensure_ascii=False))

    def test_peer_requires_exact_authentication_commands_and_no_live_resources(self):
        report = {"paramiko": "5.0.0", "bind_host": "127.0.0.1", "closed": True, "live_threads": 0,
            "active_connections": 0, "connection_errors": 0, "accepted_connections": 5,
            "authenticated_connections": 2, "completed_commands": 2, "capacity_refusals": 0,
            "command_results": [{"command": "suite-terminal-workload-v1", "exit_status": 0,
                "received_bytes": 22, "duration_seconds": 10.21, "samples": 1000, "output_bytes": 90000},
                {"command": "suite-terminal-ordered-input-v1", "exit_status": 0, "received_bytes": 642,
                 "duration_seconds": 0.11, "samples": 0, "output_bytes": 8}]}
        runner.validate_peer(report)
        for fields in ({"live_threads": 1}, {"completed_commands": 1}, {"connection_errors": 1},
                       {"authenticated_connections": 1}, {"command_results": report["command_results"] * 2}):
            with self.subTest(fields=fields), self.assertRaises(runner.ValidationError):
                runner.validate_peer({**report, **fields})

    def test_ppk_header_is_real_encrypted_argon2id_and_identity_matches(self):
        parent = runner.TEMPORARY_ROOT / "windows-terminal-runner-tests"
        parent.mkdir(parents=True, exist_ok=True)
        blob = b"x" * 51
        encoded = base64.b64encode(blob).decode()
        content = ("PuTTY-User-Key-File-3: ssh-ed25519\nEncryption: aes256-cbc\nComment: suite-terminal\n"
                   f"Public-Lines: 2\n{encoded[:64]}\n{encoded[64:]}\nKey-Derivation: Argon2id\n"
                   "Argon2-Memory: 8192\nArgon2-Passes: 123\nArgon2-Parallelism: 1\nPrivate-Lines: 1\nprivate\n")
        with tempfile.TemporaryDirectory(dir=parent) as folder:
            path = Path(folder) / "key.ppk"
            path.write_text(content, encoding="ascii")
            self.assertEqual(runner.ppk_parameters(path, blob)["Argon2-Passes"], 123)
            for old, new in (("aes256-cbc", "none"), ("Argon2id", "Argon2i"), ("8192", "1024"),
                             ("Argon2-Passes: 123", "Argon2-Passes: 0")):
                path.write_text(content.replace(old, new), encoding="ascii")
                with self.subTest(new=new), self.assertRaises(runner.ValidationError):
                    runner.ppk_parameters(path, blob)
            path.write_text(content, encoding="ascii")
            with self.assertRaises(runner.ValidationError):
                runner.ppk_parameters(path, b"different-identity")

    def test_paths_reject_escape_and_reparse_before_loading_private_data(self):
        with self.assertRaises(runner.ValidationError):
            runner.project_path(runner.TEMPORARY_ROOT / "parent/../escape")
        with self.assertRaises(runner.ValidationError):
            runner.project_path(ROOT_PARENT / "outside")
        with patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)):
            with self.assertRaises(runner.ValidationError):
                runner.project_path(runner.TEMPORARY_ROOT / "reparse/child")

    def test_help_remains_utf8_with_cp1252_redirection(self):
        result = subprocess.run([sys.executable, "-B", str(HERE / "run_ssh_test.py"), "--help"],
                                capture_output=True, timeout=10,
                                env=dict(os.environ, PYTHONIOENCODING="cp1252"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("终端", result.stdout.decode("utf-8"))


ROOT_PARENT = runner.ROOT.parent
if __name__ == "__main__":
    unittest.main()
