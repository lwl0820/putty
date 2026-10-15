"""隔离、归档与真实子进程的失败契约；测试临时文件只在项目.tmp。"""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time
import unittest
from unittest import mock
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(ROOT))
from tools.suite.windows.support import ValidationError
from tools.suite.windows.gss_kdc.archive import MAX_FILE, SafeArchive
from tools.suite.windows.gss_kdc.process import BoundedProcess, run_bounded
from tools.suite.windows.gss_kdc.security import PrivateTree, closed_environment, publish_report
from tools.suite.windows.gss_kdc.prepare import prepare
from tools.suite.windows.gss_kdc.fixture import PortableKDC, PreparedKDC, decode_metadata


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.root = ROOT / ".tmp" / ("kdc-contract-" + uuid.uuid4().hex)
        self.tree = PrivateTree(self.root)

    def tearDown(self):
        self.tree.close()
        import shutil
        shutil.rmtree(self.root)

    def archive(self, entries, *, zipped=False):
        path = self.root / ("test.zip" if zipped else "test.tar.gz")
        if zipped:
            with zipfile.ZipFile(path, "w") as archive:
                for name, content in entries:
                    archive.writestr(name, content)
        else:
            with tarfile.open(path, "w:gz") as archive:
                for name, content, kind in entries:
                    info = tarfile.TarInfo(name)
                    info.type = kind
                    info.size = len(content)
                    if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                        info.linkname = "outside"
                    archive.addfile(info, io.BytesIO(content))
        return path

    def test_closed_environment_rejects_all_inherited_injection_and_credentials(self):
        hostile = {name: "错误外部目录" for name in ("HOME", "USERPROFILE", "APPDATA", "XDG_CONFIG_HOME",
            "DOTNET_STARTUP_HOOKS", "DOTNET_ROOT", "COMPlus_EnableDiagnostics", "CORECLR_PROFILER", "COR_ENABLE_PROFILING",
            "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "SSL_CERT_FILE", "OPENSSL_CONF", "KRB5_CONFIG", "NUGET_PACKAGES",
            "SSH_AUTH_SOCK", "PYTHONPATH", "MSBUILD_EXE_PATH", "PATH")}
        hostile["SystemRoot"] = "C:/Windows"
        before = dict(hostile)
        environment = closed_environment(self.tree, base=hostile)
        self.assertEqual(before, hostile)
        self.assertTrue(all("错误外部目录" not in value for value in environment.values()))
        self.assertNotIn("CORECLR_PROFILER", environment)
        for name in ("HOME", "USERPROFILE", "APPDATA", "XDG_CONFIG_HOME", "NUGET_PACKAGES", "TMPDIR"):
            self.assertTrue(Path(environment[name]).is_relative_to(self.root))
        self.assertEqual(environment["DOTNET_GENERATE_ASPNET_CERTIFICATE"], "false")

    def test_archive_rejects_escape_ambiguous_and_duplicate_paths(self):
        for name in ("../escape", "/absolute", "C:/drive", "a\\b", "a/./b", "a//b", "AUX.txt", "name. "):
            path = self.archive([(name, b"x", tarfile.REGTYPE)])
            with self.assertRaises(ValidationError):
                SafeArchive(path)
            path.unlink()
        for entries in ([('a',b'x'),('a',b'y')], [('A',b'x'),('a',b'y')]):
            path = self.archive(entries, zipped=True)
            with self.assertRaises(ValidationError):
                SafeArchive(path)
            path.unlink()

    def test_archive_rejects_links_and_declared_size(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE):
            path = self.archive([("linked", b"", kind)])
            with self.assertRaises(ValidationError):
                SafeArchive(path)
            path.unlink()
        path = self.root / 'large.tar.gz'
        with tarfile.open(path,'w:gz') as archive:
            info = tarfile.TarInfo('large');info.size = MAX_FILE + 1
            # 无需构造压缩炸弹；有界声明在读取成员内容前即拒绝。
            archive.fileobj.write(info.tobuf())
        with self.assertRaises((ValidationError, tarfile.ReadError)):
            SafeArchive(path)

    def test_archive_verify_detects_changes_extra_and_links(self):
        path = self.archive([("a/file", b"expected", tarfile.REGTYPE)])
        target = PrivateTree(self.root / "sdk")
        try:
            with SafeArchive(path) as archive:
                archive.extract(target)
                self.assertEqual(archive.verify(target)["files"],1)
                (target.root/'a/file').write_bytes(b'changed')
                with self.assertRaises(ValidationError):archive.verify(target)
                (target.root/'a/file').write_bytes(b'expected')
                target.write(target.root/'extra',b'x')
                with self.assertRaises(ValidationError):archive.verify(target)
                (target.root/'extra').unlink()
                target.directory(target.root/'extra-directory')
                with self.assertRaises(ValidationError):archive.verify(target)
        finally:target.close()

    def test_private_files_reject_hardlinks(self):
        path = self.tree.write(self.root/'one',b'x')
        os.link(path,self.root/'two')
        with self.assertRaises(ValidationError):self.tree.check_file(path)

    @unittest.skipIf(os.name == 'nt', 'Windows私有父目录使用原生ACL契约')
    def test_runtime_rejects_nonprivate_parent_before_creating_credentials(self):
        parent = self.tree.directory(self.root / 'unsafe-parent')
        parent.chmod(0o755)
        prepared = PreparedKDC(self.root, Path(sys.executable), self.root / 'unused.dll', {})
        with self.assertRaises(ValidationError):
            PortableKDC(prepared, parent=parent)
        self.assertEqual(list(parent.iterdir()), [])

    def test_runtime_rejects_duplicate_start_and_restart_without_spawning(self):
        prepared = PreparedKDC(self.root, Path(sys.executable), self.root / 'unused.dll', {})
        realm = PortableKDC(prepared, parent=self.root)
        try:
            realm.child = object()
            with mock.patch('tools.suite.windows.gss_kdc.fixture.BoundedProcess') as spawn:
                with self.assertRaisesRegex(ValidationError, '重复启动'):
                    realm.start()
                spawn.assert_not_called()
            realm.child = None
            realm.close(failed=True)
            with mock.patch('tools.suite.windows.gss_kdc.fixture.BoundedProcess') as spawn:
                with self.assertRaisesRegex(ValidationError, '重复启动'):
                    realm.start()
                spawn.assert_not_called()
        finally:
            realm.child = None
            realm.close(failed=True)

    def test_metadata_rejects_duplicate_fields_constants_and_nonobjects(self):
        for payload in ('{"ready":false,"ready":true}', '{"nested":{"count":1,"count":0}}',
                        '{"count":NaN}', '{"count":Infinity}', '{"count":-Infinity}',
                        '{"count":1e309}', '{"nested":[-1e309]}', '[]', 'null', b'\xff'):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                decode_metadata(payload)
        self.assertEqual(decode_metadata('{"schema":1,"ready":true}'), {"schema": 1, "ready": True})
        self.assertEqual(decode_metadata('{"count":1.25}'), {"count": 1.25})
        # 各 Python 版本的解析器深度限制不同；统一转换其真实失败类型。
        with mock.patch('tools.suite.windows.gss_kdc.fixture.json.loads', side_effect=RecursionError), \
                self.assertRaises(ValidationError):
            decode_metadata('{}')

    def test_prepared_metadata_rejects_ambiguous_schema_and_nested_duplicate_before_sdk_access(self):
        from tools.suite.windows.gss_kdc.prepare import source_hash
        private = PrivateTree(self.root / 'prepared-metadata')
        state = {"schema": 1, "source_sha256": source_hash(),
                 "rid": 'win-x64' if os.name == 'nt' else 'linux-x64',
                 "artifact": 'artifact-' + '0' * 32, "files": {"one.dll": '0' * 64, "two.dll": '1' * 64}}
        path = private.write(private.root / 'prepared.json', b'{}')
        payloads = [json.dumps(dict(state, schema=value)) for value in (True, 1.0)]
        payloads.append(json.dumps(state).replace('"schema": 1', '"schema": false, "schema": 1'))
        payloads.append(json.dumps(state).replace('"one.dll":', '"one.dll": "wrong", "one.dll":'))
        try:
            for payload in payloads:
                with self.subTest(payload=payload):
                    path.write_text(payload, encoding='utf-8')
                    with mock.patch('tools.suite.windows.gss_kdc.fixture.SafeArchive') as archive:
                        with self.assertRaises(ValidationError):
                            PreparedKDC.from_directory(private.root)
                        archive.assert_not_called()
        finally:
            private.close()

    def test_ready_and_close_crlf_metadata_keeps_valid_protocol_and_cleanup(self):
        prepared = PreparedKDC(self.root, Path(sys.executable), self.root / 'unused.dll', {})
        ready = {"schema": 1, "ready": True, "realm": "SUITE.TEST", "client": "suite@SUITE.TEST",
                 "service": "host/localhost@SUITE.TEST", "kdc_port": 12345}
        summary = {"schema": 1, "stopped": True, "accepted": 0, "completed": 0, "rejected": 0,
                   "malformed": 0, "as_requests": 0, "tgs_requests": 0,
                   "active_connections": 0, "active_workers": 0}
        realm = PortableKDC(prepared, parent=self.root)
        child = mock.Mock()
        child.line.side_effect = [(json.dumps(value) + '\r').encode('utf-8') for value in (ready, summary)]
        proxy = mock.Mock(port=23456)
        proxy.report.return_value = {"passed": True}
        try:
            with mock.patch('tools.suite.windows.gss_kdc.fixture.BoundedProcess', return_value=child), \
                    mock.patch('tools.suite.windows.gss_kdc.fixture.DelayedKDCProxy', return_value=proxy):
                realm.start()
                realm.close()
            self.assertTrue(realm._data_cleaned)
            self.assertFalse(realm.root.exists())
            self.assertTrue(realm.report()['passed'])
            proxy.close.assert_called_once()
            child.abort.assert_called()
        finally:
            realm.close(failed=True)

    def test_ready_protocol_rejects_ambiguous_schema_before_proxy_start(self):
        prepared = PreparedKDC(self.root, Path(sys.executable), self.root / 'unused.dll', {})
        valid = {"schema": 1, "ready": True, "realm": "SUITE.TEST", "client": "suite@SUITE.TEST",
                 "service": "host/localhost@SUITE.TEST", "kdc_port": 12345}
        payloads = [json.dumps(dict(valid, schema=value)) for value in (True, 1.0)]
        payloads.append(json.dumps(valid).replace('"ready": true', '"ready": false, "ready": true'))
        for payload in payloads:
            with self.subTest(payload=payload):
                realm = PortableKDC(prepared, parent=self.root)
                child = mock.Mock()
                child.line.return_value = payload.encode('utf-8')
                with mock.patch('tools.suite.windows.gss_kdc.fixture.BoundedProcess', return_value=child), \
                        mock.patch('tools.suite.windows.gss_kdc.fixture.DelayedKDCProxy') as proxy:
                    with self.assertRaises(ValidationError):
                        realm.start()
                    proxy.assert_not_called()
                child.abort.assert_called()
                self.assertFalse(realm._data_cleaned)
                self.assertTrue(realm.root.is_dir())
                with self.assertRaises(ValidationError):
                    realm.report()

    def test_close_protocol_rejects_ambiguous_schema_and_keeps_failed_data(self):
        prepared = PreparedKDC(self.root, Path(sys.executable), self.root / 'unused.dll', {})
        valid = {"schema": 1, "stopped": True, "accepted": 0, "completed": 0, "rejected": 0,
                 "malformed": 0, "as_requests": 0, "tgs_requests": 0,
                 "active_connections": 0, "active_workers": 0}
        payloads = [json.dumps(dict(valid, schema=value)) for value in (True, 1.0)]
        payloads.append(json.dumps(valid).replace('"active_workers": 0', '"active_workers": 1, "active_workers": 0'))
        for payload in payloads:
            with self.subTest(payload=payload):
                realm = PortableKDC(prepared, parent=self.root)
                realm.child = mock.Mock()
                realm.child.line.return_value = payload.encode('utf-8')
                with self.assertRaises(ValidationError):
                    realm.close()
                realm.child.abort.assert_called()
                self.assertFalse(realm._data_cleaned)
                self.assertTrue(realm.root.is_dir())
                with self.assertRaises(ValidationError):
                    realm.report()

    def test_prepare_failure_invalidates_previous_success_before_fetch(self):
        private = PrivateTree(self.root / 'prepared')
        try:
            state = private.write(private.root / 'prepared.json', b'{"passed":true}')
            with mock.patch('tools.suite.windows.gss_kdc.prepare.fetch', side_effect=ValidationError('固定下载失败')):
                with self.assertRaises(ValidationError):
                    prepare(private.root)
            self.assertFalse(state.exists())
        finally:
            private.close()

    def test_fetch_uses_bytes_at_actual_ctypes_pointer_boundary(self):
        import ctypes as C
        import hashlib
        import importlib
        module = importlib.import_module('tools.suite.windows.gss_kdc.prepare')
        content = b'fixed-public-download-content'
        # 真实执行 WriteFile 缓冲区的 c_void_p 参数转换；不模拟 ctypes 接受 bytearray。
        signature = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p, C.c_uint32,
                                C.POINTER(C.c_uint32), C.c_void_p)
        calls = []
        def converted(_handle, pointer, length, written, _overlapped):
            calls.append(C.string_at(pointer, length))
            written[0] = length
            return 1
        write_file = signature(converted)
        written = C.c_uint32()
        with self.assertRaises(C.ArgumentError):
            write_file(1, bytearray(content), len(content), C.byref(written), None)
        self.assertEqual(calls, [])
        original_write = self.tree.write
        def pointer_checked_write(path, value):
            actual = C.c_uint32()
            self.assertEqual(write_file(1, value, len(value), C.byref(actual), None), 1)
            self.assertEqual(actual.value, len(content))
            return original_write(path, value)
        for algorithm in ('sha256', 'sha512'):
            with self.subTest(algorithm=algorithm):
                target = self.root / ('download-' + algorithm)
                url = 'https://builds.dotnet.microsoft.com/dotnet/fixed-download'
                source = io.BytesIO(content)
                source.geturl = lambda: url
                opener = mock.Mock()
                opener.open.return_value = source
                with mock.patch.object(module.urllib.request, 'build_opener', return_value=opener), \
                        mock.patch.object(self.tree, 'write', side_effect=pointer_checked_write):
                    self.assertEqual(module.fetch(self.tree, url, target,
                        hashlib.new(algorithm, content).hexdigest(), algorithm=algorithm), target)
                self.assertEqual(target.read_bytes(), content)
        self.assertEqual(calls, [content, content])

    def test_fetch_rejects_hash_mismatch_before_writing_download(self):
        import importlib
        module = importlib.import_module('tools.suite.windows.gss_kdc.prepare')
        for algorithm, width in (('sha256', 64), ('sha512', 128)):
            with self.subTest(algorithm=algorithm):
                target = self.root / ('rejected-' + algorithm)
                url = 'https://api.nuget.org/v3-flatcontainer/fixed-download'
                source = io.BytesIO(b'changed-public-download')
                source.geturl = lambda: url
                opener = mock.Mock()
                opener.open.return_value = source
                with mock.patch.object(module.urllib.request, 'build_opener', return_value=opener), \
                        mock.patch.object(self.tree, 'write') as write:
                    with self.assertRaisesRegex(ValidationError, '哈希不符'):
                        module.fetch(self.tree, url, target, '0' * width, algorithm=algorithm)
                    write.assert_not_called()
                self.assertFalse(target.exists())

    def test_failed_verification_overwrites_previous_success_summary(self):
        import contextlib
        import importlib
        module = importlib.import_module('tools.suite.windows.gss_kdc.verify')
        report = self.tree.write(self.root / 'report.json', b'{"passed":true}')
        arguments = ['verify.py', '--tools', str(self.root), '--parent', str(self.root),
                     '--kinit', str(Path(sys.executable)), '--kvno', str(Path(sys.executable)),
                     '--klist', str(Path(sys.executable)), '--report', str(report)]
        with mock.patch.object(sys, 'argv', arguments), \
             mock.patch.object(module, 'verify', side_effect=ValidationError('预期失败')), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(module.main(), 1)
        self.assertIs(json.loads(report.read_text())['passed'], False)

    def verification_cli(self):
        import importlib
        module = importlib.import_module('tools.suite.windows.gss_kdc.verify')
        report = self.tree.write(self.root / 'verify-report.json', b'{"passed":true}')
        arguments = ['verify.py', '--tools', str(self.root), '--parent', str(self.root),
                     '--kinit', str(Path(sys.executable)), '--kvno', str(Path(sys.executable)),
                     '--klist', str(Path(sys.executable)), '--report', str(report)]
        return module, report, arguments

    def test_verification_flushes_candidate_before_final_publication(self):
        module, report, arguments = self.verification_cli()
        original_publish, events = module.publish_report, []

        class CandidateStream:
            def write(_, value):
                events.append('write')
                return len(value)

            def flush(_):
                events.append('flush')

        def publish(path, result):
            if result['passed']:
                self.assertIn('flush', events)
                self.assertFalse(json.loads(report.read_text(encoding='utf-8'))['passed'])
                events.append('commit')
            original_publish(path, result)

        with mock.patch.object(sys, 'argv', arguments), \
                mock.patch.object(module, 'verify', return_value={'passed': True}), \
                mock.patch.object(sys, 'stdout', CandidateStream()), \
                mock.patch.object(module, 'publish_report', side_effect=publish):
            self.assertEqual(module.main(), 0)
        self.assertEqual(events[-1], 'commit')
        self.assertTrue(json.loads(report.read_text(encoding='utf-8'))['passed'])

    def test_verification_output_report_and_deadline_start_failure_keep_pending(self):
        module, report, arguments = self.verification_cli()
        original_publish = module.publish_report

        class FailedStream:
            def write(_, value):
                return len(value)

            def flush(_):
                raise OSError('内部输出失败')

        for mode in ('stdout', 'report', 'deadline'):
            with self.subTest(mode=mode):
                report.write_text('{"passed":true}', encoding='utf-8')
                def publish(path, result):
                    if mode == 'report' and result['passed']:
                        raise OSError('内部报告失败')
                    original_publish(path, result)
                stream = FailedStream() if mode == 'stdout' else io.StringIO()
                with mock.patch.object(sys, 'argv', arguments), \
                        mock.patch.object(module, 'verify', return_value={'passed': True}) as execute, \
                        mock.patch.object(sys, 'stdout', stream), mock.patch.object(sys, 'stderr'), \
                        mock.patch.object(module, 'publish_report', side_effect=publish):
                    if mode == 'deadline':
                        with mock.patch.object(module.threading.Thread, 'start', side_effect=RuntimeError('内部启动失败')):
                            self.assertEqual(module.main(), 1)
                        execute.assert_not_called()
                    else:
                        self.assertEqual(module.main(), 1)
                self.assertFalse(json.loads(report.read_text(encoding='utf-8'))['passed'])

    def test_real_verification_deadline_covers_execution_cleanup_stdout_and_report(self):
        report = self.root / 'verify-report.json'
        script = r'''
import importlib
from pathlib import Path
import sys
import time
module = importlib.import_module('tools.suite.windows.gss_kdc.verify')
root, mode = Path(sys.argv[1]), sys.argv[2]
def hang(*_):
    (root / ("entered-" + mode)).write_text("true", encoding="ascii")
    while True:
        time.sleep(1)
def verified(*_):
    try:
        if mode == "verify":
            hang()
        return {"schema": 1, "kind": "real-mit-kdc-crosscheck", "passed": True}
    finally:
        if mode == "cleanup":
            hang()
module.verify = verified
if mode == "stdout":
    class HungStream:
        def write(self, value):
            return len(value)
        def flush(self):
            hang()
    sys.stdout = HungStream()
elif mode == "report":
    publish = module.publish_report
    def final_report(path, result):
        if result["passed"]:
            hang()
        publish(path, result)
    module.publish_report = final_report
sys.argv = ["verify.py", "--tools", str(root), "--parent", str(root),
            "--kinit", sys.executable, "--kvno", sys.executable, "--klist", sys.executable,
            "--report", str(root / "verify-report.json")]
raise SystemExit(module.main(deadline_seconds=2))
'''
        for mode in ('verify', 'cleanup', 'stdout', 'report'):
            with self.subTest(mode=mode):
                report.write_text('{"passed":true}', encoding='utf-8')
                child = subprocess.run([sys.executable, '-B', '-c', script, str(self.root), mode],
                    cwd=ROOT, capture_output=True, timeout=15)
                self.assertEqual(child.returncode, 124, child.stderr.decode('utf-8', errors='replace'))
                self.assertEqual((self.root / ('entered-' + mode)).read_text(encoding='ascii'), 'true')
                self.assertFalse(json.loads(report.read_text(encoding='utf-8'))['passed'])

    def test_private_tree_cleanup_removes_only_verified_plain_tree(self):
        private = PrivateTree(self.root / 'private')
        private.write(private.root / 'nested/file', b'test')
        private.remove()
        self.assertFalse(private.root.exists())

    def test_private_tree_cleanup_preserves_unknown_file_and_directory(self):
        for directory in (False, True):
            private = PrivateTree(self.root / ('unknown-' + str(directory)))
            known = private.write(private.root / 'known', b'keep-known')
            unknown = private.root / 'unknown'
            if directory:
                unknown.mkdir(mode=0o700)
            else:
                unknown.write_bytes(b'keep-unknown')
                unknown.chmod(0o600)
            try:
                with self.assertRaisesRegex(ValidationError, '未知'):
                    private.remove()
                self.assertEqual(known.read_bytes(), b'keep-known')
                self.assertTrue(unknown.exists())
            finally:
                private.close()

    @unittest.skipIf(os.name == 'nt', 'Windows清理使用固定对象句柄，不使用目录fd')
    def test_cleanup_refuses_unknown_file_added_after_preflight(self):
        private = PrivateTree(self.root / 'late-content')
        private.write(private.root / 'known', b'known')
        unknown = private.root / 'late'
        original = os.listdir
        injected = False
        def listing(descriptor):
            nonlocal injected
            if isinstance(descriptor, int) and not injected:
                injected = True
                unknown.write_bytes(b'keep')
                unknown.chmod(0o600)
            return original(descriptor)
        try:
            with mock.patch('tools.suite.windows.gss_kdc.security.os.listdir', side_effect=listing):
                # 新增项可由重扫发现，或由最终rmdir的非空检查拒绝；两者均不得删未知文件。
                with self.assertRaises((ValidationError, OSError)):
                    private.remove()
            self.assertEqual(unknown.read_bytes(), b'keep')
        finally:
            private.close()

    def test_artifact_registration_is_exact_and_rejects_missing_parent_and_links(self):
        private = PrivateTree(self.root / 'registered')
        try:
            with self.assertRaises((ValidationError, OSError)):
                private.register_artifact(private.root / 'missing')
            with self.assertRaises(ValidationError):
                private.register_artifact(self.root / 'outside')
            nested = private.root / 'nested'
            nested.mkdir(mode=0o700)
            if os.name == 'nt':
                # 原生创建需沿公共ACL，不能用Windows继承权限冒充已核验目录。
                nested.rmdir()
                private.directory(nested)
            leaf = nested / 'file'
            leaf.write_bytes(b'test')
            leaf.chmod(0o600)
            if os.name != 'nt':
                with self.assertRaisesRegex(ValidationError, '父目录'):
                    private.register_artifact(leaf)
                private.register_artifact(nested, directory=True)
            with self.assertRaisesRegex(ValidationError, '未知'):
                private.remove()
            private.register_artifact(leaf)
            os.link(leaf, nested / 'other')
            with self.assertRaises(ValidationError):
                private.register_artifact(nested / 'other')
            (nested / 'other').unlink()
            private.remove()
            self.assertFalse(private.root.exists())
        finally:
            private.close()

    def test_report_replace_is_atomic_fsynced_and_failed_commit_keeps_pending(self):
        report = self.tree.write(self.root / 'atomic.json', b'{"passed":true}')
        pending = {'passed': False}
        original_replace, original_fsync = os.replace, os.fsync
        observed = []
        def replace(source, destination):
            observed.append(json.loads(Path(destination).read_text())['passed'])
            self.assertIn('passed', json.loads(Path(source).read_text()))
            return original_replace(source, destination)
        with mock.patch('tools.suite.windows.gss_kdc.security.os.replace', side_effect=replace), \
             mock.patch('tools.suite.windows.gss_kdc.security.os.fsync', wraps=original_fsync) as fsync:
            publish_report(report, pending)
            self.assertEqual(fsync.call_count, 1)
        self.assertEqual(observed, [True])
        self.assertIs(json.loads(report.read_text())['passed'], False)
        with mock.patch('tools.suite.windows.gss_kdc.security.os.replace', side_effect=OSError('测试替换失败')):
            with self.assertRaises(OSError):
                publish_report(report, {'passed': True})
        self.assertIs(json.loads(report.read_text())['passed'], False)
        self.assertEqual(list(self.root.glob('.kdc-report-*')), [])

    @unittest.skipIf(os.name == 'nt', 'Windows reparse由原生ACL测试核验')
    def test_private_tree_cleanup_refuses_link_without_touching_target(self):
        private = PrivateTree(self.root / 'private')
        outside = self.tree.write(self.root / 'preserve', b'keep')
        os.symlink(outside, private.root / 'link')
        try:
            with self.assertRaises(ValidationError):
                private.remove()
            self.assertEqual(outside.read_bytes(), b'keep')
        finally:
            private.close()

    def child(self, program, **options):
        return BoundedProcess([sys.executable,'-I','-c',program],cwd=self.root,
            environment=closed_environment(self.tree),timeout=options.pop('timeout',3),**options)

    def test_real_child_output_overflow_and_metadata_line_are_fatal(self):
        for code in ('import sys;sys.stdout.write("x"*5000);sys.stdout.flush()',
                     'import sys;sys.stderr.write("x"*20000);sys.stderr.flush()'):
            child=self.child(code)
            try:
                with self.assertRaises(ValidationError):child.finish()
            finally:child.abort()
            self.assertFalse(any(worker.is_alive() for worker in child._threads))

    def test_real_child_timeout_and_bad_start_are_closed(self):
        started=time.monotonic()
        child=self.child('import time;time.sleep(10)',timeout=0.2)
        try:
            with self.assertRaises(ValidationError):child.line(timeout=0.5)
        finally:child.abort()
        self.assertLess(time.monotonic()-started,3)
        self.assertIsNotNone(child.process.poll())
        self.assertFalse(any(worker.is_alive() for worker in child._threads))

    def test_timer_start_failure_still_reaps_process_and_closes_pipes(self):
        import subprocess
        original = subprocess.Popen
        created = []
        def capture(*args, **kwargs):
            process = original(*args, **kwargs)
            created.append(process)
            return process
        with mock.patch('tools.suite.windows.gss_kdc.process.subprocess.Popen', side_effect=capture), \
             mock.patch('tools.suite.windows.gss_kdc.process.threading.Timer.start', side_effect=RuntimeError('测试线程启动失败')):
            with self.assertRaisesRegex(RuntimeError, '测试线程启动失败'):
                self.child('import time;time.sleep(60)')
        self.assertEqual(len(created), 1)
        self.assertIsNotNone(created[0].poll())
        self.assertTrue(all(stream.closed for stream in (created[0].stdin, created[0].stdout, created[0].stderr)))

    def test_bounded_build_output_can_have_many_lines(self):
        output=run_bounded([sys.executable,'-I','-c','print("ok\\n"*12)'],cwd=self.root,
            environment=closed_environment(self.tree),timeout=3)
        self.assertEqual(output.count(b'ok'),12)

    def test_metadata_tail_without_newline_is_rejected(self):
        child = self.child('import sys;sys.stdout.write("{}")')
        try:
            with self.assertRaisesRegex(ValidationError, "缺少换行"):
                child.finish()
        finally:
            child.abort()
        self.assertTrue(child.process.stdout.closed)

    def test_exited_parent_cannot_leave_descendant_holding_pipes(self):
        program = ('import subprocess,sys;'
            'subprocess.Popen([sys.executable,"-I","-c","import time;time.sleep(60)"],'
            'stdout=sys.stdout,stderr=sys.stderr);print("ready",flush=True)')
        child = self.child(program, timeout=0.3)
        try:
            self.assertEqual(child.line(), b'ready\r' if os.name == 'nt' else b'ready')
            limit = time.monotonic() + 2
            while any(worker.is_alive() for worker in child._threads) and time.monotonic() < limit:
                time.sleep(0.01)
            with self.assertRaises(ValidationError):
                child.finish()
        finally:
            child.abort()
        self.assertFalse(any(worker.is_alive() for worker in child._threads))
        self.assertIsNotNone(child.process.returncode)

    def test_one_stream_close_failure_still_closes_other_streams(self):
        child = self.child('print("done")')
        original = child.process.stdin
        wrapped = mock.Mock(wraps=original)
        wrapped.close.side_effect = OSError('测试关闭错误')
        child.process.stdin = wrapped
        try:
            with self.assertRaisesRegex(ValidationError, "标准流关闭失败"):
                child.finish()
        finally:
            original.close()
            child.abort()
        self.assertTrue(child.process.stdout.closed)
        self.assertTrue(child.process.stderr.closed)
        self.assertFalse(any(worker.is_alive() for worker in child._threads))

    @unittest.skipUnless(os.name == 'nt', 'Windows原生挂起进程与Job契约')
    def test_job_failure_never_runs_suspended_child_and_closes_all_pipes(self):
        marker = self.root / 'must-not-exist'
        import subprocess
        original = subprocess.Popen
        created = []
        def capture(*args, **kwargs):
            process = original(*args, **kwargs)
            created.append(process)
            return process
        with mock.patch('tools.suite.windows.gss_kdc.process.subprocess.Popen', side_effect=capture), \
             mock.patch('tools.suite.windows.gss_kdc.process._Job', side_effect=ValidationError('测试Job失败')):
            with self.assertRaises(ValidationError):
                self.child('from pathlib import Path;Path(' + repr(str(marker)) + ').write_text("bad")')
        self.assertFalse(marker.exists())
        self.assertEqual(len(created), 1)
        self.assertIsNotNone(created[0].poll())
        self.assertTrue(all(stream.closed for stream in (created[0].stdin, created[0].stdout, created[0].stderr)))


if __name__ == '__main__':
    unittest.main()
