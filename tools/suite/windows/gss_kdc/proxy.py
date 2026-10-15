"""仅观察与转发真实 Kerberos TCP 消息；不生成或修改任何认证响应。"""
from __future__ import annotations

import socket
import struct
import threading
import time


class DelayedKDCProxy:
    def __init__(self, target_port: int, *, delay_seconds: float = 0):
        if not 0 <= delay_seconds <= 5:
            raise ValueError("测试延迟要求为 0 到 5 秒")
        self.target_port = target_port
        self.delay_seconds = delay_seconds
        self.delay_started = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(8)
        self._connections = set()
        self._workers = []
        self._requests = {"as": 0, "tgs": 0}
        self._responses = {"as": 0, "tgs": 0, "error": 0}
        self._observed_delays = []
        self._error = None
        self._accepted = 0
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.listener.bind(("127.0.0.1", 0))
            self.listener.listen(4)
            self.listener.settimeout(0.1)
            self.port = self.listener.getsockname()[1]
            self._thread = threading.Thread(target=self._accept)
            self._thread.start()
        except BaseException:
            self.listener.close()
            raise

    def _accept(self):
        try:
            while not self._stop.is_set():
                try:
                    client, _ = self.listener.accept()
                except TimeoutError:
                    continue
                with self._lock:
                    if self._stop.is_set():
                        client.close()
                        break
                    self._accepted += 1
                    exhausted = self._accepted >= 64
                    # 每个worker最多拥有client/upstream两只socket，总数至多16。
                    if not self._slots.acquire(blocking=False):
                        client.close()
                        if exhausted:
                            self.listener.close()
                            break
                        continue
                    self._connections.add(client)
                    worker = threading.Thread(target=self._relay, args=(client,))
                    self._workers.append(worker)
                try:
                    worker.start()
                except BaseException:
                    with self._lock:
                        self._connections.discard(client)
                        self._workers.remove(worker)
                    client.close()
                    self._slots.release()
                    raise
                if exhausted:
                    self.listener.close()
                    break
        except (OSError, RuntimeError) as error:
            if not self._stop.is_set():
                self._error = error

    @staticmethod
    def _read_frame(connection, deadline):
        def read_exact(length):
            result = bytearray()
            while len(result) < length:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                connection.settimeout(remaining)
                data = connection.recv(length - len(result))
                if not data:
                    raise EOFError()
                result.extend(data)
            return bytes(result)
        header = read_exact(4)
        length, = struct.unpack("!I", header)
        if not 1 <= length <= 65536:
            raise ValueError("KDC 消息长度越界")
        return header + read_exact(length)

    def _relay(self, client):
        upstream = None
        try:
            deadline = time.monotonic() + 12
            request = self._read_frame(client, time.monotonic() + 8)
            kind = {0x6a: "as", 0x6c: "tgs"}.get(request[4])
            if kind is None:
                raise ValueError("代理只接受 AS_REQ/TGS_REQ 应用标记")
            with self._lock:
                self._requests[kind] += 1
            if kind == "tgs" and self.delay_seconds:
                started = time.monotonic()
                self.delay_started.set()
                interrupted = self._stop.wait(self.delay_seconds)
                with self._lock:
                    self._observed_delays.append(time.monotonic() - started)
                if interrupted:
                    return
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self._stop.is_set():
                return
            upstream = socket.create_connection(("127.0.0.1", self.target_port), timeout=remaining)
            with self._lock:
                if self._stop.is_set():
                    return
                self._connections.add(upstream)
            upstream.settimeout(max(0.001, deadline - time.monotonic()))
            upstream.sendall(request)
            response = self._read_frame(upstream, deadline)
            response_kind = {0x6b: "as", 0x6d: "tgs", 0x7e: "error"}.get(response[4])
            if response_kind is None:
                raise ValueError("KDC 响应应用标记无效")
            with self._lock:
                self._responses[response_kind] += 1
            client.settimeout(max(0.001, deadline - time.monotonic()))
            client.sendall(response)
        except (EOFError, ConnectionError, TimeoutError):
            # 受测客户端在真实服务票据获取过程中关闭可以中断独占连接。
            pass
        except (OSError, ValueError) as error:
            if not self._stop.is_set():
                self._error = error
        finally:
            for connection in (client, upstream):
                if connection is not None:
                    with self._lock:
                        self._connections.discard(connection)
                    connection.close()
            self._slots.release()

    def reset(self):
        deadline = time.monotonic() + 2
        while True:
            with self._lock:
                active = bool(self._connections) or any(worker.is_alive() for worker in self._workers)
                if not active:
                    self._requests = {"as": 0, "tgs": 0}
                    self._responses = {"as": 0, "tgs": 0, "error": 0}
                    self._observed_delays.clear()
                    self.delay_started.clear()
                    return
            if time.monotonic() >= deadline:
                raise RuntimeError("不能在KDC代理仍处理连接时重置")
            time.sleep(0.01)

    def report(self):
        with self._lock:
            if self._error is not None:
                raise RuntimeError("KDC 字节代理失败") from self._error
            return {"requests": dict(self._requests), "responses": dict(self._responses),
                    "configured_delay_seconds": self.delay_seconds,
                    "observed_delay_seconds": list(self._observed_delays),
                    "active_connections": len(self._connections), "accepted_connections": self._accepted,
                    "live_workers": sum(worker.is_alive() for worker in self._workers)}

    def close(self, *, deadline=None):
        self._stop.set()
        self.listener.close()
        deadline = min(time.monotonic() + 3, float("inf") if deadline is None else deadline)
        self._thread.join(timeout=max(0, deadline - time.monotonic()))
        with self._lock:
            connections = list(self._connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        for worker in self._workers:
            worker.join(timeout=max(0, deadline - time.monotonic()))
        if self._thread.is_alive() or any(worker.is_alive() for worker in self._workers):
            raise RuntimeError("KDC 代理未在共享 3 秒期限内关闭")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
