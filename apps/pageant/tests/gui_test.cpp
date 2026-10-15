#include "app/agent_controller.h"
#include "app/language.h"
#include "ui/agent_window.h"
#include <QAction>
#include <QApplication>
#include <QDialog>
#include <QDialogButtonBox>
#include <QFileDialog>
#include <QFileInfo>
#include <QLineEdit>
#include <QLabel>
#include <QMessageBox>
#include <QProcess>
#include <QProcessEnvironment>
#include <QPushButton>
#include <QSettings>
#include <QSignalSpy>
#include <QTableWidget>
#include <QTest>
#include <cstring>
#include <sys/stat.h>
#include <unistd.h>
using namespace pageant;

class AgentGuiTest final : public QObject {
    Q_OBJECT
private:
    AgentController *controller = nullptr;
    Language *language = nullptr;
    AgentWindow *window = nullptr;
    QString root, key, encrypted, secondEncrypted, secondPublic;
    bool trayAvailable = false;
    QAction *action(const char *name) { return window->findChild<QAction *>(QString::fromLatin1(name)); }
    QTableWidget *table() { return window->findChild<QTableWidget *>(QStringLiteral("identities")); }
    QDialog *prompt() { return window->findChild<QDialog *>(QStringLiteral("passphraseDialog")); }
    void answer(const QString &text, bool accept)
    {
        auto *dialog = prompt(); QVERIFY(dialog);
        dialog->findChild<QLineEdit *>(QStringLiteral("passphrase"))->setText(text);
        auto *box = dialog->findChild<QDialogButtonBox *>();
        QTest::mouseClick(box->button(accept ? QDialogButtonBox::Ok : QDialogButtonBox::Cancel), Qt::LeftButton);
    }
    void addViaDialog(const QString &path, const char *operation)
    {
        action(operation)->trigger();
        QTRY_VERIFY(window->findChild<QFileDialog *>());
        auto *dialog = window->findChild<QFileDialog *>();
        dialog->selectFile(path);
        QVERIFY(QMetaObject::invokeMethod(dialog, "accept"));
    }
private slots:
    void init()
    {
        root = qEnvironmentVariable("PAGEANT_TEST_ROOT");
        QVERIFY(root.startsWith(QStringLiteral(PAGEANT_PROJECT_TMP) + '/'));
        key = qEnvironmentVariable("PAGEANT_TEST_KEY");
        encrypted = qEnvironmentVariable("PAGEANT_TEST_ENCRYPTED");
        secondEncrypted = qEnvironmentVariable("PAGEANT_TEST_SECOND_ENCRYPTED");
        secondPublic = qEnvironmentVariable("PAGEANT_TEST_SECOND_PUBLIC");
        pa_options options{};
        const auto socket = (root + QStringLiteral("/runtime/gui-agent")).toUtf8();
        QVERIFY(socket.size() < qsizetype(sizeof(options.socket_path)));
        memcpy(options.socket_path, socket.constData(), size_t(socket.size()));
        language = new Language;
        controller = new AgentController(options);
        window = new AgentWindow(controller, language, [this] { return trayAvailable; });
        window->show();
        QTRY_VERIFY(controller->isReady());
        QTRY_VERIFY(!controller->endpoint().isEmpty());
    }
    void cleanup()
    {
        window->requestExit();
        QTRY_VERIFY(controller->isClosed());
        QTRY_VERIFY(!QFileInfo::exists(controller->endpoint()));
        delete window; delete controller; delete language;
    }
    void defaultLanguageAndSelection()
    {
        QVERIFY(!language->english());
        QCOMPARE(window->windowTitle(), QStringLiteral("Pageant 密钥代理"));
        QVERIFY(!action("removeKey")->isEnabled());
        controller->addFiles({key}, false);
        QTRY_COMPARE(table()->rowCount(), 1);
        table()->selectRow(0);
        const auto blob = controller->keys()[0].blob;
        action("languageEnglish")->trigger();
        QCOMPARE(window->windowTitle(), QStringLiteral("Pageant Key Agent"));
        QCOMPARE(window->findChild<QLabel *>(QStringLiteral("agentStatus"))->text(), QStringLiteral("Operation completed"));
        QCOMPARE(controller->keys()[0].blob, blob);
        QCOMPARE(table()->selectionModel()->selectedRows().size(), 1);
        QVERIFY(action("removeKey")->isEnabled());
        action("languageChinese")->trigger();
        QSettings settings(QSettings::IniFormat, QSettings::UserScope, QStringLiteral("PuTTYSuite"), QStringLiteral("Pageant"));
        QCOMPARE(settings.value(QStringLiteral("language")).toString(), QStringLiteral("zh_CN"));
        QVERIFY(window->grab().save(root + QStringLiteral("/window.png")));
    }
    void realAddDeleteAndClearActions()
    {
        addViaDialog(key, "addKey");
        QTRY_COMPARE(table()->rowCount(), 1);
        table()->selectRow(0); action("removeKey")->trigger();
        QTRY_COMPARE(table()->rowCount(), 0);
        addViaDialog(encrypted, "addEncryptedKey");
        QTRY_COMPARE(table()->rowCount(), 1);
        QCOMPARE(controller->keys()[0].flags, 3u);
        action("clearKeys")->trigger();
        QTRY_COMPARE(table()->rowCount(), 0);
    }
    void serialPassphrasesAndReencrypt()
    {
        controller->addFiles({encrypted, secondEncrypted}, false);
        QTRY_VERIFY(prompt() && prompt()->isVisible());
        QPointer<QDialog> first = prompt();
        answer(QStringLiteral("wrong"), true);
        QTRY_VERIFY(!first);
        QTRY_VERIFY(prompt() && prompt()->isVisible());
        QPointer<QDialog> retry = prompt();
        answer(QString(), false);
        QTRY_VERIFY(!retry);
        QTRY_VERIFY(prompt() && prompt()->isVisible());
        QCOMPARE(window->findChildren<QDialog *>(QStringLiteral("passphraseDialog")).size(), 1);
        auto *edit = prompt()->findChild<QLineEdit *>(QStringLiteral("passphrase"));
        edit->setText(QStringLiteral("kept"));
        language->setEnglish(true);
        QCOMPARE(edit->text(), QStringLiteral("kept"));
        QCOMPARE(prompt()->windowTitle(), QStringLiteral("Key passphrase"));
        answer(QStringLiteral("gui-passphrase"), true);
        QTRY_COMPARE(controller->keys().size(), 1);
        QTRY_VERIFY(!controller->adding());
        controller->addFiles({secondEncrypted}, true);
        QTRY_COMPARE(controller->keys()[0].flags, 1u);
        table()->selectRow(0); QVERIFY(action("reencryptKey")->isEnabled());
        action("reencryptKey")->trigger();
        QTRY_COMPARE(controller->keys()[0].flags, 3u);
        QProcess sign;
        auto environment = QProcessEnvironment::systemEnvironment();
        environment.insert(QStringLiteral("SSH_AUTH_SOCK"), controller->endpoint());
        sign.setProcessEnvironment(environment);
        sign.start(QStringLiteral("ssh-keygen"), {QStringLiteral("-Y"), QStringLiteral("sign"),
            QStringLiteral("-f"), secondPublic, QStringLiteral("-n"), QStringLiteral("suite")});
        QVERIFY(sign.waitForStarted()); sign.write("gui-signature"); sign.closeWriteChannel();
        QTRY_VERIFY(prompt() && prompt()->isVisible());
        answer(QStringLiteral("gui-passphrase"), true);
        QTRY_COMPARE(sign.state(), QProcess::NotRunning);
        QCOMPARE(sign.exitCode(), 0);
        QVERIFY(sign.readAllStandardOutput().contains("BEGIN SSH SIGNATURE"));
        QTRY_COMPARE(controller->keys()[0].flags, 1u);
        action("reencryptAll")->trigger();
        QTRY_COMPARE(controller->keys()[0].flags, 3u);
    }
    void noTrayVisibleControlAndRecovery()
    {
        window->close();
        QTRY_VERIFY(window->findChild<QMessageBox *>(QStringLiteral("closeQuestion")));
        auto *question = window->findChild<QMessageBox *>(QStringLiteral("closeQuestion"));
        QAbstractButton *keep = nullptr;
        for (auto *button : question->buttons()) if (question->buttonRole(button) == QMessageBox::RejectRole) keep = button;
        QVERIFY(keep); QTest::mouseClick(keep, Qt::LeftButton);
        QTRY_VERIFY(window->isVisible());
        trayAvailable = true;
        QTest::qWait(1100);
        window->close();
        QTRY_VERIFY(!window->isVisible());
        trayAvailable = false;
        QTRY_VERIFY(window->isVisible());
    }
    void exitCancelsPromptAndSocket()
    {
        controller->addFiles({encrypted}, false);
        QTRY_VERIFY(prompt() && prompt()->isVisible());
        QSignalSpy closed(controller, &AgentController::closed);
        action("exitAgent")->trigger();
        QTRY_COMPARE(closed.count(), 1);
        QTRY_VERIFY(!prompt());
        QVERIFY(!QFileInfo::exists(controller->endpoint()));
    }
    void clearDuringPromptDismissesDialog()
    {
        controller->addFiles({encrypted}, false);
        QTRY_VERIFY(prompt() && prompt()->isVisible());
        action("clearKeys")->trigger();
        QTRY_VERIFY(!prompt());
        QTRY_VERIFY(!controller->adding());
        QTRY_COMPARE(table()->rowCount(), 0);
        controller->addFiles({key}, false);
        QTRY_COMPARE(table()->rowCount(), 1);
    }
    void oversizedPassphraseKeepsPrompt()
    {
        controller->addFiles({encrypted}, false);
        QTRY_VERIFY(prompt() && prompt()->isVisible());
        QPointer<QDialog> pending = prompt();
        answer(QString(30000, QChar(0x4e2d)), true);
        QCoreApplication::sendPostedEvents(nullptr, QEvent::DeferredDelete);
        QVERIFY(pending && pending->isVisible());
        QVERIFY(controller->adding());
        QVERIFY(!prompt()->findChild<QLabel *>(QStringLiteral("passphraseError"))->text().isEmpty());
        language->setEnglish(true);
        QVERIFY(prompt()->findChild<QLabel *>(QStringLiteral("passphraseError"))->text().contains(QStringLiteral("UTF-8")));
        answer(QStringLiteral("gui-passphrase"), true);
        QTRY_COMPARE(controller->keys().size(), 1);
        QTRY_VERIFY(!controller->adding());
        controller->clear();
        QTRY_COMPARE(controller->keys().size(), 0);
        controller->addFiles({encrypted}, false);
        QTRY_VERIFY(prompt() && prompt()->isVisible());
        pending = prompt();
        // 暂不处理 GUI 完成通知，确定填满控制器队列，再检验回复仍可重试。
        for (int i = 0; i < 1024; ++i) controller->refresh();
        QSignalSpy failures(controller, &AgentController::failed);
        answer(QStringLiteral("gui-passphrase"), true);
        QCOMPARE(failures.count(), 1);
        QVERIFY(pending && pending->isVisible());
        QTRY_VERIFY([&] {
            if (!pending || !pending->isVisible()) return true;
            answer(QStringLiteral("gui-passphrase"), true);
            return !pending || !pending->isVisible();
        }());
        QTRY_COMPARE(controller->keys().size(), 1);
        QTRY_VERIFY(!controller->adding());
        controller->clear();
        QTRY_COMPARE(controller->keys().size(), 0);
        controller->addFiles({encrypted}, false);
        QTRY_VERIFY(prompt() && prompt()->isVisible());
        answer(QString(30000, QChar(0x4e2d)), false);
        QTRY_VERIFY(!controller->adding());
        QTRY_VERIFY(!prompt());
    }
    void originalCliControlsGuiAgent()
    {
        const auto binary = qEnvironmentVariable("PAGEANT_TEST_CLI");
        QVERIFY(QFileInfo::exists(binary));
        auto environment = QProcessEnvironment::systemEnvironment();
        environment.insert(QStringLiteral("SSH_AUTH_SOCK"), controller->endpoint());
        environment.insert(QStringLiteral("QT_QPA_PLATFORM"), QStringLiteral("pageant-deliberately-unavailable"));
        environment.remove(QStringLiteral("DISPLAY")); environment.remove(QStringLiteral("WAYLAND_DISPLAY"));
        const auto run = [&](QStringList arguments, int expected) {
            QProcess client; client.setProcessEnvironment(environment); client.start(binary, arguments);
            if (!client.waitForFinished(5000) || client.exitStatus() != QProcess::NormalExit || client.exitCode() != expected)
                return QByteArray("失败：") + client.readAllStandardError();
            return client.readAllStandardOutput();
        };
        QCOMPARE(run({QStringLiteral("-a"), key}, 0), QByteArray());
        QTRY_COMPARE(table()->rowCount(), 1);
        QVERIFY(run({QStringLiteral("-l")}, 0).contains("one"));
        QVERIFY(run({QStringLiteral("-L"), key}, 0).startsWith("ssh-ed25519 "));
        QCOMPARE(run({QStringLiteral("-d"), key}, 0), QByteArray());
        QTRY_COMPARE(table()->rowCount(), 0);
        QCOMPARE(run({QStringLiteral("-a"), QStringLiteral("--encrypted"), encrypted}, 0), QByteArray());
        QTRY_COMPARE(table()->rowCount(), 1);
        QCOMPARE(controller->keys()[0].flags, 3u);
        QCOMPARE(run({QStringLiteral("-D")}, 0), QByteArray());
        QTRY_COMPARE(table()->rowCount(), 0);
    }
};
int main(int argc, char **argv)
{
    if (argc < 2) return 2; // 每进程只运行一个选定场景，保持核心全局状态隔离。
    const auto config = qEnvironmentVariable("PAGEANT_TEST_ROOT") + QStringLiteral("/config");
    QSettings::setDefaultFormat(QSettings::IniFormat);
    QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, config);
    QSettings::setPath(QSettings::IniFormat, QSettings::SystemScope, config);
    QApplication::setAttribute(Qt::AA_DontUseNativeDialogs);
    QApplication app(argc, argv);
    app.setQuitOnLastWindowClosed(false);
    AgentGuiTest test;
    return QTest::qExec(&test, argc, argv);
}
#include "gui_test.moc"
