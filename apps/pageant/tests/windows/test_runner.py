"""验证原生运行前的报告/链接审计门，不代替 Windows IPC 实测。"""
import json
from pathlib import Path
import tempfile
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


if __name__ == "__main__":
    unittest.main()
