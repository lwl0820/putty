#include "terminal_controller.h"
#include "ui/terminal_window.h"
#include <QApplication>
#include <QCommandLineParser>
#include <QFontDatabase>
#include <cstring>

int main(int argc, char **argv)
{
    QApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("PuTTY"));
    app.setOrganizationName(QStringLiteral("PuTTY Suite"));
    QFontDatabase::families();
    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("PuTTY SSH 终端")); parser.addHelpOption();
    parser.addOptions({{"host", QStringLiteral("主机"), "host"},
        {"port", QStringLiteral("端口"), "port", "22"},
        {"user", QStringLiteral("用户名"), "user"},
        {"key", QStringLiteral("PPK 私钥"), "file"},
        {"command", QStringLiteral("远程命令"), "command"}});
    parser.process(app);
    pt_connection connection = {};
    if (parser.isSet("host")) {
        const auto copy = [](char *out, size_t size, const QString &value) {
            const auto bytes = value.toUtf8();
            if (size_t(bytes.size()) >= size) return false;
            memcpy(out, bytes.constData(), bytes.size()); return true;
        };
        bool valid = copy(connection.host, sizeof(connection.host), parser.value("host")) &&
            copy(connection.user, sizeof(connection.user), parser.value("user")) &&
            copy(connection.keyfile, sizeof(connection.keyfile), parser.value("key")) &&
            copy(connection.command, sizeof(connection.command), parser.value("command"));
        bool portValid = false; connection.port = parser.value("port").toInt(&portValid); connection.request_pty = 1;
        if (!valid || !portValid || connection.port < 1 || connection.port > 65535) return 2;
    }
    pt_render_config render = {1, 80, 24, 14, 96, 1000, "monospace"};
    TerminalController controller(render);
    TerminalWindow window(&controller); window.show();
    if (parser.isSet("host"))
        QObject::connect(&controller, &TerminalController::ready, &controller, [&controller, connection] { controller.open(connection); });
    return app.exec();
}
