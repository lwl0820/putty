"""标准流与总期限均有界的独占进程。"""
from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time

from tools.suite.windows.support import ValidationError


class _Job:
    """主线程仍挂起时先进入 Job，成功后才允许执行用户代码。"""
    def __init__(self, process):
        import ctypes as C
        from ctypes import wintypes as W

        class Basic(C.Structure):
            _fields_ = [("process_time", C.c_int64), ("job_time", C.c_int64), ("flags", W.DWORD),
                        ("min_work", C.c_size_t), ("max_work", C.c_size_t), ("active", W.DWORD),
                        ("affinity", C.c_size_t), ("priority", W.DWORD), ("scheduling", W.DWORD)]

        class IO(C.Structure):
            _fields_ = [(name, C.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]

        class Extended(C.Structure):
            _fields_ = [("basic", Basic), ("io", IO), ("process_memory", C.c_size_t),
                        ("job_memory", C.c_size_t), ("peak_process", C.c_size_t), ("peak_job", C.c_size_t)]

        class ThreadEntry(C.Structure):
            _fields_ = [("size", W.DWORD), ("usage", W.DWORD), ("thread", W.DWORD),
                        ("owner", W.DWORD), ("priority", W.LONG), ("delta", W.LONG), ("flags", W.DWORD)]

        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.handle = None
        self.lock = threading.Lock()
        for name, args, result in (
            ("CreateJobObjectW", [C.c_void_p, W.LPCWSTR], W.HANDLE),
            ("SetInformationJobObject", [W.HANDLE, C.c_int, C.c_void_p, W.DWORD], W.BOOL),
            ("AssignProcessToJobObject", [W.HANDLE, W.HANDLE], W.BOOL),
            ("TerminateJobObject", [W.HANDLE, W.UINT], W.BOOL),
            ("CreateToolhelp32Snapshot", [W.DWORD, W.DWORD], W.HANDLE),
            ("Thread32First", [W.HANDLE, C.POINTER(ThreadEntry)], W.BOOL),
            ("Thread32Next", [W.HANDLE, C.POINTER(ThreadEntry)], W.BOOL),
            ("OpenThread", [W.DWORD, W.BOOL, W.DWORD], W.HANDLE),
            ("ResumeThread", [W.HANDLE], W.DWORD),
            ("CloseHandle", [W.HANDLE], W.BOOL)):
            method = getattr(self.kernel, name)
            method.argtypes, method.restype = args, result
        snapshot = thread = None
        try:
            self.handle = self.kernel.CreateJobObjectW(None, None)
            info = Extended()
            info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if (not self.handle or not self.kernel.SetInformationJobObject(self.handle, 9, C.byref(info), C.sizeof(info)) or
                    not self.kernel.AssignProcessToJobObject(self.handle, W.HANDLE(int(process._handle)))):
                raise ValidationError("不能为挂起子进程建立关闭即清理的 Job")
            # Popen 已关闭初始线程句柄；公开 Toolhelp API 重新打开它。
            # CREATE_SUSPENDED 的主线程尚未执行，不可能自行派生逃离 Job 的后代。
            snapshot = self.kernel.CreateToolhelp32Snapshot(4, 0)
            if snapshot == C.c_void_p(-1).value or not snapshot:
                snapshot = None
                raise ValidationError("不能枚举挂起子进程的初始线程")
            entry = ThreadEntry()
            entry.size = C.sizeof(entry)
            found = []
            available = self.kernel.Thread32First(snapshot, C.byref(entry))
            count = 0
            while available:
                count += 1
                if count > 100000:
                    raise ValidationError("系统线程快照超出上限")
                if entry.owner == process.pid:
                    found.append(entry.thread)
                entry.size = C.sizeof(entry)
                available = self.kernel.Thread32Next(snapshot, C.byref(entry))
            if C.get_last_error() != 18 or len(found) != 1:  # ERROR_NO_MORE_FILES
                raise ValidationError("挂起子进程的初始线程集合异常")
            thread = self.kernel.OpenThread(2, False, found[0])  # THREAD_SUSPEND_RESUME
            if not thread or self.kernel.ResumeThread(thread) != 1:
                raise ValidationError("不能恢复已受 Job 约束的初始线程")
        except BaseException:
            if self.handle:
                self.kernel.TerminateJobObject(self.handle, 1)
                self.kernel.CloseHandle(self.handle)
                self.handle = None
            raise
        finally:
            close_failed = False
            for handle in (thread, snapshot):
                if handle and not self.kernel.CloseHandle(handle):
                    close_failed = True
            if close_failed:
                if self.handle:
                    self.kernel.TerminateJobObject(self.handle, 1)
                    self.kernel.CloseHandle(self.handle)
                    self.handle = None
                raise ValidationError("初始线程或快照句柄关闭失败")

    def kill(self):
        with self.lock:
            if self.handle and not self.kernel.TerminateJobObject(self.handle, 1):
                raise ValidationError("独占 Job 终止失败")

    def close(self):
        with self.lock:
            if self.handle:
                if not self.kernel.CloseHandle(self.handle):
                    raise ValidationError("独占 Job 句柄关闭失败")
                self.handle = None


class BoundedProcess:
    def __init__(self, arguments, *, cwd, environment, timeout, output_limit=16384, line_protocol=True):
        if timeout <= 0 or output_limit <= 0:
            raise ValidationError("独占进程期限及标准流上限必须为正")
        self.timeout = timeout
        self.deadline = time.monotonic() + timeout
        self._limit = output_limit
        self._error = None
        self.stdout = bytearray()
        self.stderr = bytearray()
        self.lines = queue.Queue(maxsize=4)
        self._closed = False
        self._job = self._timer = self.process = None
        self._threads = []
        options = {"umask": 0o077, "start_new_session": True} if os.name != "nt" else {"creationflags": 4}
        try:
            # 无 BufferedReader 锁：失败清理即使与 read 并发，close 也不能无限等待内部锁。
            self.process = subprocess.Popen(list(map(str, arguments)), cwd=cwd, env=environment,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0, **options)
            if os.name == "nt":
                self._job = _Job(self.process)
            for stream, destination, lines in ((self.process.stdout, self.stdout, line_protocol),
                                               (self.process.stderr, self.stderr, False)):
                worker = threading.Thread(target=self._read, args=(stream, destination, lines), daemon=True)
                self._threads.append(worker)
                worker.start()
            self._timer = threading.Timer(max(0.001, self.deadline - time.monotonic()), self._expire)
            self._timer.daemon = True
            self._timer.start()
        except BaseException:
            self._cleanup(time.monotonic() + 3)
            raise

    def _fail(self, message):
        if self._error is None:
            self._error = message

    def _kill(self):
        if self.process is None:
            return
        try:
            if os.name == "nt":
                if self._job is not None:
                    self._job.kill()
                elif self.process.poll() is None:
                    self.process.kill()
            else:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        except (OSError, ValidationError):
            self._fail("独占进程树终止失败")
            # Job 初始化失败或终止失败时仍单独回收尚存的主进程。
            try:
                if self.process.poll() is None:
                    self.process.kill()
            except OSError:
                pass

    def _expire(self):
        # 主进程退出也不能放过仍持有管道的后代。
        self._fail("独占进程超出总期限")
        self._kill()

    def _read(self, stream, destination, lines):
        pending = bytearray()
        try:
            while data := os.read(stream.fileno(), 1024):
                if len(destination) + len(data) > self._limit:
                    self._fail("独占进程标准流超出字节上限")
                    self._kill()
                    return
                destination.extend(data)
                if lines:
                    pending.extend(data)
                    while b"\n" in pending:
                        line, _, remaining = pending.partition(b"\n")
                        if len(line) > 4096:
                            self._fail("独占进程元数据行超长")
                            self._kill()
                            return
                        pending = bytearray(remaining)
                        try:
                            self.lines.put_nowait(bytes(line))
                        except queue.Full:
                            self._fail("独占进程元数据行过多")
                            self._kill()
                            return
                    if len(pending) > 4096:
                        self._fail("独占进程元数据行超长")
                        self._kill()
                        return
            if lines and pending:
                self._fail("独占进程元数据末行缺少换行")
        except (OSError, ValueError):
            if not self._closed:
                self._fail("独占进程标准流读取失败")
        finally:
            if lines:
                try:
                    self.lines.put_nowait(None)
                except queue.Full:
                    pass

    def line(self, timeout=10):
        try:
            value = self.lines.get(timeout=min(timeout, max(0.001, self.deadline - time.monotonic())))
        except queue.Empty:
            raise ValidationError("独占进程元数据等待超时") from None
        if value is None or self._error:
            raise ValidationError(self._error or "独占进程提前退出")
        return value

    def _cleanup(self, deadline):
        """所有资源共享同一清理期限；单项失败不跳过其余回收。"""
        self._kill()
        if self._job is not None:
            try:
                self._job.close()
            except (OSError, ValidationError):
                self._fail("独占 Job 关闭失败")
        if self._timer is not None:
            self._timer.cancel()
            if self._timer.ident is not None and self._timer is not threading.current_thread():
                self._timer.join(timeout=max(0, deadline - time.monotonic()))
        if self.process is not None:
            try:
                self.process.wait(timeout=max(0.001, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                self._fail("独占进程终止后未回收")
        for worker in self._threads:
            if worker.ident is not None:
                worker.join(timeout=max(0, deadline - time.monotonic()))
        self._closed = True
        if self.process is not None:
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                if stream is not None:
                    try:
                        stream.close()
                    except (OSError, ValueError):
                        self._fail("独占进程标准流关闭失败")
        if any(worker.is_alive() for worker in self._threads):
            self._fail("独占进程输出读取线程没有结束")

    def finish(self, command=None, *, check=True, deadline=None):
        if self._closed:
            return
        try:
            if command is not None:
                if not isinstance(command, bytes) or len(command) > 64:
                    raise ValidationError("独占进程关闭指令非法")
                if self.process.poll() is None:
                    self.process.stdin.write(command)
            limit = self.deadline if deadline is None else min(self.deadline, deadline)
            remaining = min(5 if command is not None else self.timeout,
                            max(0.001, limit - time.monotonic()))
            try:
                self.process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                self._fail("独占进程关闭超时")
        finally:
            # 最多三秒统一回收宽限，不能因每个资源分别等待而倍增。
            self._cleanup(min(self.deadline + 3, time.monotonic() + 3,
                              float("inf") if deadline is None else deadline))
        if check and (self._error or self.process.returncode):
            raise ValidationError(self._error or f"独占进程失败，退出码 {self.process.returncode}")

    def abort(self):
        if not self._closed:
            self._cleanup(min(self.deadline + 3, time.monotonic() + 3))


def run_bounded(arguments, *, cwd, environment, timeout=120, output_limit=65536):
    child = BoundedProcess(arguments, cwd=cwd, environment=environment, timeout=timeout,
                           output_limit=output_limit, line_protocol=False)
    try:
        child.finish()
        return bytes(child.stdout)
    finally:
        child.abort()
