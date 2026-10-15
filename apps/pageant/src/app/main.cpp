#include "agent_controller.h"
#include "core/pa_cli.h"
#include "language.h"
#include "ui/agent_window.h"
#include <QApplication>
#include <QDialogButtonBox>
#include <QInputDialog>
#include <QLineEdit>
#include <QMessageBox>
#include <QTemporaryDir>
#include <cstdio>
#include <cstring>
#include <sys/stat.h>
#include <unistd.h>

static int askpass(int &argc, char **argv)
{
    const auto prompt = QString::fromUtf8(argv[2]);
    QApplication app(argc, argv);
    pageant::Language language;
    QInputDialog dialog;
    dialog.setWindowTitle(language.text("密钥口令", "Key passphrase"));
    dialog.setLabelText(prompt);
    dialog.setTextEchoMode(QLineEdit::Password);
    if (dialog.exec() != QDialog::Accepted) return 1;
    auto text = dialog.textValue().toUtf8();
    dialog.setTextValue(QString());
    const bool ok = fwrite(text.constData(), 1, size_t(text.size()), stdout) == size_t(text.size()) &&
                    fputc('\n', stdout) != EOF && fflush(stdout) == 0;
    text.fill('\0');
    return ok ? 0 : 1;
}
int main(int argc, char **argv)
{
    // 原 CLI 分支之前不创建 QApplication、核心 Runtime 或任何 Qt 工作线程。
    if (argc == 3 && !strcmp(argv[1], "--qt-askpass")) return askpass(argc, argv);
    if (argc < 2 || strcmp(argv[1], "--gui")) {
        pa_cli_prepare();
        return pageant_cli_main(argc, argv);
    }
    if (argc != 2) {
        fputs("用法：pageant --gui\n", stderr);
        return 2;
    }
    umask(077);
    QApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("Pageant"));
    app.setOrganizationName(QStringLiteral("PuTTYSuite"));
    app.setQuitOnLastWindowClosed(false);
    pageant::Language language;
    const auto runtimePath = qgetenv("XDG_RUNTIME_DIR");
    struct stat info;
    if (runtimePath.isEmpty() || runtimePath[0] != '/' ||
        lstat(runtimePath.constData(), &info) || !S_ISDIR(info.st_mode) ||
        info.st_uid != getuid() || (info.st_mode & 077)) {
        QMessageBox::critical(nullptr, QStringLiteral("Pageant"), language.text(
            "无法使用当前运行目录。请确认 XDG_RUNTIME_DIR 指向仅属于当前用户的私有目录。",
            "The runtime directory is unavailable. XDG_RUNTIME_DIR must name a private directory owned by the current user."));
        return 1;
    }
    QTemporaryDir directory(QString::fromUtf8(runtimePath) + QStringLiteral("/pageant-XXXXXX"));
    const auto socket = (directory.path() + QStringLiteral("/agent")).toUtf8();
    if (!directory.isValid() || socket.size() >= qsizetype(sizeof(pa_options::socket_path))) {
        QMessageBox::critical(nullptr, QStringLiteral("Pageant"), language.text(
            "无法创建代理地址。", "The agent address could not be created."));
        return 1;
    }
    pa_options options{};
    memcpy(options.socket_path, socket.constData(), size_t(socket.size()));
    pageant::AgentController controller(options);
    pageant::AgentWindow window(&controller, &language);
    QObject::connect(&controller, &pageant::AgentController::closed, &app, [&] {
        if (!controller.failedFatally()) { app.quit(); return; }
        auto *message = new QMessageBox(QMessageBox::Critical, QStringLiteral("Pageant"),
            language.text("代理无法继续运行，请关闭后重试。", "The agent could not continue. Close it and try again."),
            QMessageBox::NoButton, &window);
        message->setAttribute(Qt::WA_DeleteOnClose);
        message->addButton(language.text("关闭", "Close"), QMessageBox::AcceptRole);
        QObject::connect(message, &QMessageBox::finished, &app, [&app] { app.exit(1); });
        message->open();
    });
    QObject::connect(&app, &QCoreApplication::aboutToQuit, &controller, &pageant::AgentController::close);
    window.show();
    return app.exec();
}
