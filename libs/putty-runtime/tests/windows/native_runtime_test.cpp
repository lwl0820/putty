/* 原生消息桥和 Qt HANDLE 通知；不启用持久化及客户端 PRNG。 */
#include <winsock2.h>
#include "event_module.h"
#include "putty_runtime/runtime.h"
#include <QCoreApplication>
#include <QElapsedTimer>
#include <QTcpServer>
#include <QTcpSocket>
#include <QThread>
#include <cstdio>
#include <functional>
#include <stdexcept>
#include <vector>

#ifdef NDEBUG
#error Windows 原生运行时测试必须保留 Release 断言
#endif
#define CHECK(value) do { if (!(value)) throw std::runtime_error(#value); } while (0)
using namespace putty_runtime;

static bool until(const std::function<bool()> &predicate, int timeout = 6000)
{
    QElapsedTimer timer;
    timer.start();
    while (!predicate() && timer.elapsed() < timeout) {
        QCoreApplication::processEvents(QEventLoop::AllEvents, 5);
        QThread::msleep(1);
    }
    return predicate();
}
static Command command(Runtime &runtime, pr_command_type type, qint64 argument = 0)
{
    Command result;
    result.generation = runtime.generation();
    result.object = 1;
    result.type = type;
    result.argument = argument;
    return result;
}
static unsigned count(const std::vector<Event> &events, const char *state)
{
    unsigned result = 0;
    for (const auto &event : events)
        if (event.type == PR_EVENT_STATE && event.data == state) ++result;
    return result;
}
int main(int argc, char **argv)
{
    QCoreApplication app(argc, argv);
    try {
        CHECK(qEnvironmentVariable("PUTTY_SUITE_NATIVE_NO_STORAGE") == "1");
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
        limits.events = 4096;
        limits.eventBytes = 2 * 1024 * 1024;
        Runtime runtime(windows_event_module, {}, limits);
        std::vector<Event> events;
        QByteArray received, urgent;
        QObject::connect(&runtime, &Runtime::eventReceived, &runtime, [&](const Event &event) {
            CHECK(event.type != PR_EVENT_ERROR);
            if (event.type == PR_EVENT_DATA) {
                if (event.object == 2) urgent.append(event.data);
                else received.append(event.data);
            }
            events.push_back(event);
        });
        CHECK(until([&] {
            for (const auto &event : events) if (event.type == PR_EVENT_READY) return true;
            return false;
        }));
        for (unsigned round = 1; round <= 3; ++round) {
            CHECK(runtime.submit(command(runtime, PR_COMMAND_CUSTOM, 1)) == PR_OK);
            CHECK(runtime.submit(command(runtime, PR_COMMAND_CUSTOM, 2)) == PR_OK);
            CHECK(until([&] { return count(events, "manual") == round && count(events, "auto") == round; }));
        }
        CHECK(runtime.submit(command(runtime, PR_COMMAND_OPEN, server.serverPort())) == PR_OK);
        CHECK(until([&] { return peers.size() == 1 && count(events, "connected") == 1; }));
        QByteArray payload;
        for (int index = 0; index < 256 * 1024; ++index) payload.append(char(index % 251));
        auto input = command(runtime, PR_COMMAND_INPUT);
        input.data = payload;
        CHECK(runtime.submit(input) == PR_OK);
        CHECK(until([&] { return received.size() == payload.size(); }));
        CHECK(received == payload);
        received.clear();
        CHECK(::send(SOCKET(peers.front()->socketDescriptor()), "!", 1, MSG_OOB) == 1);
        CHECK(peers.front()->write("after-oob", 9) == 9);
        peers.front()->flush();
        CHECK(until([&] { return urgent == "!" && received == "after-oob"; }));
        CHECK(runtime.submit(command(runtime, PR_COMMAND_CLOSE_OBJECT)) == PR_OK);
        CHECK(until([&] { return count(events, "local-close") == 1 &&
            peers.front()->state() == QAbstractSocket::UnconnectedState; }));
        CHECK(runtime.submit(command(runtime, PR_COMMAND_OPEN, server.serverPort())) == PR_OK);
        CHECK(until([&] { return peers.size() == 2 && count(events, "connected") == 2; }));
        peers.back()->disconnectFromHost();
        CHECK(until([&] { return count(events, "closed") == 1; }));
        const auto destroyed = windows_event_destroyed();
        CHECK(runtime.submit(command(runtime, PR_COMMAND_CUSTOM, 3)) == PR_OK);
        CHECK(until([&] { return runtime.isClosed(); }));
        CHECK(windows_event_destroyed() == destroyed + 1);
        CHECK(windows_event_thread_errors() == 0);
        QCoreApplication::processEvents();
        std::puts("{\"schema\":1,\"kind\":\"events\",\"passed\":true,"
                  "\"checks\":[\"qt-manual-auto\",\"hwnd-tcp\",\"fd-oob\",\"close-reopen\",\"pending-close\",\"owner-thread\"],"
                  "\"storage\":\"not-run\"}");
        return 0;
    } catch (const std::exception &error) {
        std::fprintf(stderr, "Windows 原生事件失败：%s\n", error.what());
        return 1;
    }
}
