#include "app/agent_codec.h"
#include <QCoreApplication>
#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSocketNotifier>
#include <QTimer>
#include <cstdio>
#include <cstring>
#include <unistd.h>
#include <sys/stat.h>

/* 仅供项目夹具使用的核心驱动；不安装，不接受上游私有指针。 */
int main(int argc, char **argv)
{
    umask(077);
    QCoreApplication app(argc, argv);
    if (argc != 2 || strlen(argv[1]) >= sizeof(pa_options::socket_path)) return 2;
    pa_options options{};
    memcpy(options.socket_path, argv[1], strlen(argv[1]));
    putty_runtime::Runtime runtime(pa_agent_module,
        QByteArray(reinterpret_cast<const char *>(&options), sizeof(options)));
    QObject::connect(&runtime, &putty_runtime::Runtime::closed, &app, &QCoreApplication::quit);
    QObject::connect(&runtime, &putty_runtime::Runtime::eventReceived, &app,
        [&runtime](const putty_runtime::Event &event) {
        QJsonObject out{{"type", int(event.type)}, {"status", int(event.status)},
                        {"request", QString::number(event.request)}};
        pageant::Packet packet;
        if (!event.data.isEmpty()) {
            if (!pageant::decode(event.data, packet)) { runtime.close(); return; }
            out["kind"] = int(packet.header.kind);
            out["action"] = int(packet.header.action);
            out["result"] = int(packet.header.result);
            out["text"] = QString::fromUtf8(packet.text);
            QJsonArray keys;
            for (const auto &key : packet.keys) keys.append(QJsonObject{
                {"version", int(key.version)}, {"flags", int(key.flags)},
                {"blob", QString::fromLatin1(key.blob.toBase64())},
                {"comment", key.comment}, {"fingerprint", key.fingerprint}});
            out["keys"] = keys;
        }
        const auto line = QJsonDocument(out).toJson(QJsonDocument::Compact) + '\n';
        fwrite(line.constData(), 1, size_t(line.size()), stdout); fflush(stdout);
    });
    QByteArray incoming;
    QSocketNotifier input(STDIN_FILENO, QSocketNotifier::Read, &app);
    QObject::connect(&input, &QSocketNotifier::activated, &app, [&] {
        char buffer[8192];
        const auto n = read(STDIN_FILENO, buffer, sizeof(buffer));
        if (n <= 0) { input.setEnabled(false); runtime.close(); return; }
        incoming.append(buffer, n);
        while (true) {
            const auto end = incoming.indexOf('\n');
            if (end < 0) break;
            auto line = incoming.left(end);
            incoming.remove(0, end + 1);
            const auto obj = QJsonDocument::fromJson(line).object();
            if (obj.value("close").toBool()) { runtime.close(); break; }
            pageant::Identity key;
            key.version = obj.value("version").toInt(2);
            key.blob = QByteArray::fromBase64(obj.value("blob").toString().toLatin1());
            const auto action = pa_action(obj.value("action").toInt());
            auto pp = obj.value("passphrase").toString().toUtf8();
            putty_runtime::Command command;
            command.generation = runtime.generation(); command.object = 1;
            command.request = obj.value("request").toString().toULongLong();
            command.type = action == PA_REPLY ? PR_COMMAND_REPLY : PR_COMMAND_CUSTOM;
            command.data = pageant::input(action, obj.value("flags").toInt(),
                obj.value("path").toString().toUtf8(), pp, key);
            if (obj.value("corrupt").toBool() && !command.data.isEmpty()) command.data[0] = '\0';
            pp.fill('\0'); line.fill('\0');
            const auto status = runtime.submit(std::move(command));
            if (status != PR_OK) {
                fprintf(stderr, "核心命令队列拒绝：%d\n", int(status));
                runtime.close();
            }
        }
        if (incoming.size() > 1024 * 1024) runtime.close();
    });
    QTimer::singleShot(120000, &runtime, &putty_runtime::Runtime::close);
    return app.exec();
}
