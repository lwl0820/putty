#!/usr/bin/env python3
"""真实MIT客户端交叉验收；报告只包含公开身份、计数与时间。"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import socket
import struct
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.suite.windows.support import ValidationError, project_path
from tools.suite.windows.gss_kdc import PortableKDC, PreparedKDC
from tools.suite.windows.gss_kdc.process import BoundedProcess, run_bounded
from tools.suite.windows.gss_kdc.security import publish_report, utf8_streams


class _ProcessDeadline:
    """独立 CLI 的硬期限包含原生调用、私有资料清理和结果提交。"""

    def __init__(self, seconds):
        self.deadline = time.monotonic() + seconds
        self.finished = threading.Event()
        self.worker = threading.Thread(target=self._watch, daemon=True, name="mit-kdc-verify-deadline")
        self.worker.start()

    def _watch(self):
        if not self.finished.wait(max(0, self.deadline - time.monotonic())):
            os._exit(124)

    def complete(self):
        if time.monotonic() >= self.deadline:
            os._exit(124)
        self.finished.set()


def rejected(arguments, realm, environment=None):
    child = BoundedProcess(arguments, cwd=realm.root,
        environment=realm.environment if environment is None else environment,
        timeout=12, output_limit=8192, line_protocol=False)
    try:
        try:
            child.finish()
        except ValidationError:
            if child.process.returncode and child._error is None:
                return True
            raise
        raise ValidationError("应拒绝的真实MIT请求意外成功")
    finally:
        child.abort()


def closed_frame(port, content, *, length=None):
    with socket.create_connection(('127.0.0.1', port), timeout=2) as client:
        client.settimeout(2)
        client.sendall(struct.pack('!I', len(content) if length is None else length) + content)
        try:
            data = client.recv(1)
        except ConnectionError:
            data = b''
        if data:
            raise ValidationError("无效KDC帧不应产生成功响应")


def policy_probes(realm):
    """将正式库编码的负例送入真实TCP入口，精确核验策略错误码。"""
    path = realm.tree.check_file(realm.policy_probes)
    if path.stat().st_size > 8192:
        raise ValidationError("KDC拒绝向量文件过大")
    probes = json.loads(path.read_text(encoding='utf-8'))
    expected = {'Forwarded', 'Proxiable', 'Proxy', 'ConstrainedDelegation',
                'CNameInAdditionalTicket', 'EncTktInSkey', 'CrossRealm', 'AdditionalTicket'}
    if set(probes) != expected:
        raise ValidationError("KDC拒绝向量集合不符")
    def fields(value):
        # 只读取有界DER错误外壳和整数；不处理票据、密钥或任何认证状态。
        while value:
            if len(value) < 2:
                raise ValidationError('KDC错误响应DER截断')
            tag, length = value[0], value[1]
            offset = 2
            if length & 128:
                width = length & 127
                if not 1 <= width <= 3 or len(value) < 2 + width:
                    raise ValidationError('KDC错误响应DER长度非法')
                length = int.from_bytes(value[2:2 + width], 'big')
                offset += width
            if offset + length > len(value):
                raise ValidationError('KDC错误响应DER长度越界')
            yield tag, value[offset:offset + length]
            value = value[offset + length:]
    for name in sorted(expected):
        try:
            request = base64.b64decode(probes[name], validate=True)
        except (ValueError, TypeError):
            raise ValidationError('KDC拒绝向量编码非法') from None
        if not 1 <= len(request) <= 2048 or request[0] != 0x6c:
            raise ValidationError('KDC拒绝向量不是有界TGS请求')
        with socket.create_connection(('127.0.0.1', realm.kdc_port), timeout=2) as client:
            client.sendall(struct.pack('!I', len(request)) + request)
            response = realm.proxy._read_frame(client, time.monotonic() + 3)[4:]
        outer = list(fields(response))
        if len(outer) != 1 or outer[0][0] != 0x7e:
            raise ValidationError('KDC策略负例没有返回真实KRB_ERROR')
        sequence = list(fields(outer[0][1]))
        if len(sequence) != 1 or sequence[0][0] != 0x30:
            raise ValidationError('KDC策略负例错误序列非法')
        code = [value for tag, value in fields(sequence[0][1]) if tag == 0xa6]
        expected_code = b'\x02\x01\x0c' if name == 'CrossRealm' else b'\x02\x01\x0d'
        if len(code) != 1 or code[0] != expected_code:
            raise ValidationError('KDC策略负例没有返回预期错误码')
    return len(expected)


def verify(tools, parent, kinit, kvno, klist):
    tools = PreparedKDC.from_directory(tools)
    parent = project_path(parent, must_exist=True)
    for tool in (kinit, kvno, klist):
        if not tool.is_absolute() or not tool.is_file():
            raise ValidationError("MIT工具必须为调用方已核验的绝对路径")
    realm = PortableKDC(tools, parent=parent, delay_seconds=1.6)
    with realm:
        realm.reset_client(kinit)
        before = run_bounded([klist, '-c', realm.cache], cwd=realm.root,
            environment=realm.environment, timeout=8).decode('utf-8')
        if 'krbtgt/SUITE.TEST@SUITE.TEST' not in before or 'host/localhost@SUITE.TEST' in before:
            raise ValidationError("AS后缓存必须仅有本域TGT")
        started = time.monotonic()
        output = run_bounded([kvno, '-k', realm.keytab, 'host/localhost@SUITE.TEST'],
            cwd=realm.root, environment=realm.environment, timeout=12).decode('utf-8')
        elapsed = time.monotonic() - started
        if 'kvno = 1' not in output or not realm.delay_started.is_set() or elapsed < 1.5:
            raise ValidationError("缺少真实TGS延迟或服务keytab解密证据")
        if not rejected([kvno, '-k', realm.keytab, 'host/unknown@SUITE.TEST'], realm):
            raise ValidationError("未知服务未拒绝")
        rejected([kinit, '-f', '-k', '-t', realm.client_keytab, 'suite@SUITE.TEST'], realm)
        rejected_count = policy_probes(realm)
        other = realm.tree.write(realm.root / 'other.conf', realm._configuration(realm.kdc_port).replace(
            b' SUITE.TEST = {', b' OTHER.TEST = {'))
        rejected([kinit, '-k', '-t', realm.client_keytab, 'suite@OTHER.TEST'], realm,
            {**realm.environment, 'KRB5_CONFIG': str(other)})
        realm.reset_client(kinit)
        after = run_bounded([klist, '-c', realm.cache], cwd=realm.root,
            environment=realm.environment, timeout=8).decode('utf-8')
        if 'host/localhost@SUITE.TEST' in after:
            raise ValidationError("重新AS后仍残留服务票据")
        # 再请求一次真实TGS，使最终代理摘要包含完整成功响应证据。
        run_bounded([kvno, '-k', realm.keytab, 'host/localhost@SUITE.TEST'],
            cwd=realm.root, environment=realm.environment, timeout=12)
        closed_frame(realm.kdc_port, b'', length=65537)
        closed_frame(realm.kdc_port, b'', length=0)
        closed_frame(realm.kdc_port, b'\x6c\x00')
        refused = False
        for _ in range(80):
            try:
                closed_frame(realm.kdc_port, b'', length=65537)
            except ConnectionError:
                refused = True
                break
        if not refused:
            raise ValidationError("KDC连接总预算未停止监听")
    first = realm.report()
    if first['kdc']['accepted'] != 64 or first['kdc']['malformed'] < 1:
        raise ValidationError("KDC总预算或正式解析失败计数不符")
    busy = PortableKDC(tools, parent=parent)
    connections = []
    try:
        busy.start()
        for _ in range(8):
            client = socket.create_connection(('127.0.0.1', busy.kdc_port), timeout=2)
            connections.append(client)
            client.sendall(b'\0')
        for _ in range(9):
            client = socket.create_connection(('127.0.0.1', busy.proxy.port), timeout=2)
            connections.append(client)
            try:
                client.sendall(b'\0')
            except ConnectionError:
                pass
        deadline = time.monotonic() + 2
        while busy.delay_report()['accepted_connections'] < 9 and time.monotonic() < deadline:
            time.sleep(0.01)
        if busy.delay_report()['active_connections'] != 8:
            raise ValidationError("KDC代理没有执行最多8个双socket worker的预算")
        # 不以sleep冒充TGS；仅向独占socket发送部分header，验证取消真实在途读取。
        started = time.monotonic()
        busy.close()
        close_seconds = time.monotonic() - started
        if close_seconds > 6:
            raise ValidationError("全部在途KDC连接未在共享期限内关闭")
    finally:
        for client in connections:
            client.close()
        busy.close(failed=True)
    second = busy.report()
    if second['kdc']['rejected'] < 1:
        raise ValidationError("未观察到真实并发连接拒绝")
    result = {'schema': 1, 'kind': 'real-mit-kdc-crosscheck', 'passed': True,
        'native_windows': sys.platform == 'win32', 'only_tgt_after_as': True,
        'service_keytab_decryption_verified': True, 'kvno': 1,
        'tgs_elapsed_seconds': elapsed, 'forwardable_as_rejected': True,
        'forwarding_rejected': True, 'network_policy_probes': rejected_count,
        'cross_realm_rejected': True, 'unknown_service_rejected': True,
        'reset_removes_service_ticket': True, 'oversize_zero_malformed_rejected': True,
        'connection_budget': 64, 'inflight_close_seconds': close_seconds,
        'proxy_worker_budget': 8, 'proxy_socket_budget': 16,
        'normal': first, 'inflight': second}
    if str(parent) in json.dumps(result):
        raise ValidationError("公开KDC报告出现私有绝对路径")
    return result


def main(*, deadline_seconds=180):
    utf8_streams()
    parser = argparse.ArgumentParser(description='受控KDC真实MIT验收')
    parser.add_argument('--tools', type=Path, required=True)
    parser.add_argument('--parent', type=Path, required=True)
    parser.add_argument('--kinit', type=Path, required=True)
    parser.add_argument('--kvno', type=Path, required=True)
    parser.add_argument('--klist', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    options = parser.parse_args()
    try:
        report = project_path(options.report)
        publish_report(report, {'schema': 1, 'kind': 'real-mit-kdc-crosscheck', 'passed': False})
    except Exception:
        print('受控KDC验收占位报告失败', file=sys.stderr, flush=True)
        return 1
    try:
        deadline = _ProcessDeadline(deadline_seconds)
    except Exception:
        print('受控KDC验收期限启动失败', file=sys.stderr, flush=True)
        return 1
    try:
        result = verify(options.tools, options.parent, options.kinit, options.kvno, options.klist)
        # 先刷新候选，刷新挂起或失败时最终文件仍为已落盘的失败占位。
        print(json.dumps(result, ensure_ascii=False), flush=True)
        publish_report(report, result)
    except Exception:
        print('受控KDC真实MIT验收失败', file=sys.stderr, flush=True)
        deadline.complete()
        return 1
    deadline.complete()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
