"""不启动 Docker 的发行版选择/实际创建参数回归；不是目标系统运行证据。"""
import json
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

from tools.suite.container_ssh_fixture import ContainerSSHServer, DEFAULT_CONTAINER_PARENT, SYSTEM_PATH
from tools.suite.isolation import IsolationEnvironment
from tools.suite.ssh_fixture import FixtureUnavailable


@unittest.skipUnless(sys.platform == "linux" and os.geteuid() != 0, "仅普通用户 Linux 夹具")
class ContainerSetupTest(unittest.TestCase):
    def setUp(self):
        self.environment = IsolationEnvironment(DEFAULT_CONTAINER_PARENT)
        self.addCleanup(self.environment.close)

    def create_arguments(self, server):
        calls = []
        image_id = "sha256:" + "a" * 64

        def docker(*arguments, **unused):
            calls.append(arguments)
            if arguments[:2] == ("image", "inspect"):
                output = json.dumps([{"Id": image_id, "Os": "linux", "Architecture": "amd64"}]).encode()
            elif arguments[0] == "create":
                output = b"test-container-id\n"
            else:
                output = b""
            return subprocess.CompletedProcess(arguments, 0, output, b"")

        # 只记录创建接线，没有真实容器、线程或服务，因此没有 Docker 清理对象。
        with patch.object(server, "_docker", side_effect=docker):
            server._create()
            server.container_id = None
            server._create_attempted = False
            server.close()
        return calls, next(value for value in calls if value[0] == "create"), image_id

    def test_ubuntu_sbin_daemon_and_matching_existing_image(self):
        def which(program, **unused):
            return "/usr/sbin/sshd" if program == "sshd" else "/usr/bin/" + program

        for version in ("22.04", "24.04"):
            with self.subTest(version=version), patch("tools.suite.container_ssh_fixture.platform.freedesktop_os_release",
                    return_value={"ID": "ubuntu", "VERSION_ID": version}), patch(
                    "tools.suite.container_ssh_fixture.shutil.which", side_effect=which) as lookup:
                server = ContainerSSHServer(self.environment)
                calls, arguments, image_id = self.create_arguments(server)
                self.assertEqual(calls[0], ("image", "inspect", "ubuntu:" + version))
                self.assertEqual(arguments[arguments.index("--entrypoint") + 1], "/usr/sbin/sshd")
                self.assertEqual(arguments[arguments.index("--entrypoint") + 2], image_id)
                self.assertIn("--pull=never", arguments)
                self.assertIn("PATH=" + SYSTEM_PATH, arguments)
                lookup.assert_any_call("sshd", path=SYSTEM_PATH)

    def test_unknown_system_requires_explicit_image_before_mounts(self):
        with patch("tools.suite.container_ssh_fixture.platform.freedesktop_os_release", return_value={"ID": "unknown"}), \
                patch("tools.suite.container_ssh_fixture.shutil.which", return_value="/usr/bin/tool"), \
                patch("tools.suite.container_ssh_fixture._system_mounts") as mounts:
            with self.assertRaisesRegex(FixtureUnavailable, "显式"):
                ContainerSSHServer(self.environment)
            mounts.assert_not_called()

    def test_explicit_unknown_system_image_remains_pinned_without_pull(self):
        with patch("tools.suite.container_ssh_fixture.platform.freedesktop_os_release", return_value={"ID": "unknown"}), \
                patch("tools.suite.container_ssh_fixture.shutil.which", return_value="/usr/bin/tool"):
            server = ContainerSSHServer(self.environment, image="existing-matching-system:fixture")
            calls, arguments, image_id = self.create_arguments(server)
        self.assertEqual(calls[0], ("image", "inspect", "existing-matching-system:fixture"))
        self.assertEqual(arguments[arguments.index("--entrypoint") + 2], image_id)
        self.assertTrue(all(value[0] != "pull" for value in calls))


if __name__ == "__main__":
    unittest.main()
