/* 真实 WinAPI 对端；仅编译随机命名空间版本，绝不探测个人 Pageant。 */
#include <winsock2.h>
#include <afunix.h>
#include <windows.h>
#include "app/agent_codec.h"
#include "core/pa_windows.h"
#include "encrypted_fixture.h"
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QDir>
#include <QFile>
#include <QProcess>
#include <QThread>
#include <QTimer>
#include <cstdio>
#include <csignal>
#include <cstdlib>
#include <cstdint>
#include <atomic>
#include <array>
#include <future>
#include <functional>
#include <stdexcept>
#include <string>

#ifndef PA_WINDOWS_TEST_NAMESPACE
#error 原生 IPC 测试必须编译到专用命名空间
#endif
#ifdef NDEBUG
#error 原生 IPC 测试必须保留断言
#endif
static volatile LONG diagnosticStage = 0, diagnosticFault = 0;
static void diagnosticWrite(const char *data, size_t length)
{
    DWORD written = 0;
    WriteFile(GetStdHandle(STD_ERROR_HANDLE), data, DWORD(length), &written, nullptr);
}
static void diagnosticStack(const char *kind, DWORD code)
{
    if (InterlockedCompareExchange(&diagnosticFault, 1, 0)) return;
    char line[256];
    int size = std::snprintf(line, sizeof(line),
        "Pageant 测试故障：类型=%s，代码=0x%08lx，阶段=%ld\n", kind,
        static_cast<unsigned long>(code), InterlockedCompareExchange(&diagnosticStage, 0, 0));
    if (size > 0 && size < int(sizeof(line))) diagnosticWrite(line, size_t(size));
    void *frames[24]{};
    const auto count = CaptureStackBackTrace(0, 24, frames, nullptr);
    const auto executable = GetModuleHandleW(nullptr);
    for (USHORT index = 0; index < count; ++index) {
        HMODULE module = nullptr;
        if (!GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                               GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                               reinterpret_cast<LPCWSTR>(frames[index]), &module) || module != executable)
            continue;
        const auto offset = reinterpret_cast<uintptr_t>(frames[index]) - reinterpret_cast<uintptr_t>(module);
        size = std::snprintf(line, sizeof(line), "Pageant 测试栈：帧=%u，偏移=0x%llx\n",
                            unsigned(index), static_cast<unsigned long long>(offset));
        if (size > 0 && size < int(sizeof(line))) diagnosticWrite(line, size_t(size));
    }
}
static void diagnosticAbort(int)
{
    /* 只改变专用测试宿主的故障收尾；不执行析构，不把断言转换为可恢复错误。 */
    diagnosticStack("abort", SIGABRT);
    TerminateProcess(GetCurrentProcess(), 134);
    std::_Exit(134);
}
static LONG WINAPI diagnosticException(EXCEPTION_POINTERS *exception)
{
    diagnosticStack("exception", exception->ExceptionRecord->ExceptionCode);
    return EXCEPTION_CONTINUE_SEARCH;
}
static void testStage(const char *stage, LONG number)
{
    InterlockedExchange(&diagnosticStage, number);
    std::fprintf(stderr, "Pageant 测试阶段：%s\n", stage); std::fflush(stderr);
}
extern "C" void pa_test_unix_stage(unsigned stage)
{
    InterlockedExchange(&diagnosticStage, LONG(stage));
    std::fprintf(stderr, "Pageant AF_UNIX 子阶段：编号=%u\n", stage); std::fflush(stderr);
}
static void checkFailure(unsigned line)
{
    std::fprintf(stderr, "Pageant 测试检查失败：行=%u\n", line); std::fflush(stderr);
    throw std::runtime_error("检查未通过");
}
#define CHECK(value) do { if (!(value)) checkFailure(__LINE__); } while (0)
extern "C" int pa_test_verify(const void *, size_t, const void *, size_t, const void *, size_t);
extern "C" void *pa_test_stalled_pipe(void);
extern "C" int pa_test_remote_query(void);
using namespace putty_runtime;
static bool until(const std::function<bool()> &predicate, int timeout = 8000)
{
    QElapsedTimer timer; timer.start();
    while (!predicate() && timer.elapsed() < timeout) {
        QCoreApplication::processEvents(QEventLoop::AllEvents, 5); QThread::msleep(1);
    }
    return predicate();
}
static quint32 u32(const QByteArray &bytes, qsizetype offset = 0)
{
    CHECK(offset >= 0 && offset + 4 <= bytes.size());
    const auto *data = reinterpret_cast<const unsigned char *>(bytes.constData() + offset);
    return (quint32(data[0]) << 24) | (quint32(data[1]) << 16) | (quint32(data[2]) << 8) | data[3];
}
static QByteArray number(quint32 value)
{
    char data[4] = {char(value >> 24), char(value >> 16), char(value >> 8), char(value)};
    return QByteArray(data, 4);
}
static QByteArray string(const QByteArray &value) { return number(quint32(value.size())) + value; }
static QByteArray framed(const QByteArray &body) { return string(body); }
static QByteArray publicKey()
{
    return string("ssh-ed25519") + string(QByteArray::fromHex(
        "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"));
}
static QByteArray addKey()
{
    const auto publicBytes = QByteArray::fromHex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a");
    const auto seed = QByteArray::fromHex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60");
    return QByteArray(1, char(17)) + publicKey() + string(seed + publicBytes) + string("固定测试身份");
}
static QByteArray signRequest(const QByteArray &message)
{ return QByteArray(1, char(13)) + string(publicKey()) + string(message) + number(0); }
class WinsockSession {
public:
    WinsockSession() { WSADATA data{}; CHECK(WSAStartup(MAKEWORD(2, 2), &data) == 0); }
    ~WinsockSession() { WSACleanup(); }
};
class UnixSocket {
public:
    explicit UnixSocket(const QByteArray &path)
    {
        CHECK(path.size() < qsizetype(sizeof(((SOCKADDR_UN *)nullptr)->sun_path)));
        value = socket(AF_UNIX, SOCK_STREAM, 0);
        CHECK(value != INVALID_SOCKET);
        DWORD timeout = 8000;
        if (setsockopt(value, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char *>(&timeout), sizeof(timeout)) ||
            setsockopt(value, SOL_SOCKET, SO_SNDTIMEO, reinterpret_cast<const char *>(&timeout), sizeof(timeout))) {
            closesocket(value); value = INVALID_SOCKET; throw std::runtime_error("AF_UNIX 超时设置失败");
        }
        SOCKADDR_UN address{};
        address.sun_family = AF_UNIX;
        memcpy(address.sun_path, path.constData(), size_t(path.size() + 1));
        if (connect(value, reinterpret_cast<const sockaddr *>(&address), sizeof(address))) {
            closesocket(value); value = INVALID_SOCKET; throw std::runtime_error("AF_UNIX 连接失败");
        }
    }
    UnixSocket(const UnixSocket &) = delete;
    UnixSocket &operator=(const UnixSocket &) = delete;
    ~UnixSocket() { if (value != INVALID_SOCKET) closesocket(value); }
    SOCKET value = INVALID_SOCKET;
};
static QByteArray unixPath;
static QByteArray unixQuery(const QByteArray &request, bool closing = false)
{
    UnixSocket socket(unixPath);
    const auto bytes = framed(request);
    qsizetype done = 0;
    while (done < bytes.size()) {
        const int count = send(socket.value, bytes.constData() + done, int(bytes.size() - done), 0);
        CHECK(count > 0); done += count;
    }
    QByteArray response;
    while (response.size() < 4 || response.size() < qsizetype(u32(response) + 4)) {
        char block[4096];
        const int count = recv(socket.value, block, sizeof(block), 0);
        if (!count && closing) return {};
        if (count == SOCKET_ERROR && closing &&
            (WSAGetLastError() == WSAECONNRESET || WSAGetLastError() == WSAECONNABORTED)) return {};
        CHECK(count > 0);
        response.append(block, count);
        CHECK(response.size() < 4 || u32(response) <= 262140);
    }
    CHECK(response.size() == qsizetype(u32(response) + 4));
    return response.mid(4);
}
static QByteArray queryUnix(const QByteArray &request)
{
    auto future = std::async(std::launch::async, [=] { return unixQuery(request); });
    CHECK(until([&] { return future.wait_for(std::chrono::milliseconds(0)) == std::future_status::ready; }));
    return future.get();
}
static QByteArray pipeQuery(const QByteArray &query)
{
    char *name = pa_windows_pipe_name();
    CHECK(WaitNamedPipeA(name, 8000));
    HANDLE pipe = CreateFileA(name, GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
    pa_windows_free(name);
    CHECK(pipe != INVALID_HANDLE_VALUE);
    const auto bytes = framed(query);
    DWORD done = 0;
    while (done < DWORD(bytes.size())) {
        DWORD count = 0;
        if (!WriteFile(pipe, bytes.constData() + done, DWORD(bytes.size()) - done, &count, NULL) || !count) {
            CloseHandle(pipe); throw std::runtime_error("WriteFile");
        }
        done += count;
    }
    QByteArray response;
    while (response.size() < 4 || response.size() < qsizetype(u32(response) + 4)) {
        char block[4096]; DWORD count = 0;
        if (!ReadFile(pipe, block, sizeof(block), &count, NULL) || !count) {
            CloseHandle(pipe); throw std::runtime_error("ReadFile");
        }
        response.append(block, count);
        if (response.size() >= 4 && u32(response) > 262140) {
            CloseHandle(pipe); throw std::runtime_error("管道响应超限");
        }
    }
    CloseHandle(pipe);
    CHECK(response.size() == qsizetype(u32(response) + 4));
    return response.mid(4);
}
static QByteArray windowQuery(const QByteArray &query, bool invalidLength = false)
{
    HWND window = FindWindowA(pa_windows_class_name(), pa_windows_class_name());
    CHECK(window);
    const auto name = QByteArray(pa_windows_class_name()) + "-mapping-" + QByteArray::number(qulonglong(GetCurrentThreadId()));
    HANDLE mapping = CreateFileMappingA(INVALID_HANDLE_VALUE, NULL, PAGE_READWRITE, 0, 262144, name.constData());
    CHECK(mapping && GetLastError() != ERROR_ALREADY_EXISTS);
    auto *view = static_cast<char *>(MapViewOfFile(mapping, FILE_MAP_WRITE, 0, 0, 0));
    CHECK(view);
    const auto bytes = invalidLength ? number(UINT32_MAX) : framed(query);
    memcpy(view, bytes.constData(), size_t(bytes.size()));
    COPYDATASTRUCT copy{};
    copy.dwData = 0x804e50ba; copy.cbData = DWORD(name.size() + 1);
    copy.lpData = const_cast<char *>(name.constData());
    const auto result = SendMessageA(window, WM_COPYDATA, 0, reinterpret_cast<LPARAM>(&copy));
    const QByteArray prefix(view, 4);
    const auto size = u32(prefix);
    QByteArray response;
    if (result && size && size <= 262140) response = QByteArray(view + 4, qsizetype(size));
    UnmapViewOfFile(view); CloseHandle(mapping);
    CHECK(result && !response.isEmpty());
    return response;
}
static QByteArray query(bool legacy, const QByteArray &bytes)
{
    auto future = std::async(std::launch::async, [=] {
        return legacy ? windowQuery(bytes) : pipeQuery(bytes);
    });
    CHECK(until([&] { return future.wait_for(std::chrono::milliseconds(0)) == std::future_status::ready; }));
    return future.get();
}
static void verify(bool legacy, const QByteArray &message)
{
    const auto response = query(legacy, signRequest(message));
    CHECK(response.size() >= 5 && response[0] == char(14));
    CHECK(u32(response, 1) == quint32(response.size() - 5));
    const auto blob = publicKey(), signature = response.mid(5);
    CHECK(pa_test_verify(blob.constData(), size_t(blob.size()), signature.constData(), size_t(signature.size()),
                         message.constData(), size_t(message.size())));
}
static void verifyUnix(const QByteArray &message)
{
    const auto response = queryUnix(signRequest(message));
    CHECK(response.size() >= 5 && response[0] == char(14));
    CHECK(u32(response, 1) == quint32(response.size() - 5));
    const auto blob = publicKey(), signature = response.mid(5);
    CHECK(pa_test_verify(blob.constData(), size_t(blob.size()), signature.constData(), size_t(signature.size()),
                         message.constData(), size_t(message.size())));
}
static void socketOptions(pa_options &options, const QByteArray &path)
{
    CHECK(path.size() < qsizetype(sizeof(options.socket_path)));
    memcpy(options.socket_path, path.constData(), size_t(path.size() + 1));
}
static void absentEndpoints()
{
    CHECK(!FindWindowA(pa_windows_class_name(), pa_windows_class_name()));
    char *name = pa_windows_pipe_name();
    WIN32_FIND_DATAA info{};
    HANDLE found = FindFirstFileA(name, &info);
    pa_windows_free(name);
    if (found != INVALID_HANDLE_VALUE) FindClose(found);
    CHECK(found == INVALID_HANDLE_VALUE);
}
static void rejectedUnixListener(const QByteArray &path)
{
    pa_options options{}; socketOptions(options, path);
    Runtime runtime(pa_agent_module, QByteArray(reinterpret_cast<const char *>(&options), sizeof(options)));
    bool ready = false, failed = false;
    QObject::connect(&runtime, &Runtime::eventReceived, &runtime, [&](const Event &event) {
        if (event.type == PR_EVENT_READY) ready = true;
        if (event.type == PR_EVENT_ERROR) failed = true;
    });
    CHECK(until([&] { return ready || failed; }) && failed && !ready);
    CHECK(until([&] { return runtime.isClosed(); }));
    absentEndpoints();
}
static Command reply(Runtime &runtime, quint64 id, bool cancel)
{
    Command command;
    command.generation = runtime.generation(); command.object = 1; command.request = id;
    command.type = PR_COMMAND_REPLY;
    command.data = pageant::input(PA_REPLY, cancel ? PA_CANCEL : 0, {}, cancel ? QByteArray() : QByteArray("suite-passphrase"));
    return command;
}
static void marker(const char *value)
{ std::puts(value); std::fflush(stdout); }
static HANDLE unixReplaceGo, unixReplaceBound;
static std::atomic_bool unixReplaceRequested{false}, unixReplaceSucceeded{false};
extern "C" void pa_test_unix_after_bind(const char *path)
{
    if (!unixReplaceRequested.exchange(false)) return;
    const auto wide = QString::fromUtf8(path);
    /* 两个端点都属于本次私有夹具；只在测试变体中模拟外部进程抢换路径。 */
    unixReplaceSucceeded = DeleteFileW(reinterpret_cast<LPCWSTR>(wide.utf16())) &&
        SetEvent(unixReplaceGo) && WaitForSingleObject(unixReplaceBound, 2500) == WAIT_OBJECT_0;
}
static int replacementServer(const QString &path)
{
    const auto prefix = QByteArray(pa_windows_class_name());
    HANDLE go = OpenEventA(SYNCHRONIZE, FALSE, (prefix + "-unix-replace-go").constData());
    HANDLE bound = OpenEventA(EVENT_MODIFY_STATE, FALSE, (prefix + "-unix-replace-bound").constData());
    HANDLE stop = OpenEventA(SYNCHRONIZE, FALSE, (prefix + "-unix-replace-stop").constData());
    struct Resources {
        HANDLE go, bound, stop;
        SOCKET listener = INVALID_SOCKET;
        std::array<SOCKET, 8> clients;
        Resources(HANDLE go, HANDLE bound, HANDLE stop) : go(go), bound(bound), stop(stop)
        { clients.fill(INVALID_SOCKET); }
        ~Resources() {
            for (auto socket : clients) if (socket != INVALID_SOCKET) closesocket(socket);
            if (listener != INVALID_SOCKET) closesocket(listener);
            if (go) CloseHandle(go);
            if (bound) CloseHandle(bound);
            if (stop) CloseHandle(stop);
        }
    } resources(go, bound, stop);
    CHECK(go && bound && stop);
    marker("armed");
    CHECK(WaitForSingleObject(go, 8000) == WAIT_OBJECT_0);
    const auto bytes = QDir::toNativeSeparators(path).toUtf8();
    CHECK(bytes.size() < qsizetype(sizeof(((SOCKADDR_UN *)nullptr)->sun_path)));
    resources.listener = socket(AF_UNIX, SOCK_STREAM, 0);
    CHECK(resources.listener != INVALID_SOCKET);
    CHECK(SetHandleInformation(reinterpret_cast<HANDLE>(resources.listener), HANDLE_FLAG_INHERIT, 0));
    u_long nonblocking = 1;
    CHECK(ioctlsocket(resources.listener, FIONBIO, &nonblocking) == 0);
    SOCKADDR_UN address{}; address.sun_family = AF_UNIX;
    memcpy(address.sun_path, bytes.constData(), size_t(bytes.size() + 1));
    CHECK(bind(resources.listener, reinterpret_cast<const sockaddr *>(&address), sizeof(address)) == 0);
    CHECK(listen(resources.listener, 8) == 0 && SetEvent(bound));
    QElapsedTimer total; total.start();
    size_t count = 0;
    while (WaitForSingleObject(stop, 0) == WAIT_TIMEOUT && total.elapsed() < 8000) {
        fd_set readable; FD_ZERO(&readable); FD_SET(resources.listener, &readable);
        timeval timeout{0, 50000};
        const int result = select(0, &readable, nullptr, nullptr, &timeout);
        CHECK(result != SOCKET_ERROR);
        if (!result) continue;
        SOCKET client = accept(resources.listener, nullptr, nullptr);
        CHECK(client != INVALID_SOCKET);
        if (count == resources.clients.size()) {
            closesocket(client);
            throw std::runtime_error("替换端点测试连接超限");
        }
        resources.clients[count++] = client;
        CHECK(SetHandleInformation(reinterpret_cast<HANDLE>(client), HANDLE_FLAG_INHERIT, 0));
    }
    CHECK(WaitForSingleObject(stop, 0) == WAIT_OBJECT_0);
    return 0;
}
static HANDLE stalledStop;
static LRESULT CALLBACK stalledWindow(HWND window, UINT message, WPARAM wp, LPARAM lp)
{
    if (message == WM_COPYDATA) {
        marker("query");
        WaitForSingleObject(stalledStop, 30000);
        return 0;
    }
    return DefWindowProcA(window, message, wp, lp);
}
static int stalledServer(bool legacy, bool fragmented = false)
{
    const auto stopName = QByteArray(pa_windows_class_name()) + "-stop";
    stalledStop = OpenEventA(SYNCHRONIZE, FALSE, stopName.constData());
    CHECK(stalledStop);
    if (!legacy) {
        HANDLE pipe = pa_test_stalled_pipe();
        CHECK(pipe != INVALID_HANDLE_VALUE);
        marker("ready");
        CHECK(ConnectNamedPipe(pipe, NULL) || GetLastError() == ERROR_PIPE_CONNECTED);
        char prefix[4]; DWORD count = 0, done = 0;
        while (done < sizeof(prefix)) {
            CHECK(ReadFile(pipe, prefix + done, sizeof(prefix) - done, &count, NULL) && count);
            done += count;
        }
        if (fragmented) {
            CHECK(u32(QByteArray(prefix, sizeof(prefix))) == 128 * 1024 + 1);
            QByteArray body;
            while (body.size() < 128 * 1024 + 1) {
                char part[4093];
                CHECK(ReadFile(pipe, part, qMin(DWORD(sizeof(part)), DWORD(128 * 1024 + 1 - body.size())),
                               &count, NULL) && count);
                body.append(part, count);
            }
            CHECK(body[0] == char(11) && body.mid(1) == QByteArray(128 * 1024, char(0x5a)));
            const auto response = number(5) + QByteArray(1, char(12)) + number(0);
            for (const char byte : response) {
                CHECK(WriteFile(pipe, &byte, 1, &count, NULL) && count == 1);
                QThread::msleep(2);
            }
        }
        marker("query");
        CHECK(WaitForSingleObject(stalledStop, 30000) == WAIT_OBJECT_0);
        CloseHandle(pipe);
    } else {
        WNDCLASSA cls{};
        cls.lpfnWndProc = stalledWindow; cls.hInstance = GetModuleHandleA(NULL);
        cls.lpszClassName = pa_windows_class_name();
        CHECK(RegisterClassA(&cls));
        HWND window = CreateWindowExA(0, cls.lpszClassName, cls.lpszClassName,
            WS_OVERLAPPED, 0, 0, 0, 0, NULL, NULL, cls.hInstance, NULL);
        CHECK(window); marker("ready");
        while (MsgWaitForMultipleObjects(1, &stalledStop, FALSE, 30000, QS_ALLINPUT) == WAIT_OBJECT_0 + 1) {
            MSG message;
            while (PeekMessageA(&message, NULL, 0, 0, PM_REMOVE)) {
                TranslateMessage(&message); DispatchMessageA(&message);
                if (WaitForSingleObject(stalledStop, 0) != WAIT_TIMEOUT) break;
            }
        }
        CHECK(WaitForSingleObject(stalledStop, 0) == WAIT_OBJECT_0);
        DestroyWindow(window); UnregisterClassA(cls.lpszClassName, cls.hInstance);
    }
    CloseHandle(stalledStop); return 0;
}
static int boundedClient()
{
    const auto closeName = QByteArray(pa_windows_class_name()) + "-client-close";
    HANDLE closeRequested = OpenEventA(SYNCHRONIZE, FALSE, closeName.constData());
    CHECK(closeRequested);
    pa_options options{}; options.client_only = true;
    Runtime runtime(pa_agent_module, QByteArray(reinterpret_cast<const char *>(&options), sizeof(options)));
    QElapsedTimer total, heartbeat; total.start(); heartbeat.start();
    qint64 maximumGap = 0, closeDuration = -1;
    int ticks = 0;
    QTimer pulse;
    QObject::connect(&pulse, &QTimer::timeout, [&] {
        maximumGap = qMax(maximumGap, heartbeat.restart()); ++ticks;
        if (closeDuration < 0 && WaitForSingleObject(closeRequested, 0) == WAIT_OBJECT_0) {
            QElapsedTimer closing; closing.start(); runtime.close(); closeDuration = closing.elapsed();
        }
    });
    pulse.start(10);
    CHECK(until([&] { return runtime.isClosed(); }, 10000));
    CHECK(pa_windows_query_failed());
    CHECK(closeDuration >= 0 && closeDuration < 500 && maximumGap < 500 && ticks >= 10);
    CHECK(total.elapsed() < 10000);
    CloseHandle(closeRequested);
    marker("bounded-client-closed"); return 0;
}
static bool readMarker(QProcess &process, const QByteArray &expected, int timeout)
{
    QElapsedTimer timer; timer.start();
    while (!process.canReadLine() && timer.elapsed() < timeout && process.state() != QProcess::NotRunning)
        process.waitForReadyRead(50);
    return process.readLine().trimmed() == expected;
}
static void checkStalledClient(bool legacy, const QString &scope, bool fragmented = false)
{
    const auto stopName = QByteArray(pa_windows_class_name()) + "-stop";
    HANDLE stop = CreateEventA(NULL, TRUE, FALSE, stopName.constData());
    CHECK(stop && GetLastError() != ERROR_ALREADY_EXISTS);
    const auto closeName = QByteArray(pa_windows_class_name()) + "-client-close";
    HANDLE closeRequested = CreateEventA(NULL, TRUE, FALSE, closeName.constData());
    CHECK(closeRequested && GetLastError() != ERROR_ALREADY_EXISTS);
    QProcess server, client;
    try {
        server.start(QCoreApplication::applicationFilePath(),
            {fragmented ? QStringLiteral("--fragmented-pipe") :
                legacy ? QStringLiteral("--stall-window") : QStringLiteral("--stall-pipe"),
             QStringLiteral("--scope"), scope});
        CHECK(readMarker(server, "ready", 8000));
        client.start(QCoreApplication::applicationFilePath(),
            {fragmented ? QStringLiteral("--query-client") : QStringLiteral("--bounded-client"),
             QStringLiteral("--scope"), scope});
        CHECK(readMarker(server, "query", 8000));
        SetEvent(closeRequested);
        CHECK(client.waitForFinished(12000) && client.exitStatus() == QProcess::NormalExit && client.exitCode() == 0);
        CHECK(client.readAllStandardOutput().trimmed() ==
              (fragmented ? "fragmented-query-ok" : "bounded-client-closed"));
        SetEvent(stop);
        CHECK(server.waitForFinished(3000) && server.exitStatus() == QProcess::NormalExit && server.exitCode() == 0);
        CloseHandle(stop); CloseHandle(closeRequested);
    } catch (...) {
        SetEvent(stop);
        SetEvent(closeRequested);
        // 只回收本测试创建的对端进程，不终止核心线程或任何个人代理。
        if (!server.waitForFinished(3000)) { server.kill(); server.waitForFinished(3000); }
        if (!client.waitForFinished(12000)) { client.kill(); client.waitForFinished(3000); }
        CloseHandle(stop); CloseHandle(closeRequested); throw;
    }
}
static void unixPendingClose()
{
    pa_options options{}; socketOptions(options, unixPath);
    Runtime runtime(pa_agent_module, QByteArray(reinterpret_cast<const char *>(&options), sizeof(options)));
    bool ready = false, failed = false;
    quint64 prompt = 0;
    QObject::connect(&runtime, &Runtime::eventReceived, &runtime, [&](const Event &event) {
        if (event.type == PR_EVENT_READY) ready = true;
        if (event.type == PR_EVENT_ERROR) failed = true;
        if (event.type == PR_EVENT_REQUEST) prompt = event.request;
    });
    CHECK(until([&] { return ready || failed; }) && ready && !failed);
    const auto encrypted = QByteArray(1, char(27)) + string("add-ppk@putty.projects.tartarus.org") + string(encryptedFixture);
    CHECK(queryUnix(encrypted) == QByteArray(1, char(6)));
    auto pending = std::async(std::launch::async, [] { return unixQuery(signRequest("unix-pending-close"), true); });
    CHECK(until([&] { return prompt != 0; }));
    CHECK(pending.wait_for(std::chrono::milliseconds(0)) != std::future_status::ready);
    QElapsedTimer closing; closing.start(); runtime.close();
    CHECK(closing.elapsed() < 500);
    CHECK(until([&] { return runtime.isClosed(); }));
    CHECK(until([&] { return pending.wait_for(std::chrono::milliseconds(0)) == std::future_status::ready; }));
    const auto response = pending.get();
    CHECK(response.isEmpty() || response == QByteArray(1, char(5)));
    CHECK(runtime.submit(reply(runtime, prompt, false)) == PR_CLOSING);
    absentEndpoints();
    CHECK(GetFileAttributesA(unixPath.constData()) == INVALID_FILE_ATTRIBUTES && GetLastError() == ERROR_FILE_NOT_FOUND);
}
static void checkReplacedUnixEndpoint(const QString &scope, const QString &path)
{
    const auto prefix = QByteArray(pa_windows_class_name());
    unixReplaceGo = CreateEventA(nullptr, TRUE, FALSE, (prefix + "-unix-replace-go").constData());
    const bool newGo = unixReplaceGo && GetLastError() != ERROR_ALREADY_EXISTS;
    unixReplaceBound = CreateEventA(nullptr, TRUE, FALSE, (prefix + "-unix-replace-bound").constData());
    const bool newBound = unixReplaceBound && GetLastError() != ERROR_ALREADY_EXISTS;
    HANDLE stop = CreateEventA(nullptr, TRUE, FALSE, (prefix + "-unix-replace-stop").constData());
    const bool newStop = stop && GetLastError() != ERROR_ALREADY_EXISTS;
    struct Events {
        HANDLE stop;
        ~Events() {
            unixReplaceRequested = false;
            if (stop) CloseHandle(stop);
            if (unixReplaceBound) CloseHandle(unixReplaceBound);
            if (unixReplaceGo) CloseHandle(unixReplaceGo);
            unixReplaceBound = unixReplaceGo = nullptr;
        }
    } events{stop};
    CHECK(newGo && newBound && newStop);
    HANDLE endpoint = INVALID_HANDLE_VALUE;
    QProcess server;
    try {
        server.start(QCoreApplication::applicationFilePath(), {QStringLiteral("--replacement-unix"),
            QStringLiteral("--scope"), scope, QStringLiteral("--unix-socket"), path});
        CHECK(readMarker(server, "armed", 8000));
        unixReplaceSucceeded = false;
        unixReplaceRequested = true;
        rejectedUnixListener(unixPath);
        CHECK(unixReplaceSucceeded && !unixReplaceRequested);
        endpoint = CreateFileW(reinterpret_cast<LPCWSTR>(path.utf16()), DELETE | FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, nullptr);
        CHECK(endpoint != INVALID_HANDLE_VALUE);
        FILE_ATTRIBUTE_TAG_INFO tag{};
        CHECK(GetFileInformationByHandleEx(endpoint, FileAttributeTagInfo, &tag, sizeof(tag)) &&
              tag.ReparseTag == IO_REPARSE_TAG_AF_UNIX);
        {
            UnixSocket socket(unixPath);
            ULONG pid = 0; DWORD returned = 0;
            CHECK(WSAIoctl(socket.value, SIO_AF_UNIX_GETPEERPID, nullptr, 0, &pid, sizeof(pid),
                           &returned, nullptr, nullptr) == 0);
            CHECK(pid == ULONG(server.processId()) && pid != GetCurrentProcessId());
        }
        CHECK(SetEvent(stop));
        CHECK(server.waitForFinished(3000) && server.exitStatus() == QProcess::NormalExit && server.exitCode() == 0);
        FILE_DISPOSITION_INFO disposition{}; disposition.DeleteFile = TRUE;
        CHECK(SetFileInformationByHandle(endpoint, FileDispositionInfo, &disposition, sizeof(disposition)));
        CHECK(CloseHandle(endpoint)); endpoint = INVALID_HANDLE_VALUE;
        CHECK(GetFileAttributesW(reinterpret_cast<LPCWSTR>(path.utf16())) == INVALID_FILE_ATTRIBUTES &&
              GetLastError() == ERROR_FILE_NOT_FOUND);
    } catch (...) {
        unixReplaceRequested = false;
        SetEvent(stop);
        if (!server.waitForFinished(3000)) { server.kill(); server.waitForFinished(3000); }
        if (endpoint != INVALID_HANDLE_VALUE) CloseHandle(endpoint);
        throw;
    }
}
int main(int argc, char **argv)
{
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX);
    _set_abort_behavior(0, _WRITE_ABORT_MSG | _CALL_REPORTFAULT);
    std::signal(SIGABRT, diagnosticAbort);
    SetUnhandledExceptionFilter(diagnosticException);
    testStage("初始化", 100);
    pa_windows_prepare();
    QCoreApplication app(argc, argv);
    try {
        WinsockSession winsock;
        CHECK(qEnvironmentVariable("PUTTY_SUITE_NATIVE_NO_STORAGE") == "1");
        const auto arguments = app.arguments();
        const auto scopeIndex = arguments.indexOf(QStringLiteral("--scope"));
        CHECK(scopeIndex >= 0 && scopeIndex + 1 < arguments.size());
        const auto scope = arguments[scopeIndex + 1].toLatin1();
        CHECK(pa_windows_set_test_scope(scope.constData()));
        CHECK(QByteArray(pa_windows_class_name()).startsWith("SuitePageantTest-"));
        if (arguments.contains(QStringLiteral("--replacement-unix"))) {
            const auto index = arguments.indexOf(QStringLiteral("--unix-socket"));
            CHECK(index >= 0 && index + 1 < arguments.size());
            return replacementServer(arguments[index + 1]);
        }
        if (arguments.contains(QStringLiteral("--stall-pipe"))) return stalledServer(false);
        if (arguments.contains(QStringLiteral("--stall-window"))) return stalledServer(true);
        if (arguments.contains(QStringLiteral("--fragmented-pipe"))) return stalledServer(false, true);
        if (arguments.contains(QStringLiteral("--query-client"))) {
            CHECK(pa_test_remote_query()); marker("fragmented-query-ok"); return 0;
        }
        if (arguments.contains(QStringLiteral("--bounded-client"))) return boundedClient();
        if (!arguments.contains(QStringLiteral("--probe-existing"))) {
            testStage("分帧客户端", 101);
            checkStalledClient(false, QString::fromLatin1(scope), true);
            testStage("管道停滞客户端", 102);
            checkStalledClient(false, QString::fromLatin1(scope));
            testStage("窗口停滞客户端", 103);
            checkStalledClient(true, QString::fromLatin1(scope));
        }
        bool existing = false; char *error = nullptr;
        void *lock = pa_windows_startup_lock(&existing, &error);
        CHECK(lock && !error);
        if (app.arguments().contains(QStringLiteral("--probe-existing"))) {
            pa_windows_startup_unlock(lock); return existing ? 0 : 1;
        }
        CHECK(!existing);
        const auto socketIndex = arguments.indexOf(QStringLiteral("--unix-socket"));
        const auto blockedIndex = arguments.indexOf(QStringLiteral("--blocked-path"));
        CHECK(socketIndex >= 0 && socketIndex + 1 < arguments.size());
        CHECK(blockedIndex >= 0 && blockedIndex + 1 < arguments.size());
        const auto path = QDir::toNativeSeparators(arguments[socketIndex + 1]);
        unixPath = path.toLocal8Bit();
        CHECK(QString::fromLocal8Bit(unixPath) == path);
        const auto blockedPath = QDir::toNativeSeparators(arguments[blockedIndex + 1]);
        const auto blocked = blockedPath.toLocal8Bit();
        CHECK(QString::fromLocal8Bit(blocked) == blockedPath);
        QFile preserved(blockedPath);
        CHECK(preserved.open(QIODevice::ReadOnly));
        const auto content = preserved.readAll(); preserved.close();
        CHECK(content == "pageant-unix-existing-file-preserved\n");
        testStage("已有文件拒绝", 104);
        rejectedUnixListener(blocked);
        CHECK(preserved.open(QIODevice::ReadOnly) && preserved.readAll() == content); preserved.close();
        const auto missing = QDir::toNativeSeparators(QDir::current().filePath(QStringLiteral("missing/agent.sock"))).toLocal8Bit();
        testStage("缺失父目录拒绝", 105);
        rejectedUnixListener(missing);
        CHECK(GetFileAttributesA(missing.constData()) == INVALID_FILE_ATTRIBUTES && GetLastError() == ERROR_PATH_NOT_FOUND);
        testStage("无效编码拒绝", 106);
        rejectedUnixListener(QByteArray(1, char(0xff)));
        testStage("跨进程替换拒绝", 107);
        checkReplacedUnixEndpoint(QString::fromLatin1(scope), path);
        testStage("三传输启动", 108);
        pa_options options{}; socketOptions(options, unixPath);
        Runtime runtime(pa_agent_module, QByteArray(reinterpret_cast<const char *>(&options), sizeof(options)));
        bool ready = false, failed = false;
        QList<quint64> prompts;
        QObject::connect(&runtime, &Runtime::eventReceived, &runtime, [&](const Event &event) {
            if (event.type == PR_EVENT_READY) ready = true;
            if (event.type == PR_EVENT_ERROR) failed = true;
            if (event.type == PR_EVENT_REQUEST) {
                pageant::Packet packet;
                CHECK(pageant::decode(event.data, packet));
                if (packet.header.kind == PA_PROMPT) prompts.append(event.request);
            }
        });
        CHECK(until([&] { return ready || failed; }) && ready && !failed);
        pa_windows_startup_unlock(lock);
        HWND window = FindWindowA(pa_windows_class_name(), pa_windows_class_name());
        DWORD process = 0;
        CHECK(window && GetWindowThreadProcessId(window, &process) != GetCurrentThreadId());
        CHECK(process == GetCurrentProcessId());
        QProcess probe;
        probe.start(app.applicationFilePath(), {QStringLiteral("--probe-existing"), QStringLiteral("--scope"),
                                               QString::fromLatin1(scope)});
        CHECK(probe.waitForFinished(8000) && probe.exitStatus() == QProcess::NormalExit && probe.exitCode() == 0);
        testStage("三传输共享密钥与签名", 109);
        const auto list = QByteArray(1, char(11));
        CHECK(query(false, list) == QByteArray(1, char(12)) + number(0));
        CHECK(query(true, list) == QByteArray(1, char(12)) + number(0));
        CHECK(queryUnix(list) == QByteArray(1, char(12)) + number(0));
        CHECK(query(false, addKey()) == QByteArray(1, char(6)));
        const auto listed = query(true, list);
        CHECK(listed.size() > 5 && listed[0] == char(12) && u32(listed, 1) == 1);
        CHECK(listed.mid(9, qsizetype(u32(listed, 5))) == publicKey());
        CHECK(queryUnix(list) == listed);
        verify(false, QByteArrayLiteral("管道签名\0二进制"));
        verify(true, QByteArrayLiteral("旧窗口签名\0二进制"));
        verifyUnix(QByteArrayLiteral("AF_UNIX 签名\0二进制"));
        CHECK(queryUnix(QByteArray(1, char(19))) == QByteArray(1, char(6)));
        CHECK(query(false, list) == QByteArray(1, char(12)) + number(0));
        CHECK(query(true, list) == QByteArray(1, char(12)) + number(0));
        CHECK(queryUnix(addKey()) == QByteArray(1, char(6)));
        CHECK(query(false, list) == listed && query(true, list) == listed);
        COPYDATASTRUCT malformed{};
        malformed.dwData = 0x804e50ba;
        CHECK(!SendMessageA(window, WM_COPYDATA, 0, reinterpret_cast<LPARAM>(&malformed)));
        char invalidName[2] = {'a', 'b'}; malformed.lpData = invalidName; malformed.cbData = 2;
        CHECK(!SendMessageA(window, WM_COPYDATA, 0, reinterpret_cast<LPARAM>(&malformed)));
        auto malformedReply = std::async(std::launch::async, [] { return windowQuery({}, true); });
        CHECK(until([&] { return malformedReply.wait_for(std::chrono::milliseconds(0)) == std::future_status::ready; }));
        CHECK(malformedReply.get() == QByteArray(1, char(5)));
        CHECK(query(true, QByteArray(1, char(19))) == QByteArray(1, char(6)));
        CHECK(query(false, list) == QByteArray(1, char(12)) + number(0));
        CHECK(queryUnix(list) == QByteArray(1, char(12)) + number(0));
        CHECK(query(true, addKey()) == QByteArray(1, char(6)));
        CHECK(queryUnix(list) == listed && query(false, list) == listed);
        CHECK(query(false, QByteArray(1, char(19))) == QByteArray(1, char(6)));
        CHECK(queryUnix(list) == QByteArray(1, char(12)) + number(0));
        CHECK(query(true, list) == QByteArray(1, char(12)) + number(0));
        // 固定 RFC8032 向量的有效加密 PPK，只经协议添加，不触及文件或原设置。
        const auto encrypted = QByteArray(1, char(27)) + string("add-ppk@putty.projects.tartarus.org") + string(encryptedFixture);
        CHECK(query(false, encrypted) == QByteArray(1, char(6)));
        auto deferred = std::async(std::launch::async, [] { return windowQuery(signRequest("cancel")); });
        CHECK(until([&] { return !prompts.isEmpty(); }));
        CHECK(runtime.submit(reply(runtime, prompts.takeFirst(), true)) == PR_OK);
        CHECK(until([&] { return deferred.wait_for(std::chrono::milliseconds(0)) == std::future_status::ready; }));
        CHECK(deferred.get() == QByteArray(1, char(5)));
        auto decrypt = std::async(std::launch::async, [] { return pipeQuery(signRequest("decrypt")); });
        CHECK(until([&] { return !prompts.isEmpty(); }));
        CHECK(runtime.submit(reply(runtime, prompts.takeFirst(), false)) == PR_OK);
        CHECK(until([&] { return decrypt.wait_for(std::chrono::milliseconds(0)) == std::future_status::ready; }));
        const auto signedReply = decrypt.get();
        const auto blob = publicKey();
        CHECK(signedReply[0] == char(14) && u32(signedReply, 1) == quint32(signedReply.size() - 5));
        CHECK(pa_test_verify(blob.constData(), size_t(blob.size()), signedReply.constData() + 5,
                             size_t(signedReply.size() - 5), "decrypt", 7));
        testStage("在途与部分帧关闭", 110);
        const auto reencrypt = QByteArray(1, char(27)) + string("reencrypt@putty.projects.tartarus.org") + string(blob);
        CHECK(query(false, reencrypt) == QByteArray(1, char(6)));
        auto pending = std::async(std::launch::async, [] { return windowQuery(signRequest("pending")); });
        CHECK(until([&] { return !prompts.isEmpty(); }));
        const auto cancelledPrompt = prompts.takeFirst();
        char *pipeName = pa_windows_pipe_name();
        CHECK(WaitNamedPipeA(pipeName, 8000));
        HANDLE partial = CreateFileA(pipeName, GENERIC_READ | GENERIC_WRITE, 0, NULL, OPEN_EXISTING, 0, NULL);
        pa_windows_free(pipeName); CHECK(partial != INVALID_HANDLE_VALUE);
        DWORD written = 0; CHECK(WriteFile(partial, "\0\0", 2, &written, NULL) && written == 2);
        UnixSocket partialUnix(unixPath);
        CHECK(send(partialUnix.value, "\0\0", 2, 0) == 2);
        CHECK(queryUnix(list)[0] == char(12));
        QElapsedTimer closing; closing.start(); runtime.close();
        CHECK(closing.elapsed() < 500);
        CHECK(until([&] { return runtime.isClosed(); }));
        CHECK(until([&] { return pending.wait_for(std::chrono::milliseconds(0)) == std::future_status::ready; }));
        CHECK(pending.get() == QByteArray(1, char(5)));
        CHECK(runtime.submit(reply(runtime, cancelledPrompt, false)) == PR_CLOSING);
        CHECK(until([&] {
            DWORD available = 0;
            if (PeekNamedPipe(partial, NULL, 0, NULL, &available, NULL)) return false;
            return GetLastError() == ERROR_BROKEN_PIPE || GetLastError() == ERROR_PIPE_NOT_CONNECTED;
        }));
        CloseHandle(partial);
        char unused;
        const int end = recv(partialUnix.value, &unused, 1, 0);
        CHECK(!end || (end == SOCKET_ERROR &&
            (WSAGetLastError() == WSAECONNRESET || WSAGetLastError() == WSAECONNABORTED)));
        CHECK(GetFileAttributesA(unixPath.constData()) == INVALID_FILE_ATTRIBUTES && GetLastError() == ERROR_FILE_NOT_FOUND);
        CHECK(!FindWindowA(pa_windows_class_name(), pa_windows_class_name()));
        bool after = true; error = nullptr;
        lock = pa_windows_startup_lock(&after, &error);
        CHECK(lock && !error && !after); pa_windows_startup_unlock(lock);
        testStage("AF_UNIX 在途提示关闭", 111);
        unixPendingClose();
        testStage("全部完成", 112);
        std::puts("{\"schema\":1,\"kind\":\"pageant-ipc\",\"passed\":true,\"storage\":\"not-run\","
            "\"checks\":[\"startup-discovery\",\"dual-list\",\"pipe-add-wm-list\",\"dual-signatures\","
            "\"malformed-copydata\",\"malformed-mapping\",\"cross-transport-remove\",\"deferred-cancel\","
            "\"deferred-decrypt\",\"pending-close\",\"partial-pipe-close\",\"endpoint-cleanup\","
            "\"stalled-pipe-client-close\",\"stalled-window-client-close\",\"fragmented-client-query\","
            "\"unix-list\",\"unix-cross-transport-keys\",\"unix-signature\",\"unix-existing-file-preserved\","
            "\"unix-listener-failure-cleanup\",\"unix-partial-close\",\"unix-pending-close\",\"unix-endpoint-cleanup\"]}");
        return 0;
    } catch (const std::exception &) {
        std::fprintf(stderr, "Windows Pageant IPC 失败：检查未通过\n"); return 1;
    }
}
