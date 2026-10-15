#include "putty_runtime/runtime.h"
#include "test_module.h"
#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QElapsedTimer>
#include <QTcpServer>
#include <QTcpSocket>
#include <QThread>
#include <QTimer>
#include <cstdio>
#include <stdexcept>
#include <functional>
#include <vector>
#ifdef Q_OS_WIN
#include <windows.h>
#else
#include <sys/socket.h>
#endif
using namespace putty_runtime;
#define CHECK(expr) do { if (!(expr)) throw std::runtime_error(#expr); } while (false)

static bool until(const std::function<bool()> &predicate, int timeout = 5000)
{
    QElapsedTimer clock;
    clock.start();
    while (!predicate() && clock.elapsed() < timeout) {
        QCoreApplication::processEvents(QEventLoop::AllEvents, 5);
        QThread::msleep(1);
    }
    return predicate();
}
static Command command(Runtime &runtime, pr_command_type type, int64_t argument = 0)
{
    Command value;
    value.generation = runtime.generation();
    value.object = 1;
    value.type = type;
    value.argument = argument;
    return value;
}
static void waitReady(Runtime &runtime, std::vector<Event> &events)
{
    QObject::connect(&runtime, &Runtime::eventReceived, &runtime,
                     [&events](const Event &event) { events.push_back(event); });
    CHECK(until([&] {
        for (const auto &event : events) if (event.type == PR_EVENT_READY) return true;
        return false;
    }));
}
static bool state(const std::vector<Event> &events, const QByteArray &text)
{
    for (const auto &event : events)
        if (event.type == PR_EVENT_STATE && event.data == text) return true;
    return false;
}
static void lifecycle()
{
    std::vector<Event> events;
    Runtime runtime(runtime_test_module);
    waitReady(runtime, events);
    std::vector<Event> busyEvents;
    Runtime busy(runtime_test_module);
    QObject::connect(&busy, &Runtime::eventReceived, &busy,
                     [&busyEvents](const Event &event) { busyEvents.push_back(event); });
    CHECK(until([&] { return busy.isClosed(); }));
    CHECK(until([&] { return !busyEvents.empty(); }));
    CHECK(busyEvents.front().status == PR_BUSY);
    auto stale = command(runtime, PR_COMMAND_CUSTOM);
    stale.generation--;
    CHECK(runtime.submit(stale) == PR_STALE);
    CHECK(runtime.submit(command(runtime, PR_COMMAND_CUSTOM, 1)) == PR_OK);
    CHECK(until([&] { return state(events, "timer") && state(events, "callback"); }));
    CHECK(runtime_test_late_callbacks() == 0);
    CHECK(runtime.submit(command(runtime, PR_COMMAND_CUSTOM, 2)) == PR_OK);
    uint64_t request = 0;
    CHECK(until([&] {
        for (const auto &event : events) if (event.type == PR_EVENT_REQUEST) request = event.request;
        return request != 0;
    }));
    auto reply = command(runtime, PR_COMMAND_REPLY);
    reply.request = request;
    reply.object = 2;
    CHECK(runtime.submit(reply) == PR_OK);
    CHECK(until([&] {
        for (const auto &event : events)
            if (event.type == PR_EVENT_COMPLETED && event.request == request && event.status == PR_STALE)
                return true;
        return false;
    }));
    reply.object = 1;
    CHECK(runtime.submit(reply) == PR_OK);
    CHECK(until([&] { return state(events, "reply"); }));
    const auto before = events.size();
    CHECK(runtime.submit(reply) == PR_OK);
    CHECK(until([&] { return events.size() > before; }));
    CHECK(events.back().status == PR_STALE);
    CHECK(runtime.submit(command(runtime, PR_COMMAND_CUSTOM, 2)) == PR_OK);
    CHECK(until([&] {
        unsigned count = 0;
        for (const auto &event : events) if (event.type == PR_EVENT_REQUEST) ++count;
        return count == 2;
    }));
    CHECK(runtime.submit(command(runtime, PR_COMMAND_CUSTOM, 5)) == PR_OK);
    CHECK(until([&] { return state(events, "storage"); }));
    const unsigned destroyed = runtime_test_destroyed();
    runtime.close();
    runtime.close();
    CHECK(runtime.submit(command(runtime, PR_COMMAND_CUSTOM)) == PR_CLOSING);
    CHECK(until([&] { return runtime.isClosed(); }));
    CHECK(runtime_test_destroyed() == destroyed + 1);
    CHECK(runtime_test_cancelled() == 1);
    CHECK(runtime_test_late_callbacks() == 0);
    CHECK(runtime_test_thread_errors() == 0);
    uint64_t sequence = 0;
    for (const auto &event : events) {
        CHECK(event.generation == runtime.generation());
        CHECK(event.sequence > sequence);
        sequence = event.sequence;
    }
}
static void eventCopyLimit()
{
    Limits limits;
    limits.events = 4;
    limits.eventBytes = 64;
    Runtime runtime(runtime_test_module, {}, limits);
    std::vector<Event> events;
    waitReady(runtime, events);
    auto probe = command(runtime, PR_COMMAND_CUSTOM, 7);
    probe.request = 700;
    CHECK(runtime.submit(probe) == PR_OK);
    /* 暂停消费者，保证第二个载荷超过剩余容量，但没有超过总容量。 */
    QElapsedTimer clock;
    clock.start();
    while (!runtime_test_oversize_done() && clock.elapsed() < 2000) QThread::msleep(1);
    CHECK(runtime_test_oversize_done() == 1);
    CHECK(until([&] {
        for (const auto &event : events)
            if (event.type == PR_EVENT_COMPLETED && event.request == 700)
                return event.status == PR_OK;
        return false;
    }));
    unsigned dataEvents = 0;
    for (const auto &event : events) if (event.type == PR_EVENT_DATA) {
        CHECK(event.data.size() == 48);
        ++dataEvents;
    }
    CHECK(dataEvents == 1);
    CHECK(runtime.eventHighWater() <= limits.events);
    runtime.close();
    CHECK(until([&] { return runtime.isClosed(); }));
}
static void network()
{
    QTcpServer server;
    CHECK(server.listen(QHostAddress::LocalHost, 0));
    QList<QTcpSocket *> peers;
    QObject::connect(&server, &QTcpServer::newConnection, &server, [&] {
        auto *peer = server.nextPendingConnection();
        peers.append(peer);
        QObject::connect(peer, &QTcpSocket::readyRead, peer, [peer] {
            peer->write(peer->readAll());
        });
    });
    Limits limits;
    limits.commands = 8;
    limits.events = 4;
    limits.eventBytes = 65536;
    Runtime runtime(runtime_test_module, {}, limits);
    std::vector<Event> events;
    waitReady(runtime, events);
    CHECK(runtime.submit(command(runtime, PR_COMMAND_OPEN, server.serverPort())) == PR_OK);
    CHECK(until([&] { return !peers.empty() && state(events, "connected"); }));
    QByteArray received;
    QObject::connect(&runtime, &Runtime::eventReceived, &runtime, [&received](const Event &event) {
        if (event.type == PR_EVENT_DATA) received.append(event.data);
    });
    QByteArray payload;
    for (int i = 0; i < 256 * 1024; ++i) payload.append(char(i % 251));
    auto input = command(runtime, PR_COMMAND_INPUT);
    input.data = payload;
    CHECK(runtime.submit(input) == PR_OK);
    CHECK(until([&] { return received.size() == payload.size(); }));
    CHECK(received == payload);
    received.clear();
    peers.front()->write(payload);
    peers.front()->flush();
    /* 阻塞消费者，让核心填满有界事件队列并冻结真实 socket。 */
    QThread::msleep(80);
    CHECK(until([&] { return received.size() == payload.size(); }));
    CHECK(received == payload);
    CHECK(runtime.eventHighWater() <= limits.events);
#ifndef Q_OS_WIN
    received.clear();
    CHECK(peers.front()->write("before", 6) == 6);
    peers.front()->flush();
    CHECK(::send(int(peers.front()->socketDescriptor()), "!", 1, MSG_OOB) == 1);
    CHECK(peers.front()->write("after", 5) == 5);
    peers.front()->flush();
    CHECK(until([&] { return received == "before!after" && state(events, "urgent"); }));
#endif
    CHECK(runtime.submit(command(runtime, PR_COMMAND_CLOSE_OBJECT)) == PR_OK);
    CHECK(until([&] { return peers.front()->state() == QAbstractSocket::UnconnectedState; }));
    CHECK(runtime.submit(command(runtime, PR_COMMAND_OPEN, server.serverPort())) == PR_OK);
    CHECK(until([&] { return peers.size() == 2; }));
    peers.back()->disconnectFromHost();
    CHECK(until([&] { return state(events, "disconnected"); }));
    runtime.close();
    CHECK(until([&] { return runtime.isClosed(); }));
    CHECK(runtime_test_thread_errors() == 0);
}
static void asynchronousClose()
{
    Limits limits;
    limits.commands = 4;
    Runtime runtime(runtime_test_module, {}, limits);
    std::vector<Event> events;
    waitReady(runtime, events);
    CHECK(runtime.submit(command(runtime, PR_COMMAND_CUSTOM, 3)) == PR_OK);
    QThread::msleep(30);
    unsigned accepted = 0;
    while (runtime.submit(command(runtime, PR_COMMAND_CUSTOM)) == PR_OK && accepted < 100) ++accepted;
    CHECK(accepted <= limits.commands);
    QElapsedTimer closeClock;
    closeClock.start();
    runtime.close();
    CHECK(closeClock.elapsed() < 50);
    CHECK(!runtime.isClosed());
    int heartbeats = 0;
    QTimer heartbeat;
    QObject::connect(&heartbeat, &QTimer::timeout, &heartbeat, [&] { ++heartbeats; });
    heartbeat.start(10);
    CHECK(until([&] { return runtime.isClosed(); }));
    CHECK(heartbeats > 10);
    CHECK(runtime.commandHighWater() <= limits.commands);
    const unsigned destroyed = runtime_test_destroyed();
    auto *detached = new Runtime(runtime_test_module);
    std::vector<Event> detachedEvents;
    waitReady(*detached, detachedEvents);
    CHECK(detached->submit(command(*detached, PR_COMMAND_CUSTOM, 3)) == PR_OK);
    QThread::msleep(30);
    delete detached;
    CHECK(until([&] { return runtime_test_destroyed() == destroyed + 1; }));
    CHECK(runtime_test_late_callbacks() == 0);
    Runtime callbackClose(runtime_test_module);
    std::vector<Event> callbackEvents;
    waitReady(callbackClose, callbackEvents);
    CHECK(callbackClose.submit(command(callbackClose, PR_COMMAND_CUSTOM, 4)) == PR_OK);
    CHECK(until([&] { return callbackClose.isClosed(); }));
    CHECK(runtime_test_late_callbacks() == 0);
    const unsigned beforeDeleteSlot = runtime_test_destroyed();
    auto *deletedInSlot = new Runtime(runtime_test_module);
    std::vector<Event> deleteEvents;
    waitReady(*deletedInSlot, deleteEvents);
    QObject::connect(deletedInSlot, &Runtime::eventReceived, deletedInSlot,
        [deletedInSlot](const Event &event) {
            if (event.data == "delete-in-slot") delete deletedInSlot;
        });
    CHECK(deletedInSlot->submit(command(*deletedInSlot, PR_COMMAND_CUSTOM, 6)) == PR_OK);
    CHECK(until([&] { return runtime_test_destroyed() == beforeDeleteSlot + 1; }));
}
int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
#ifdef Q_OS_WIN
    /* 未经平台注册表隔离的自动运行禁止创建随机种子或访问个人 HKCU。 */
    std::fputs("Windows 原生测试需要已验证的测试身份/注册表与随机种子隔离夹具\n", stderr);
    return 77;
#else
    const auto isolatedRoot = qEnvironmentVariable("PUTTY_RUNTIME_ISOLATED_ROOT");
    const auto canonical = QFileInfo(isolatedRoot).canonicalFilePath();
    if (isolatedRoot.isEmpty() || canonical.isEmpty() ||
        !canonical.startsWith(QString::fromUtf8(PUTTY_RUNTIME_PROJECT_TMP) + "/")) {
        std::fputs("必须通过已验证的项目私有隔离运行器启动测试\n", stderr);
        return 1;
    }
#endif
    try {
        CHECK(pr_core_timer_delay(5UL, 10UL) == 0);
        CHECK(pr_core_timer_delay(5UL, ~0UL - 4UL) == 10);
        lifecycle();
#ifndef Q_OS_WIN
        CHECK(runtime_test_fd_close());
#endif
        eventCopyLimit();
        network();
        asynchronousClose();
        QCoreApplication::processEvents();
        std::puts("运行时：真实 TCP、回调、计时、一次性请求、背压及异步关闭通过");
        return 0;
    } catch (const std::exception &error) {
        std::fprintf(stderr, "运行时失败：%s\n", error.what());
        return 1;
    }
}
