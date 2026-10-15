"""源码补丁、脱敏协议、私有环境与网络容量的真实边界回归。"""
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from tools.suite.windows.gss_ssh import dependencies as deps
from tools.suite.windows.gss_ssh.native import verify_environment, ssh_mic_payload, MAX_TOKEN, buffer
from tools.suite.windows.gss_ssh.run import validate_child, RunFailure, register_replay
from tools.suite.windows.gss_ssh.peer import GssSSHPeer, BoundedPacketizer, unicode_line
from tools.suite.windows.gss_kdc.security import PrivateTree, closed_environment
from tools.suite.windows.support import ROOT, ValidationError


class Boundaries(unittest.TestCase):
    def setUp(self):
        root = ROOT / ".tmp" / "gss-ssh-unit"
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(dir=root))
        self.addCleanup(shutil.rmtree, self.folder)
        logging.getLogger("paramiko").addHandler(logging.NullHandler())
        logging.getLogger("paramiko").setLevel(logging.CRITICAL)

    def test_requirements_match_exact_seven_windows_wheels(self):
        state = json.loads((deps.HERE / "windows-wheel-lock.json").read_text())
        lines = (deps.HERE / "requirements-windows.txt").read_text().splitlines()
        expected = {f'{item["name"]}=={item["version"]} --hash=sha256:{item["sha256"]}' for item in state["files"]}
        self.assertEqual({line for line in lines if line and not line.startswith("#")}, expected)
        self.assertEqual(len(expected), 7)

    def test_isolated_absolute_script_entry_imports_from_private_cwd(self):
        env = self.environment()
        result = subprocess.run([sys.executable, "-I", "-B", deps.HERE / "run.py", "--help"],
                                cwd=self.folder, env=env, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertIn(b"--prepared-kdc", result.stdout)
        self.assertIn(b"--report", result.stdout)
        self.assertIn(b"--allow-linux", result.stdout)

    def test_official_patch_before_after_and_mutation(self):
        directory = deps.source_directory()
        content = (directory / "auth_handler.py").read_bytes()
        original = content.replace(deps.IMPORT_NEW, deps.IMPORT_OLD).replace(deps.TABLE_NEW, deps.TABLE_OLD)
        self.assertEqual(hashlib.sha256(original).hexdigest(), deps.ORIGINAL["auth_handler.py"])
        self.assertEqual(deps.patched_auth(original), content)
        with self.assertRaises(ValidationError):
            deps.patched_auth(original + b"\n")
        self.assertEqual(set(deps.verify_sources()), set(deps.ORIGINAL))

    def test_reject_dependency_version_drift(self):
        with mock.patch.object(deps.importlib.metadata, "version", return_value="0.0"):
            with self.assertRaises(ValidationError):
                deps.source_directory()

    def test_wheel_rejects_extra_and_mutated_content(self):
        wheels = self.folder / "wheels"
        wheels.mkdir()
        records = []
        for number in range(7):
            content = bytes([number])
            name = f"locked-{number}.whl"
            (wheels / name).write_bytes(content)
            records.append({"wheel": name, "size": 1, "sha256": hashlib.sha256(content).hexdigest()})
        (self.folder / "windows-wheel-lock.json").write_text(json.dumps({"files": records}))
        with mock.patch.object(deps, "HERE", self.folder):
            self.assertEqual(deps.verify_windows_wheels(wheels), 7)
            (wheels / "unexpected.dll").write_bytes(b"x")
            with self.assertRaises(ValidationError):
                deps.verify_windows_wheels(wheels)
            (wheels / "unexpected.dll").unlink()
            (wheels / "locked-0.whl").write_bytes(b"x")
            with self.assertRaises(ValidationError):
                deps.verify_windows_wheels(wheels)

    def environment(self):
        tree = PrivateTree(self.folder, existing=True)
        self.addCleanup(tree.close)
        env = closed_environment(tree)
        for name in ("krb5.conf", "client.ccache", "server.keytab"):
            tree.write(self.folder / name, b"test")
        env.update({"KRB5_CONFIG": str(self.folder / "krb5.conf"), "KRB5CCNAME": "FILE:" + str(self.folder / "client.ccache"),
                    "KRB5_KTNAME": "FILE:" + str(self.folder / "server.keytab"),
                    "KRB5_CLIENT_KTNAME": "FILE:" + str(self.folder / "absent-client.keytab"),
                    "KRB5RCACHETYPE": "dfl", "KRB5RCACHEDIR": str(self.folder),
                    "KRB5_TRACE": str(self.folder / "trace.log")})
        return env

    def test_environment_requires_exact_private_files(self):
        env = self.environment()
        with mock.patch.dict(os.environ, env, clear=True):
            files = verify_environment(self.folder)
            self.assertEqual(files["KRB5CCNAME"], self.folder / "client.ccache")
            os.environ["KRB5CCNAME"] = "API:personal"
            with self.assertRaises(ValidationError):
                verify_environment(self.folder)

    def test_environment_rejects_personal_plugin_or_agent(self):
        env = self.environment()
        for variable in ("KRB5_PLUGIN_BASE_DIR", "SSH_AUTH_SOCK", "LD_PRELOAD"):
            with self.subTest(variable=variable), mock.patch.dict(os.environ, {**env, variable: "foreign"}, clear=True):
                with self.assertRaises(ValidationError):
                    verify_environment(self.folder)

    def test_environment_rejects_extra_variables_and_alternate_private_locations(self):
        env = self.environment()
        alternate = self.folder / "alternate"
        alternate.mkdir(mode=0o700)
        (alternate / "krb5.conf").write_bytes(b"test")
        for changes in ({"SSL_CERT_FILE": "foreign"}, {"PYTHONPATH": "foreign"},
                        {"HOME": str(alternate)}, {"KRB5_CONFIG": str(alternate / "krb5.conf")},
                        {"KRB5_TRACE": str(alternate / "trace.log")}):
            with self.subTest(changes=tuple(changes)), mock.patch.dict(os.environ, {**env, **changes}, clear=True):
                with self.assertRaises(ValidationError):
                    verify_environment(self.folder)

    def test_missing_cache_is_explicit_and_not_created(self):
        env = self.environment()
        env["KRB5CCNAME"] = "FILE:" + str(self.folder / "absent.ccache")
        with mock.patch.dict(os.environ, env, clear=True):
            verify_environment(self.folder, missing_cache=True)
            self.assertFalse((self.folder / "absent.ccache").exists())
            (self.folder / "absent.ccache").write_bytes(b"x")
            with self.assertRaises(ValidationError):
                verify_environment(self.folder, missing_cache=True)

    def test_token_and_mic_inputs_have_hard_bounds(self):
        with self.assertRaises(ValidationError):
            buffer(b"x" * (MAX_TOKEN + 1))
        with self.assertRaises(ValidationError):
            ssh_mic_payload(b"short")
        with self.assertRaises(ValidationError):
            ssh_mic_payload(b"x" * 32, "other")
        self.assertIn(b"gssapi-with-mic", ssh_mic_payload(b"x" * 32))

    def valid_child(self):
        detail = {"mutual_authentication_verified": True, "mic_verified": True, "tampered_mic_rejected": True,
                  "delegation_not_requested_or_returned": True, "initiator_flags": 34, "acceptor_flags": 34, "exchanges": 1,
                  "mic_rejection": {"major": 393216, "minor": 0}}
        return {"schema": 1, "kind": "mit-gss-ssh-child", "passed": True, "scenario": "token-mic",
                "native_windows": os.name == "nt", "sspi_verified": False, "all_native_objects_released": True,
                "source_sha256": {"fixed": "sha"}, "detail": detail}

    def test_child_report_rejects_extra_secret_false_and_delegation(self):
        result = self.valid_child()
        self.assertTrue(validate_child(json.dumps(result).encode(), "token-mic", {"fixed": "sha"})["mic_verified"])
        for mutation in (lambda r: r.update({"private_path": "secret"}), lambda r: r.update({"passed": False}),
                         lambda r: r["detail"].update({"acceptor_flags": 35}),
                         lambda r: r["detail"]["mic_rejection"].update({"major": 0})):
            value = self.valid_child()
            mutation(value)
            with self.assertRaises(RunFailure):
                validate_child(json.dumps(value).encode(), "token-mic", {"fixed": "sha"})

    def test_child_protocol_rejects_duplicate_fields_and_nonfinite_numbers(self):
        original = json.dumps(self.valid_child())
        for value in (original.replace('"schema": 1', '"schema": 0, "schema": 1'),
                      original.replace('"major": 393216', '"major": 0, "major": 393216'),
                      original.replace('"minor": 0', '"minor": NaN'),
                      "[" * 1100 + "0" + "]" * 1100, original + " " * 16384):
            with self.subTest(value=value[:60]), self.assertRaises(RunFailure):
                validate_child(value.encode(), "token-mic", {"fixed": "sha"})

    def test_child_protocol_rejects_boolean_schema_and_out_of_range_native_values(self):
        for mutation in (lambda r: r.update({"schema": True}),
                         lambda r: r.update({"native_windows": int(os.name == "nt")}),
                         lambda r: r["detail"].update({"initiator_flags": 2**32 + 34}),
                         lambda r: r["detail"].update({"mic_rejection": []}),
                         lambda r: r["detail"]["mic_rejection"].update({"minor": 2**32})):
            result = self.valid_child()
            mutation(result)
            with self.assertRaises(RunFailure):
                validate_child(json.dumps(result).encode(), "token-mic", {"fixed": "sha"})

    def test_failed_child_rejects_wrong_stage_and_native_status_shape(self):
        original = {"schema": 1, "kind": "mit-gss-ssh-child", "passed": False,
                    "scenario": "token-mic", "stage": "token-mic", "sspi_verified": False}
        with self.assertRaises(RunFailure) as caught:
            validate_child(json.dumps(original).encode(), "token-mic", {})
        self.assertEqual(caught.exception.stage, "child-token-mic-token-mic")
        for changes in ({"stage": "ssh-authentication"}, {"schema": True},
                        {"native_major": None, "native_minor": None},
                        {"native_major": 0, "native_minor": 0},
                        {"native_major": 1, "native_minor": 2**32}):
            with self.subTest(changes=changes), self.assertRaises(RunFailure) as caught:
                validate_child(json.dumps({**original, **changes}).encode(), "token-mic", {})
            self.assertIn(caught.exception.stage, ("child-protocol", "child-token-mic"))

    def test_ssh_child_requires_native_initiator_and_consistent_failure_counts(self):
        result = self.valid_child()
        result["scenario"] = "ssh"
        result["detail"] = {
            "rejection_verified": False, "fixed_command_completed": True, "formal_plink_verified": False,
            "peer": {"accepted": 1, "authenticated": 1, "completed": 1, "rejected_auth": 0,
                     "capacity_refused": 0, "worker_errors": 0, "active_high_water": 1,
                     "closed": True, "active_connections": 0, "live_threads": 0},
            "provider": {"wrong_username_rejections": 0, "mic_rejections": 0,
                         "initiator_completed": 1, "acceptor_completed": 1},
            "native_acquire_failures": 0,
        }
        self.assertTrue(validate_child(json.dumps(result).encode(), "ssh", {"fixed": "sha"})["fixed_command_completed"])
        for section, field, value in (("provider", "initiator_completed", 0),
                                      ("provider", "mic_rejections", 1),
                                      (None, "native_acquire_failures", -1),
                                      (None, "native_acquire_failures", 1)):
            mutated = json.loads(json.dumps(result))
            target = mutated["detail"][section] if section else mutated["detail"]
            target[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(RunFailure):
                validate_child(json.dumps(mutated).encode(), "ssh", {"fixed": "sha"})

    def test_replay_registration_has_single_exact_name(self):
        realm = mock.Mock(root=self.folder)
        with self.assertRaises(RunFailure):
            register_replay(realm, required=True)
        unknown = self.folder / "unknown-secret"
        unknown.write_bytes(b"secret")
        register_replay(realm, required=False)
        realm.register_artifact.assert_not_called()
        name = "suite-gss-replay" if os.name == "nt" else f"krb5_{os.geteuid()}.rcache2"
        expected = self.folder / name
        expected.write_bytes(b"replay")
        register_replay(realm, required=True)
        realm.register_artifact.assert_called_once_with(expected)
        self.assertTrue(unknown.exists())

    def test_packetizer_rejects_unbounded_banner_and_packet(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        a.settimeout(0.1)
        packetizer = BoundedPacketizer(a)
        with self.assertRaises(Exception):
            packetizer.read_all(256 * 1024 + 1)
        b.sendall(b"x" * 1025)
        with self.assertRaises(Exception):
            packetizer.readline(1)
        packetizer.start_handshake(0.01)
        time.sleep(0.02)
        self.assertTrue(packetizer.handshake_timed_out())
        packetizer.complete_handshake()
        self.assertFalse(packetizer.handshake_timed_out())

    def test_silent_connections_obey_capacity_and_shared_close_deadline(self):
        sockets = []
        peer = GssSSHPeer()
        try:
            with peer:
                for number in range(16):
                    sock = socket.create_connection(("127.0.0.1", peer.port), timeout=1)
                    sockets.append(sock)
                    limit = time.monotonic() + 1
                    while peer.report()["accepted"] < number + 1 and time.monotonic() < limit:
                        time.sleep(0.005)
                self.assertEqual(peer.report()["accepted"], 16)
                self.assertEqual(peer.report()["active_high_water"], 2)
                self.assertEqual(peer.report()["capacity_refused"], 14)
                started = time.monotonic()
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual(peer.report()["live_threads"], 0)
            self.assertEqual(peer.report()["active_connections"], 0)
        finally:
            peer.close()
            for sock in sockets:
                sock.close()

    def test_future_unicode_payload_matches_existing_terminal_fixture(self):
        expected = ("\x1b[H\x1b[2KSSH 000007 中文 e\u0301 Ω Ж 😀 漢字\r\n"
                    "\x1b[38;2;120;210;255m真彩色\x1b[0m").encode("utf-8")
        self.assertEqual(unicode_line(7), expected)

    def test_native_and_cleanup_hangs_have_process_hard_deadline(self):
        for stage in ("load", "cleanup"):
            code = (f"import sys,time; sys.path.insert(0,{str(ROOT)!r}); "
                    "import tools.suite.windows.gss_ssh.child as c; "
                    f"sys.argv=['child','--root',{str(self.folder)!r},'--scenario','token-mic']; ")
            if stage == "load":
                code += "c.NativeGss=lambda **kw: time.sleep(60); "
            else:
                code += ("c.NativeGss=lambda **kw: type('Fake',(),{'close':lambda self:time.sleep(60)})(); "
                         "c.token_mic=lambda api:{}; ")
            code += "c.main(deadline_seconds=0.15)"
            result = subprocess.run([sys.executable, "-I", "-B", "-c", code],
                                    capture_output=True, timeout=5)
            self.assertEqual(result.returncode, 124, stage)
            self.assertNotIn(b'"passed": true', result.stdout)

    def test_final_report_hang_replaces_old_success_before_deadline(self):
        report = self.folder / "report.json"
        report.write_text('{"passed":true}')
        code = (f"import sys,time; sys.path.insert(0,{str(ROOT)!r}); "
                "import tools.suite.windows.gss_ssh.run as r; "
                f"sys.argv=['run','--prepared-kdc',{str(self.folder)!r},'--parent',{str(self.folder)!r},'--report',{str(report)!r}]; "
                "original=r.publish_report; calls=[]; "
                "r.publish_report=lambda path,value: (original(path,value) if not calls else time.sleep(60),calls.append(1)); "
                "r.run=lambda args:{'passed':True}; r.main(deadline_seconds=0.15)")
        result = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 124)
        value = json.loads(report.read_text())
        self.assertIs(value["passed"], False)
        self.assertEqual(value["stage"], "pending")

    def test_stdout_flush_hang_preserves_pending_file_before_hard_deadline(self):
        report = self.folder / "report.json"
        report.write_text('{"passed":true}')
        code = (f"import sys,time; sys.path.insert(0,{str(ROOT)!r}); "
                "import tools.suite.windows.gss_ssh.run as r; "
                f"sys.argv=['run','--prepared-kdc',{str(self.folder)!r},'--parent',{str(self.folder)!r},'--report',{str(report)!r}]; "
                "r.run=lambda args:{'passed':True}; "
                "sys.stdout=type('Blocked',(),{'write':lambda self,value:len(value),'flush':lambda self:time.sleep(60)})(); "
                "r.main(deadline_seconds=0.15)")
        result = subprocess.run([sys.executable, "-I", "-B", "-c", code], capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 124)
        value = json.loads(report.read_text())
        self.assertIs(value["passed"], False)
        self.assertEqual(value["stage"], "pending")


if __name__ == "__main__":
    unittest.main()
