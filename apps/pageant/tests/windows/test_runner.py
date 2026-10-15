"""验证原生运行前的报告/链接审计门，不代替 Windows IPC 实测。"""
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

import run_ipc_test as runner


class RunnerChecks(unittest.TestCase):
    def test_report_requires_every_check_once_and_no_storage(self):
        report = {"schema": 1, "kind": "pageant-ipc", "passed": True,
                  "storage": "not-run", "checks": sorted(runner.CHECKS)}
        self.assertEqual(runner.decode(json.dumps(report).encode()), report)
        for invalid in ({**report, "checks": report["checks"][:-1]},
                        {**report, "checks": report["checks"] + report["checks"][:1]},
                        {**report, "checks": [None]}, {**report, "storage": "passed"},
                        {**report, "passed": False}, {**report, "kind": "other"}):
            with self.subTest(invalid=invalid), self.assertRaises(runner.ValidationError):
                runner.decode(json.dumps(invalid).encode())

    def test_link_map_accepts_pageant_fatal_random_only(self):
        parent = runner.ROOT / ".tmp"
        parent.mkdir(exist_ok=True)
        symbols = "read_random_seed write_random_seed open_settings_r open_settings_w " \
                  "pageant_init pa_windows_ipc_start random_read no_storage.c.obj " \
                  "agent_query_synchronous pa_windows_query_failed"
        with tempfile.TemporaryDirectory(dir=parent, prefix="pa-ipc-map-") as directory:
            path = Path(directory) / "test.map"
            path.write_text(symbols)
            self.assertTrue(runner.audit_map(path)["storage_tripwires_linked"])
            for forbidden in ("suite-putty:storage.c.obj", "suite-putty:sshrand.c.obj",
                              "suite-putty:noise.c.obj", "random_ref", "random_reseed",
                              "suite-putty:aqsync.c.obj", "suite-putty:agent-client.c.obj"):
                path.write_text(symbols + " " + forbidden)
                with self.subTest(forbidden=forbidden), self.assertRaises(runner.ValidationError):
                    runner.audit_map(path)
            path.write_text(symbols.replace("open_settings_r", ""))
            with self.assertRaises(runner.ValidationError):
                runner.audit_map(path)

    def test_non_native_platform_never_executes_binary(self):
        with patch.object(runner.sys, "platform", "linux"), patch.object(runner.subprocess, "run") as execute:
            with self.assertRaises(runner.ValidationError):
                runner.run(Path("pageant.exe"), runner.ROOT / ".tmp/unused")
            execute.assert_not_called()

    def test_legacy_dual_transport_report_cannot_pass_unix_gate(self):
        checks = sorted(check for check in runner.CHECKS if not check.startswith("unix-"))
        report = {"schema": 1, "kind": "pageant-ipc", "passed": True,
                  "storage": "not-run", "checks": checks}
        self.assertEqual(len(checks), 15)
        with self.assertRaises(runner.ValidationError):
            runner.decode(json.dumps(report).encode())

    def test_fault_diagnostics_only_allow_fixed_stages_and_numeric_locations(self):
        allowed = ["Pageant 测试阶段：缺失父目录拒绝", "Pageant AF_UNIX 子阶段：编号=6",
                   "Pageant 测试检查失败：行=421",
                   "Pageant 测试故障：类型=abort，代码=0x00000006，阶段=20",
                   "Pageant 测试栈：帧=7，偏移=0x123abc"]
        rejected = ["口令=保密", "Pageant 测试阶段：C:\\个人路径", "Pageant AF_UNIX 子阶段：编号=1000",
                    "Pageant 测试故障：类型=任意日志，代码=0x00000006，阶段=20",
                    "Pageant 测试栈：帧=24，偏移=0x123abc", "Pageant 测试栈：帧=7，偏移=C:\\密钥",
                    "Windows Pageant IPC 失败：个人路径", "Pageant 测试检查失败：行=421 口令"]
        self.assertEqual(runner.safe_diagnostics("\n".join(allowed + rejected).encode()), allowed)
        self.assertEqual(runner.safe_diagnostics(b"x" * 16384 + "\n".join(allowed).encode()), [])
        self.assertEqual(runner.safe_diagnostics("\n".join(allowed * 40).encode()), (allowed * 40)[-64:])
        self.assertEqual(runner.safe_diagnostics(None), [])

    def test_truncated_diagnostic_line_cannot_change_numeric_location(self):
        line = "Pageant 测试栈：帧=7，偏移=0x123abc".encode()
        prefix = b"x" * (16384 - len(line) + 2) + b"\n"
        self.assertEqual(runner.safe_diagnostics(prefix + line), [])
        self.assertEqual(runner.safe_diagnostics(line + b"\r\n" + b"x" * 16384), [line.decode()])
        self.assertEqual(runner.safe_diagnostics(b"\xff\n" + line), [line.decode()])

    def test_failure_and_timeout_keep_only_safe_fault_diagnostics(self):
        output = "Pageant 测试阶段：三传输启动\nPageant 测试栈：帧=7，偏移=0x42\n私钥=秘密\n".encode()
        result = subprocess.CompletedProcess(["fixture"], 134, b"", output)
        outcomes = (result, subprocess.TimeoutExpired(["fixture"], 70, stderr=output))
        for outcome in outcomes:
            with self.subTest(outcome=type(outcome).__name__), patch.object(runner.subprocess, "run") as execute:
                if isinstance(outcome, Exception):
                    execute.side_effect = outcome
                else:
                    execute.return_value = outcome
                with self.assertRaises(runner.ValidationError) as caught:
                    runner.run_child(["fixture"], cwd=Path("."), environment={})
                self.assertIn("Pageant 测试阶段：三传输启动", str(caught.exception))
                self.assertIn("偏移=0x42", str(caught.exception))
                self.assertNotIn("秘密", str(caught.exception))
                self.assertNotIn("私钥", str(caught.exception))
                self.assertEqual(execute.call_args.kwargs["timeout"], 70)


if __name__ == "__main__":
    unittest.main()
