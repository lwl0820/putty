/* 真实 WinAPI 对端；仅编译随机命名空间版本，绝不探测个人 Pageant。 */
#include <windows.h>
#include "app/agent_codec.h"
#include "core/pa_windows.h"
#include "encrypted_fixture.h"
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QProcess>
#include <QThread>
#include <QTimer>
#include <cstdio>
#include <future>
#include <functional>
#include <stdexcept>

#ifndef PA_WINDOWS_TEST_NAMESPACE
#error 原生 IPC 测试必须编译到专用命名空间
#endif
#ifdef NDEBUG
#error 原生 IPC 测试必须保留断言
#endif
#define CHECK(value) do { if (!(value)) throw std::runtime_error(#value); } while (0)
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
int main(int argc, char **argv)
{
    pa_windows_prepare();
    QCoreApplication app(argc, argv);
    try {
        CHECK(qEnvironmentVariable("PUTTY_SUITE_NATIVE_NO_STORAGE") == "1");
        const auto arguments = app.arguments();
        const auto scopeIndex = arguments.indexOf(QStringLiteral("--scope"));
        CHECK(scopeIndex >= 0 && scopeIndex + 1 < arguments.size());
        const auto scope = arguments[scopeIndex + 1].toLatin1();
        CHECK(pa_windows_set_test_scope(scope.constData()));
        CHECK(QByteArray(pa_windows_class_name()).startsWith("SuitePageantTest-"));
        if (arguments.contains(QStringLiteral("--stall-pipe"))) return stalledServer(false);
        if (arguments.contains(QStringLiteral("--stall-window"))) return stalledServer(true);
        if (arguments.contains(QStringLiteral("--fragmented-pipe"))) return stalledServer(false, true);
        if (arguments.contains(QStringLiteral("--query-client"))) {
            CHECK(pa_test_remote_query()); marker("fragmented-query-ok"); return 0;
        }
        if (arguments.contains(QStringLiteral("--bounded-client"))) return boundedClient();
        if (!arguments.contains(QStringLiteral("--probe-existing"))) {
            checkStalledClient(false, QString::fromLatin1(scope), true);
            checkStalledClient(false, QString::fromLatin1(scope));
            checkStalledClient(true, QString::fromLatin1(scope));
        }
        bool existing = false; char *error = nullptr;
        void *lock = pa_windows_startup_lock(&existing, &error);
        CHECK(lock && !error);
        if (app.arguments().contains(QStringLiteral("--probe-existing"))) {
            pa_windows_startup_unlock(lock); return existing ? 0 : 1;
        }
        CHECK(!existing);
        pa_options options{};
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
        const auto list = QByteArray(1, char(11));
        CHECK(query(false, list) == QByteArray(1, char(12)) + number(0));
        CHECK(query(true, list) == QByteArray(1, char(12)) + number(0));
        CHECK(query(false, addKey()) == QByteArray(1, char(6)));
        const auto listed = query(true, list);
        CHECK(listed.size() > 5 && listed[0] == char(12) && u32(listed, 1) == 1);
        CHECK(listed.mid(9, qsizetype(u32(listed, 5))) == publicKey());
        verify(false, QByteArrayLiteral("管道签名\0二进制"));
        verify(true, QByteArrayLiteral("旧窗口签名\0二进制"));
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
        CHECK(!FindWindowA(pa_windows_class_name(), pa_windows_class_name()));
        bool after = true; error = nullptr;
        lock = pa_windows_startup_lock(&after, &error);
        CHECK(lock && !error && !after); pa_windows_startup_unlock(lock);
        std::puts("{\"schema\":1,\"kind\":\"pageant-ipc\",\"passed\":true,\"storage\":\"not-run\","
            "\"checks\":[\"startup-discovery\",\"dual-list\",\"pipe-add-wm-list\",\"dual-signatures\","
            "\"malformed-copydata\",\"malformed-mapping\",\"cross-transport-remove\",\"deferred-cancel\","
            "\"deferred-decrypt\",\"pending-close\",\"partial-pipe-close\",\"endpoint-cleanup\","
            "\"stalled-pipe-client-close\",\"stalled-window-client-close\",\"fragmented-client-query\"]}");
        return 0;
    } catch (const std::exception &error) {
        std::fprintf(stderr, "Windows Pageant IPC 失败：%s\n", error.what()); return 1;
    }
}
