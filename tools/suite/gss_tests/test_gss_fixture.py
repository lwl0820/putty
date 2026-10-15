"""显式启用的真实 Kerberos/GSSAPI 集成检查。"""

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import unittest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.suite.gss_fixture import GSSSSHServer, KerberosRealm  # noqa: E402
from tools.suite.isolation import IsolationEnvironment, IsolationError  # noqa: E402


@unittest.skipUnless(sys.platform == "linux" and os.environ.get("PUTTY_SUITE_GSS_TESTS") == "1",
                     "需显式启用真实 KDC 与 Docker SSH 测试")
class GSSFixtureTest(unittest.TestCase):
    def setUp(self):
        binary = os.environ.get("PUTTY_SUITE_GSS_PLINK")
        if not binary or not Path(binary).is_file():
            self.fail("PUTTY_SUITE_GSS_PLINK 必须指定已构建的 Plink")
        self.plink = Path(binary).absolute()
        self.original_environment = dict(os.environ)
        self.environment = IsolationEnvironment(ROOT / ".tmp/suite-gss-tests")
        self.addCleanup(self.environment.close)
        self.realm = KerberosRealm(self.environment)
        self.addCleanup(self.realm.close)
        self.realm.start()
        self.server = GSSSSHServer(self.environment, realm=self.realm)
        self.addCleanup(self.server.close)
        self.server.start()

    def arguments(self):
        return [str(self.plink), "-batch", "-v", "-noagent", "-noshare", "-P",
                str(self.server.port), "-l", self.server.user, "-hostkey",
                self.server.host_key_fingerprint, "-T", self.server.host,
                "printf gss-authenticated"]

    def run_client(self, environment=None):
        return subprocess.run(self.arguments(), cwd=self.environment.root,
                              env=environment or self.realm.client_environment(),
                              capture_output=True, timeout=20, umask=0o077)

    def assert_authenticated(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"gss-authenticated")
        self.assertIn(b"Trying gssapi-with-mic", result.stderr)
        self.assertIn(b"GSSAPI authentication loop finished OK", result.stderr)

    def test_actual_gss_authentication_and_private_paths(self):
        child = self.realm.client_environment()
        for name, value in child.items():
            if name.startswith("KRB5"):
                self.assertIn(str(self.environment.root), value, name)
        self.assertEqual(child.get("HOME"), os.environ.get("HOME"))
        self.assertNotIn("SSH_AUTH_SOCK", child)
        self.assert_authenticated(self.run_client())
        self.assertGreaterEqual(self.realm.delay_report()["tgs_requests"], 1)
        self.assertEqual(dict(os.environ), self.original_environment)
        for path in self.realm.root.iterdir():
            self.assertEqual(path.stat().st_mode & 0o077, 0, path.name)

    def test_delays_a_real_service_ticket_request(self):
        self.realm.reset_client(delay_seconds=1.5)
        begin = time.monotonic()
        self.assert_authenticated(self.run_client())
        elapsed = time.monotonic() - begin
        report = self.realm.delay_report()
        self.assertGreaterEqual(report["requests"], 1)
        self.assertGreaterEqual(report["delayed_requests"], 1)
        self.assertGreaterEqual(report["tgs_requests"], 1)
        self.assertGreaterEqual(max(report["observed_delays"]), 1.5)
        self.assertGreaterEqual(elapsed, 1.5)
        self.assertTrue(self.realm.delay_started.is_set())

    def test_missing_credentials_fail_without_public_key_fallback(self):
        child = self.realm.client_environment()
        child["KRB5CCNAME"] = "FILE:" + str(self.realm.root / "absent.ccache")
        child["KRB5_CLIENT_KTNAME"] = "FILE:" + str(self.realm.root / "absent.keytab")
        result = self.run_client(child)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(b"gss-authenticated", result.stdout)

    def test_close_interrupts_an_in_flight_ticket_and_reaps_resources(self):
        self.realm.reset_client(delay_seconds=5)
        client = subprocess.Popen(self.arguments(), cwd=self.environment.root,
                                  env=self.realm.client_environment(), stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, umask=0o077)
        try:
            self.assertTrue(self.realm.delay_started.wait(10), "客户端未发出真实 KDC 请求")
            self.assertIsNone(client.poll())
            process = self.realm._process
            proxy_port = self.realm._proxy.port
            begin = time.monotonic()
            self.realm.close()
            self.realm.close()
            self.assertLess(time.monotonic() - begin, 1.5)
            stdout, _ = client.communicate(timeout=5)
            self.assertNotEqual(client.returncode, 0)
            self.assertNotIn(b"gss-authenticated", stdout)
            self.assertIsNotNone(process.poll())
            self.assertTrue(all(not thread.is_alive() for thread in self.realm._proxy._workers))
            for port in (proxy_port, self.realm.kdc_port):
                with self.assertRaises(OSError):
                    socket.create_connection(("127.0.0.1", port), timeout=0.2)
            with self.assertRaises(IsolationError):
                self.realm.client_environment()
        finally:
            if client.poll() is None:
                client.kill()
            client.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
