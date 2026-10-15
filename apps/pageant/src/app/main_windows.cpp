#include "agent_controller.h"
#include "core/pa_windows.h"
#include "language.h"
#include "ui/agent_window.h"
#include <QApplication>
#include <QMessageBox>

int main(int argc, char **argv)
{
    pa_windows_prepare();
    QApplication app(argc, argv);
    app.setApplicationName(QStringLiteral("Pageant"));
    app.setOrganizationName(QStringLiteral("PuTTYSuite"));
    app.setQuitOnLastWindowClosed(false);
    pageant::Language language;
    QStringList files;
    QList<bool> encryptedFiles;
    bool encrypted = false;
    const auto arguments = app.arguments();
    for (qsizetype index = 1; index < arguments.size(); ++index) {
        const auto &argument = arguments[index];
        if (argument == QStringLiteral("--gui") || argument == QStringLiteral("-keylist")) continue;
        if (argument == QStringLiteral("-encrypted") || argument == QStringLiteral("-no-decrypt") ||
            argument == QStringLiteral("-nodecrypt") || argument == QStringLiteral("-no_decrypt")) {
            encrypted = true; continue;
        }
        if (argument.startsWith(u'-')) {
            QMessageBox::critical(nullptr, QStringLiteral("Pageant"), language.text(
                "无法识别此参数：", "Unrecognised option: ") + argument);
            return 2;
        }
        files.append(argument);
        encryptedFiles.append(encrypted);
    }
    char *error = nullptr;
    bool existing = false;
    void *lock = pa_windows_startup_lock(&existing, &error);
    if (!lock) {
        QMessageBox::critical(nullptr, QStringLiteral("Pageant"), language.text(
            "无法启动代理：", "The agent could not start: ") + QString::fromUtf8(error));
        pa_windows_free(error);
        return 1;
    }
    if (existing) pa_windows_startup_unlock(lock);
    if (existing && files.isEmpty()) {
        QMessageBox::information(nullptr, QStringLiteral("Pageant"), language.text(
            "Pageant 已在运行。", "Pageant is already running."));
        return 0;
    }
    pa_options options{};
    options.client_only = existing;
    pageant::AgentController controller(options);
    pageant::AgentWindow window(&controller, &language);
    bool locked = !existing;
    bool messagePending = false;
    int status = 0;
    const auto unlock = [&] {
        if (locked) { pa_windows_startup_unlock(lock); locked = false; }
    };
    QObject::connect(&controller, &pageant::AgentController::stateChanged, &app, [&] {
        if (!controller.isReady()) return;
        unlock();
        if (controller.adding()) return;
        if (!files.isEmpty()) {
            const auto file = files.takeFirst();
            const auto loadEncrypted = encryptedFiles.takeFirst();
            controller.addFiles({file}, loadEncrypted);
            return;
        }
        // 二次启动与原版相同：添加到已有代理后退出，不清除对端身份。
        if (existing && !messagePending) controller.close();
    });
    QObject::connect(&controller, &pageant::AgentController::completed, &app,
        [&](int, int result, const QString &detail) {
            if (result == PA_FAILURE || result == PA_CANCELLED) status = 1;
            if (existing && result != PA_SUCCESS && result != PA_CANCELLED) {
                messagePending = true;
                auto *message = new QMessageBox(result == PA_WARNING ? QMessageBox::Warning : QMessageBox::Critical,
                    QStringLiteral("Pageant"), detail.isEmpty() ? language.text(
                        "无法添加此密钥。", "This key could not be added.") : detail,
                    QMessageBox::Ok, &window);
                message->setAttribute(Qt::WA_DeleteOnClose);
                QObject::connect(message, &QMessageBox::finished, &app, [&] {
                    messagePending = false;
                    if (!controller.adding() && files.isEmpty()) controller.close();
                });
                message->open();
            }
        });
    QObject::connect(&controller, &pageant::AgentController::closed, &app, [&] {
        unlock();
        if (controller.failedFatally()) {
            QMessageBox::critical(&window, QStringLiteral("Pageant"), language.text(
                "代理无法继续运行，请关闭后重试。", "The agent could not continue. Close it and try again."));
            status = 1;
        }
        app.exit(status);
    });
    QObject::connect(&app, &QCoreApplication::aboutToQuit, &controller, &pageant::AgentController::close);
    window.show();
    const int result = app.exec();
    unlock();
    return result;
}
