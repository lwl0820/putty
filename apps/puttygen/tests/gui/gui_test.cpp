#include "app/language_manager.h"
#include "app/session_controller.h"
#include "ui/main_window.h"
#include "ui/ppk_dialog.h"
#include <QAction>
#include <QApplication>
#include <QComboBox>
#include <QClipboard>
#include <QElapsedTimer>
#include <QFileDialog>
#include <QFileInfo>
#include <QPlainTextEdit>
#include <QDialogButtonBox>
#include <QDir>
#include <QFile>
#include <QInputDialog>
#include <QLibraryInfo>
#include <QToolButton>
#include <QLineEdit>
#include <QLabel>
#include <QMessageBox>
#include <QProgressBar>
#include <QProcess>
#include <QStandardPaths>
#include <QTextBrowser>
#include <QPushButton>
#include <QRadioButton>
#include <QSettings>
#include <QSignalSpy>
#include <QTest>
#include <QTimer>

class GuiTest final : public QObject {
    Q_OBJECT
private:
    QString directory_;
    void saveScreenshot(MainWindow &window, const QString &name) {
        const QString suffix = qEnvironmentVariable("QT_SCALE_FACTOR") == QStringLiteral("2") ? QStringLiteral("-2x") : QString();
        QVERIFY(window.grab().save(directory_ + QLatin1Char('/') + name + suffix + QStringLiteral(".png")));
    }
    bool saveThroughDialog(MainWindow &window, const char *buttonName, const QString &path,
                           bool cancel = false, QMessageBox::StandardButton overwrite = QMessageBox::NoButton, int holdMilliseconds = 0) {
        bool fileChosen = false, prepared = false, overwriteAnswered = false, timedOut = false;
        QTimer responder, watchdog;
        QElapsedTimer modalDuration;
        responder.setInterval(10);
        watchdog.setSingleShot(true);
        connect(&watchdog, &QTimer::timeout, this, [&] {
            timedOut = true;
            if (auto *dialog = qobject_cast<QDialog *>(QApplication::activeModalWidget())) dialog->reject();
        });
        connect(&responder, &QTimer::timeout, this, [&] {
            if (auto *dialog = qobject_cast<QFileDialog *>(QApplication::activeModalWidget())) {
                if (fileChosen) return;
                if (!prepared) {
                    dialog->setDirectory(QFileInfo(path).absolutePath());
                    dialog->selectFile(QFileInfo(path).fileName());
                    if (auto *name = dialog->findChild<QLineEdit *>(QStringLiteral("fileNameEdit"))) name->setText(QFileInfo(path).fileName());
                    prepared = true;
                    modalDuration.start();
                    return;
                }
                if (modalDuration.elapsed() < holdMilliseconds) return;
                auto *buttons = dialog->findChild<QDialogButtonBox *>();
                auto *button = buttons ? buttons->button(cancel ? QDialogButtonBox::Cancel : QDialogButtonBox::Save) : nullptr;
                if (!button || !button->isEnabled()) return;
                fileChosen = true;
                QTest::mouseClick(button, Qt::LeftButton);
            } else if (auto *message = qobject_cast<QMessageBox *>(QApplication::activeModalWidget())) {
                if (overwrite == QMessageBox::NoButton || overwriteAnswered) return;
                if (auto *button = message->button(overwrite)) {
                    overwriteAnswered = true;
                    QTest::mouseClick(button, Qt::LeftButton);
                }
            }
        });
        auto *button = window.findChild<QPushButton *>(QString::fromLatin1(buttonName));
        if (!button || !button->isEnabled()) return false;
        responder.start();
        watchdog.start(10000);
        QTest::mouseClick(button, Qt::LeftButton);
        QElapsedTimer deadline;
        deadline.start();
        while (!timedOut && deadline.elapsed() < 10000) {
            if (fileChosen && (overwrite == QMessageBox::NoButton || overwriteAnswered)
                && !window.controller()->busy() && !QApplication::activeModalWidget()) break;
            QTest::qWait(10);
        }
        responder.stop();
        watchdog.stop();
        if (timedOut) qWarning("文件对话框交互超时：已选择=%d，已回答覆盖=%d", fileChosen, overwriteAnswered);
        return !timedOut && fileChosen && (overwrite == QMessageBox::NoButton || overwriteAnswered)
            && !window.controller()->busy() && !QApplication::activeModalWidget();
    }
    static void generateTestKey(SessionController *controller) {
        controller->generate({PG_EDDSA, 255, PG_PRIME_PROBABLE, 0}, QByteArrayLiteral("GUI test-only entropy"));
    }
private slots:
    void initTestCase() {
        QCoreApplication::setAttribute(Qt::AA_DontUseNativeDialogs);
        QCoreApplication::setOrganizationName(QStringLiteral("PuTTYgenGUITest"));
        QCoreApplication::setApplicationName(QStringLiteral("PuTTYgenGUITest"));
        const QString configuration = QStringLiteral(PG_GUI_TEST_ROOT "/config");
        QVERIFY(QDir().mkpath(configuration));
        QSettings::setDefaultFormat(QSettings::IniFormat);
        QSettings::setPath(QSettings::IniFormat, QSettings::UserScope, configuration);
        QSettings::setPath(QSettings::IniFormat, QSettings::SystemScope, configuration);
        directory_ = QStringLiteral(PG_GUI_TEST_ROOT "/files");
        QVERIFY(QDir().mkpath(directory_));
        const QString runtime = qEnvironmentVariable("XDG_RUNTIME_DIR");
        if (!runtime.isEmpty()) QFile::setPermissions(runtime, QFileDevice::ReadOwner | QFileDevice::WriteOwner | QFileDevice::ExeOwner);
    }
    void init() { QSettings().clear(); }
    void defaultsAndActions() {
        LanguageManager language;
        QCOMPARE(language.language(), QStringLiteral("zh_CN"));
        MainWindow window(&language);
        window.show();
        QTRY_VERIFY(!window.controller()->busy());
        auto *generate = window.findChild<QPushButton *>(QStringLiteral("generateButton"));
        QVERIFY(generate->text().contains(QStringLiteral("生成")));
        QVERIFY(generate->isEnabled());
        QVERIFY(!window.windowIcon().isNull());
        QVERIFY(!window.windowIcon().pixmap(32, 32).isNull());
        QVERIFY(!window.findChild<QAction *>(QStringLiteral("savePrivate"))->isEnabled());
        QVERIFY(!window.findChild<QAction *>(QStringLiteral("addCertificate"))->isEnabled());
        QVERIFY(window.findChild<QRadioButton *>(QStringLiteral("algorithmRadio0"))->isChecked());
        for (int algorithm = 0; algorithm < 5; ++algorithm) {
            window.findChild<QAction *>(QStringLiteral("algorithm%1").arg(algorithm))->trigger();
            QVERIFY(window.findChild<QRadioButton *>(QStringLiteral("algorithmRadio%1").arg(algorithm))->isChecked());
            QVERIFY(window.findChild<QAction *>(QStringLiteral("algorithm%1").arg(algorithm))->isChecked());
            QCOMPARE(window.findChild<QComboBox *>(QStringLiteral("nistCurve"))->isVisible(), algorithm == PG_ECDSA);
            QCOMPARE(window.findChild<QComboBox *>(QStringLiteral("edCurve"))->isVisible(), algorithm == PG_EDDSA);
        }
        window.findChild<QAction *>(QStringLiteral("algorithm0"))->trigger();
        saveScreenshot(window, QStringLiteral("main-zh-initial"));
        language.setLanguage(QStringLiteral("en"));
        QVERIFY(generate->text().contains(QStringLiteral("Generate")));
        saveScreenshot(window, QStringLiteral("main-en-initial"));
    }
    void translatedStandardButtonsAndSaveGuards() {
        LanguageManager language;
        QDialogButtonBox buttons(QDialogButtonBox::Ok | QDialogButtonBox::Cancel | QDialogButtonBox::Close | QDialogButtonBox::Yes | QDialogButtonBox::No);
        QVERIFY(buttons.button(QDialogButtonBox::Ok)->text().contains(QStringLiteral("确定")));
        QVERIFY(buttons.button(QDialogButtonBox::Cancel)->text().contains(QStringLiteral("取消")));
        QVERIFY(buttons.button(QDialogButtonBox::Close)->text().contains(QStringLiteral("关闭")));
        QVERIFY(buttons.button(QDialogButtonBox::Yes)->text().contains(QStringLiteral("是")));
        QVERIFY(buttons.button(QDialogButtonBox::No)->text().contains(QStringLiteral("否")));
        MainWindow window(&language);
        window.show();
        QTRY_VERIFY(!window.controller()->busy());
        generateTestKey(window.controller());
        QTRY_VERIFY_WITH_TIMEOUT(!window.controller()->busy(), 10000);
        auto *pass = window.findChild<QLineEdit *>(QStringLiteral("passphrase"));
        auto *confirm = window.findChild<QLineEdit *>(QStringLiteral("confirmPassphrase"));
        pass->setText(QStringLiteral("test-only"));
        confirm->setText(QStringLiteral("mismatch"));
        QString observed;
        QTimer::singleShot(0, this, [&] {
            auto *message = qobject_cast<QMessageBox *>(QApplication::activeModalWidget());
            if (message) { observed = message->text(); message->accept(); }
        });
        window.findChild<QAction *>(QStringLiteral("savePrivate"))->trigger();
        QVERIFY(observed.contains(QStringLiteral("不一致")));
        QVERIFY(!window.controller()->busy());
        pass->clear(); confirm->clear();
        QTimer::singleShot(0, this, [&] {
            auto *message = qobject_cast<QMessageBox *>(QApplication::activeModalWidget());
            if (message) {
                observed = message->text();
                message->done(QMessageBox::No);
            }
        });
        window.findChild<QAction *>(QStringLiteral("savePrivate"))->trigger();
        QVERIFY(observed.contains(QStringLiteral("口令")));
        QVERIFY(!window.controller()->busy());
        QVERIFY(window.controller()->snapshot().exists);
    }
    void invalidBitsAndShortKeyCancellation() {
        LanguageManager language;
        MainWindow window(&language);
        window.show();
        QTRY_VERIFY(!window.controller()->busy());
        auto *bits = window.findChild<QLineEdit *>(QStringLiteral("bits"));
        bits->setText(QStringLiteral("128"));
        QTimer::singleShot(0, this, [] {
            if (auto *message = qobject_cast<QMessageBox *>(QApplication::activeModalWidget())) message->done(QMessageBox::Cancel);
        });
        window.findChild<QAction *>(QStringLiteral("generate"))->trigger();
        QVERIFY(!window.collectingEntropy());
        QVERIFY(!window.controller()->busy());
        QCOMPARE(bits->text(), QStringLiteral("128"));
        bits->setText(QStringLiteral("invalid"));
        window.findChild<QAction *>(QStringLiteral("generate"))->trigger();
        QVERIFY(window.collectingEntropy());
        QCOMPARE(window.findChild<QProgressBar *>(QStringLiteral("generationProgress"))->maximum(), 912);
        window.close();
    }
    void languagePreservesEditsAndLoadedKey() {
        LanguageManager language;
        MainWindow window(&language);
        window.show();
        QTRY_VERIFY(!window.controller()->busy());
        generateTestKey(window.controller());
        QTRY_VERIFY_WITH_TIMEOUT(!window.controller()->busy(), 10000);
        QVERIFY(window.controller()->snapshot().exists);
        auto *comment = window.findChild<QLineEdit *>(QStringLiteral("comment"));
        auto *pass = window.findChild<QLineEdit *>(QStringLiteral("passphrase"));
        auto *confirm = window.findChild<QLineEdit *>(QStringLiteral("confirmPassphrase"));
        comment->setText(QStringLiteral("尚未保存的注释"));
        pass->setText(QStringLiteral("test-only passphrase"));
        confirm->setText(QStringLiteral("different unsubmitted value"));
        const auto fingerprint = window.controller()->snapshot().sha256;
        language.setLanguage(QStringLiteral("en"));
        QCOMPARE(comment->text(), QStringLiteral("尚未保存的注释"));
        QCOMPARE(pass->text(), QStringLiteral("test-only passphrase"));
        QCOMPARE(confirm->text(), QStringLiteral("different unsubmitted value"));
        QCOMPARE(window.controller()->snapshot().sha256, fingerprint);
        language.setLanguage(QStringLiteral("zh_CN"));
        QVERIFY(window.findChild<QAction *>(QStringLiteral("savePrivate"))->isEnabled());
        QVERIFY(window.findChild<QAction *>(QStringLiteral("exportOpenSshNew"))->isEnabled());
        QVERIFY(window.findChild<QAction *>(QStringLiteral("addCertificate"))->isEnabled());
        QVERIFY(!window.findChild<QAction *>(QStringLiteral("removeCertificate"))->isEnabled());
        window.findChild<QAction *>(QStringLiteral("fingerprintMd5"))->trigger();
        QCOMPARE(window.findChild<QLineEdit *>(QStringLiteral("fingerprint"))->text(), window.controller()->snapshot().md5);
        window.findChild<QAction *>(QStringLiteral("fingerprintSha256"))->trigger();
        QCOMPARE(window.findChild<QLineEdit *>(QStringLiteral("fingerprint"))->text(), fingerprint);
        // 截图只保留测试公钥；口令控件隐藏明文。
        saveScreenshot(window, QStringLiteral("main-zh-key"));
        language.setLanguage(QStringLiteral("en"));
        QCoreApplication::processEvents();
        for (const char *name : {"fingerprintLabel", "commentLabel", "passphraseLabel", "confirmPassphraseLabel"}) {
            auto *label = window.findChild<QLabel *>(QString::fromLatin1(name));
            QVERIFY(label);
            QVERIFY(label->width() >= label->sizeHint().width());
        }
        saveScreenshot(window, QStringLiteral("main-en-key"));
        LanguageManager restarted;
        QCOMPARE(restarted.language(), QStringLiteral("en"));
    }
    void entropyRequiresMouseAndRateLimit() {
        LanguageManager language;
        MainWindow window(&language);
        window.show();
        QTRY_VERIFY(!window.controller()->busy());
        window.findChild<QAction *>(QStringLiteral("algorithm3"))->trigger();
        window.findChild<QAction *>(QStringLiteral("generate"))->trigger();
        QVERIFY(window.collectingEntropy());
        auto *progress = window.findChild<QProgressBar *>(QStringLiteral("generationProgress"));
        QCOMPARE(progress->value(), 0);
        QTest::qWait(50);
        QCOMPARE(progress->value(), 0);
        QVERIFY(!window.findChild<QAction *>(QStringLiteral("load"))->isEnabled());
        for (int i = 0; i < 140 && window.collectingEntropy(); ++i) {
            QTest::qWait(12);
            QTest::mouseMove(window.centralWidget(), QPoint(20 + i % 100, 20 + i % 20));
        }
        QTRY_VERIFY_WITH_TIMEOUT(!window.collectingEntropy() && !window.controller()->busy(), 10000);
        QVERIFY(window.controller()->snapshot().exists);
        QCOMPARE(window.controller()->snapshot().algorithm, PG_EDDSA);
    }
    void ppkParametersAndCancellation() {
        SessionController validator;
        QTRY_VERIFY(!validator.busy());
        const pg_ppk_params original{3, PG_ARGON2_ID, 8192, 1, 100, 1};
        PpkDialog cancelled(original, &validator);
        cancelled.findChild<QLineEdit *>(QStringLiteral("argonMemory"))->setText(QStringLiteral("16384"));
        cancelled.reject();
        QCOMPARE(cancelled.parameters().memory_kib, original.memory_kib);
        PpkDialog dialog(original, &validator);
        dialog.show();
        dialog.findChild<QComboBox *>(QStringLiteral("argon2"))->setCurrentIndex(2);
        dialog.findChild<QComboBox *>(QStringLiteral("argonMode"))->setCurrentIndex(1);
        QCOMPARE(dialog.findChild<QLineEdit *>(QStringLiteral("argonTime"))->text(), QStringLiteral("13"));
        dialog.findChild<QComboBox *>(QStringLiteral("argonMode"))->setCurrentIndex(0);
        QCOMPARE(dialog.findChild<QLineEdit *>(QStringLiteral("argonTime"))->text(), QStringLiteral("100"));
        dialog.findChild<QComboBox *>(QStringLiteral("argonMode"))->setCurrentIndex(1);
        dialog.findChild<QLineEdit *>(QStringLiteral("argonMemory"))->setText(QStringLiteral("16384"));
        dialog.findChild<QLineEdit *>(QStringLiteral("argonTime"))->setText(QStringLiteral("5"));
        dialog.findChild<QLineEdit *>(QStringLiteral("argonParallelism"))->setText(QStringLiteral("2"));
        auto *buttons = dialog.findChild<QDialogButtonBox *>();
        QTest::mouseClick(buttons->button(QDialogButtonBox::Ok), Qt::LeftButton);
        QTRY_COMPARE(dialog.result(), int(QDialog::Accepted));
        QCOMPARE(dialog.parameters().memory_kib, 16384u);
        QCOMPARE(dialog.parameters().argon2, PG_ARGON2_D);
        QCOMPARE(dialog.parameters().passes_auto, 0);
        QCOMPARE(dialog.parameters().passes_or_ms, 5u);
        QCOMPARE(dialog.parameters().parallelism, 2u);
        PpkDialog v2(original, &validator);
        v2.findChild<QComboBox *>(QStringLiteral("ppkVersion"))->setCurrentIndex(0);
        QVERIFY(!v2.findChild<QLineEdit *>(QStringLiteral("argonMemory"))->isEnabled());
        PpkDialog invalid(original, &validator);
        invalid.show();
        invalid.findChild<QLineEdit *>(QStringLiteral("argonMemory"))->setText(QStringLiteral("1"));
        QTimer dismiss;
        dismiss.setInterval(10);
        int warnings = 0;
        connect(&dismiss, &QTimer::timeout, this, [&] {
            if (auto *message = qobject_cast<QMessageBox *>(QApplication::activeModalWidget())) { ++warnings; message->accept(); }
        });
        dismiss.start();
        invalid.findChild<QDialogButtonBox *>()->button(QDialogButtonBox::Ok)->click();
        QTRY_VERIFY(warnings > 0 && !validator.busy());
        QVERIFY(invalid.isVisible());
        QCOMPARE(invalid.parameters().memory_kib, original.memory_kib);
        invalid.findChild<QLineEdit *>(QStringLiteral("argonMemory"))->setText(QStringLiteral("8192"));
        invalid.findChild<QLineEdit *>(QStringLiteral("argonParallelism"))->setText(QStringLiteral("2000"));
        invalid.findChild<QDialogButtonBox *>()->button(QDialogButtonBox::Ok)->click();
        QTRY_VERIFY(warnings > 1 && !validator.busy());
        QVERIFY(invalid.isVisible());
        QCOMPARE(invalid.parameters().parallelism, original.parallelism);
        invalid.reject();
        dismiss.stop();
    }
    void encryptedLoadRetriesAndPreservesFailedKey() {
        LanguageManager language;
        MainWindow window(&language);
        window.show();
        auto *controller = window.controller();
        QTRY_VERIFY(!controller->busy());
        generateTestKey(controller);
        QTRY_VERIFY_WITH_TIMEOUT(!controller->busy(), 10000);
        const QString fingerprint = controller->snapshot().sha256;
        const QString path = directory_ + QStringLiteral("/中文 空格 测试.ppk");
        controller->save(path, PG_SAVE_PRIVATE, QByteArrayLiteral("test-only"), QStringLiteral("测试密钥"), {3, PG_ARGON2_ID, 8192, 0, 1, 1});
        QTRY_VERIFY_WITH_TIMEOUT(!controller->busy(), 10000);
        QVERIFY(QFile::exists(path));
        int prompts = 0;
        QTimer response;
        response.setInterval(10);
        connect(&response, &QTimer::timeout, this, [&] {
            if (auto *dialog = qobject_cast<QInputDialog *>(QApplication::activeModalWidget())) {
                ++prompts;
                dialog->setTextValue(prompts == 1 ? QStringLiteral("incorrect") : QStringLiteral("test-only"));
                dialog->accept();
            }
        });
        response.start();
        window.loadPath(path, true);
        QTRY_VERIFY_WITH_TIMEOUT(prompts == 2 && !controller->busy(), 10000);
        response.stop();
        QCOMPARE(controller->snapshot().sha256, fingerprint);
        QCOMPARE(window.findChild<QLineEdit *>(QStringLiteral("passphrase"))->text(), QStringLiteral("test-only"));
        QFile invalid(directory_ + QStringLiteral("/invalid.ppk"));
        QVERIFY(invalid.open(QIODevice::WriteOnly));
        invalid.write("invalid input"); invalid.close();
        QTimer dismiss;
        dismiss.setInterval(10);
        connect(&dismiss, &QTimer::timeout, this, [] {
            if (auto *message = qobject_cast<QMessageBox *>(QApplication::activeModalWidget())) message->accept();
        });
        dismiss.start();
        QSignalSpy finished(controller, &SessionController::completed);
        window.loadPath(invalid.fileName(), true);
        QTRY_VERIFY_WITH_TIMEOUT(finished.count() > 0 && !controller->busy(), 10000);
        QCOMPARE(controller->snapshot().sha256, fingerprint);
        dismiss.stop();
    }
    void certificateControlsAndInformation() {
        const QString ssh = QStandardPaths::findExecutable(QStringLiteral("ssh-keygen"));
        if (ssh.isEmpty()) QSKIP("当前环境没有证书签名对照工具 ssh-keygen");
        LanguageManager language;
        MainWindow window(&language);
        window.show();
        auto *controller = window.controller();
        QTRY_VERIFY(!controller->busy());
        generateTestKey(controller);
        QTRY_VERIFY_WITH_TIMEOUT(!controller->busy(), 10000);
        const QString ca = directory_ + QStringLiteral("/gui-certificate-ca");
        QFile::remove(ca); QFile::remove(ca + QStringLiteral(".pub"));
        QProcess signer;
        signer.start(ssh, {QStringLiteral("-q"), QStringLiteral("-t"), QStringLiteral("ed25519"), QStringLiteral("-N"), QString(), QStringLiteral("-f"), ca});
        QVERIFY(signer.waitForFinished(10000));
        QCOMPARE(signer.exitCode(), 0);
        const QString subject = directory_ + QStringLiteral("/gui-subject.pub");
        QFile publicKey(subject);
        QVERIFY(publicKey.open(QIODevice::WriteOnly));
        QVERIFY(publicKey.write(controller->snapshot().publicText.toUtf8()) > 0);
        publicKey.close();
        signer.start(ssh, {QStringLiteral("-q"), QStringLiteral("-s"), ca, QStringLiteral("-I"), QStringLiteral("gui-test-only"),
                          QStringLiteral("-n"), QStringLiteral("test-user"), QStringLiteral("-V"), QStringLiteral("+1h"), subject});
        QVERIFY(signer.waitForFinished(10000));
        QCOMPARE(signer.exitCode(), 0);
        const QString cert = directory_ + QStringLiteral("/gui-subject-cert.pub");
        QVERIFY(QFile::exists(cert));
        controller->addCertificate(cert, QStringLiteral("certificate test"));
        QTRY_VERIFY_WITH_TIMEOUT(!controller->busy(), 10000);
        QVERIFY(controller->snapshot().certificate);
        QVERIFY(window.findChild<QAction *>(QStringLiteral("removeCertificate"))->isEnabled());
        auto *infoButton = window.findChild<QPushButton *>(QStringLiteral("certificateInfo"));
        QVERIFY(infoButton->isVisible());
        QVERIFY(!window.findChild<QWidget *>(QStringLiteral("publicKey"))->isVisible());
        QString information;
        QTimer::singleShot(0, this, [&] {
            if (auto *dialog = qobject_cast<QDialog *>(QApplication::activeModalWidget())) {
                if (auto *browser = dialog->findChild<QTextBrowser *>()) information = browser->toPlainText();
                dialog->reject();
            }
        });
        infoButton->click();
        QVERIFY(information.contains(QStringLiteral("gui-test-only")));
        window.findChild<QAction *>(QStringLiteral("removeCertificate"))->trigger();
        QTRY_VERIFY_WITH_TIMEOUT(!controller->busy(), 10000);
        QVERIFY(!controller->snapshot().certificate);
        QVERIFY(!window.findChild<QAction *>(QStringLiteral("removeCertificate"))->isEnabled());
        QVERIFY(window.findChild<QWidget *>(QStringLiteral("publicKey"))->isVisible());
        controller->generate({PG_EDDSA, 448, PG_PRIME_PROBABLE, 0}, QByteArrayLiteral("GUI test-only entropy"));
        QTRY_VERIFY_WITH_TIMEOUT(!controller->busy(), 10000);
        QVERIFY(!window.findChild<QAction *>(QStringLiteral("addCertificate"))->isEnabled());
        QFile::remove(ca); QFile::remove(ca + QStringLiteral(".pub"));
    }
    void fileDialogSavingOverwriteAndCancellation() {
        LanguageManager language;
        MainWindow window(&language);
        window.show();
        auto *controller = window.controller();
        QTRY_VERIFY(!controller->busy());
        generateTestKey(controller);
        QTRY_VERIFY_WITH_TIMEOUT(!controller->busy(), 10000);
        const QString fingerprint = controller->snapshot().sha256;
        const QString comment = QStringLiteral("文件对话框 测试注释");
        window.findChild<QLineEdit *>(QStringLiteral("comment"))->setText(comment);
        window.findChild<QLineEdit *>(QStringLiteral("passphrase"))->setText(QStringLiteral("test-only-dialog-passphrase"));
        window.findChild<QLineEdit *>(QStringLiteral("confirmPassphrase"))->setText(QStringLiteral("test-only-dialog-passphrase"));
        const QString privatePath = directory_ + QStringLiteral("/文件对话框 私钥.ppk");
        const QString publicPath = directory_ + QStringLiteral("/文件对话框 公钥.pub");
        const QString cancelledPath = directory_ + QStringLiteral("/取消 不创建.ppk");
        QFile::remove(privatePath); QFile::remove(publicPath); QFile::remove(cancelledPath);
        const auto read = [](const QString &path) {
            QFile file(path);
            return file.open(QIODevice::ReadOnly) ? file.readAll() : QByteArray();
        };
        const auto replace = [](const QString &path, const QByteArray &contents) {
            QFile file(path);
            return file.open(QIODevice::WriteOnly) && file.write(contents) == contents.size();
        };
        QVERIFY(saveThroughDialog(window, "savePrivateButton", privatePath));
        QByteArray savedPrivate = read(privatePath);
        QVERIFY(savedPrivate.startsWith("PuTTY-User-Key-File-3: ssh-ed25519"));
        QVERIFY(savedPrivate.contains("Encryption: aes256-cbc"));
        QVERIFY(savedPrivate.contains(comment.toUtf8()));
        QVERIFY(saveThroughDialog(window, "savePublicButton", publicPath));
        const QByteArray savedPublic = read(publicPath);
        QVERIFY(savedPublic.startsWith("---- BEGIN SSH2 PUBLIC KEY ----"));
        QVERIFY(savedPublic.contains(comment.toUtf8()));
        const QByteArray privateMarker("existing-private-file-marker\n");
        const QByteArray publicMarker("existing-public-file-marker\n");
        QVERIFY(replace(privatePath, privateMarker));
        QVERIFY(saveThroughDialog(window, "savePrivateButton", privatePath, false, QMessageBox::No));
        QVERIFY(read(privatePath) == privateMarker);
        QVERIFY(saveThroughDialog(window, "savePrivateButton", privatePath, false, QMessageBox::Yes));
        savedPrivate = read(privatePath);
        QVERIFY(savedPrivate.startsWith("PuTTY-User-Key-File-3: ssh-ed25519"));
        QVERIFY(savedPrivate != privateMarker);
        QVERIFY(replace(publicPath, publicMarker));
        QVERIFY(saveThroughDialog(window, "savePublicButton", publicPath, false, QMessageBox::No));
        QVERIFY(read(publicPath) == publicMarker);
        QVERIFY(saveThroughDialog(window, "savePublicButton", publicPath, false, QMessageBox::Yes));
        QVERIFY(read(publicPath) == savedPublic);
        QVERIFY(saveThroughDialog(window, "savePrivateButton", privatePath, true));
        QVERIFY(read(privatePath) == savedPrivate);
        QVERIFY(saveThroughDialog(window, "savePublicButton", publicPath, true));
        QVERIFY(read(publicPath) == savedPublic);
        QVERIFY(saveThroughDialog(window, "savePrivateButton", cancelledPath, true));
        QVERIFY(!QFile::exists(cancelledPath));
        QCOMPARE(controller->snapshot().sha256, fingerprint);
        auto *commentEdit = window.findChild<QLineEdit *>(QStringLiteral("comment"));
        commentEdit->selectAll();
        QTest::keyClicks(commentEdit, "pending-comment-save-cancel");
        const QString pending = commentEdit->text();
        QSignalSpy duringDialog(controller, &SessionController::completed);
        QVERIFY(saveThroughDialog(window, "savePublicButton", publicPath, true, QMessageBox::NoButton, 400));
        QCOMPARE(duringDialog.count(), 0);
        QVERIFY(read(publicPath) == savedPublic);
        QTRY_COMPARE_WITH_TIMEOUT(controller->snapshot().comment, pending, 2000);
        QVERIFY(controller->snapshot().publicText.contains(pending));
        QCOMPARE(controller->snapshot().sha256, fingerprint);
        QSignalSpy loaded(controller, &SessionController::completed);
        controller->load(privatePath, QByteArrayLiteral("test-only-dialog-passphrase"));
        QTRY_VERIFY_WITH_TIMEOUT(!controller->busy(), 10000);
        QVERIFY(!loaded.isEmpty());
        QCOMPARE(loaded.last().at(0).toInt(), int(SessionController::Load));
        QCOMPARE(loaded.last().at(1).toInt(), int(PG_OK));
        QCOMPARE(controller->snapshot().sha256, fingerprint);
        QCOMPARE(controller->snapshot().comment, comment);
    }
    void clipboardCopyAndLanguageSwitch() {
        LanguageManager language;
        MainWindow window(&language);
        window.show();
        QTRY_VERIFY(!window.controller()->busy());
        generateTestKey(window.controller());
        QTRY_VERIFY_WITH_TIMEOUT(!window.controller()->busy(), 10000);
        auto *publicKey = window.findChild<QPlainTextEdit *>(QStringLiteral("publicKey"));
        QVERIFY(publicKey);
        QVERIFY(publicKey->isVisible());
        const QString displayed = publicKey->toPlainText();
        QVERIFY(displayed.startsWith(QStringLiteral("ssh-ed25519 ")));
        window.activateWindow();
        publicKey->setFocus();
        QApplication::clipboard()->clear();
        QTest::keyClick(publicKey, Qt::Key_A, Qt::ControlModifier);
        QTest::keyClick(publicKey, Qt::Key_C, Qt::ControlModifier);
        QTRY_COMPARE(QApplication::clipboard()->text(), displayed);
        language.setLanguage(QStringLiteral("en"));
        QCOMPARE(publicKey->toPlainText(), displayed);
        QCOMPARE(QApplication::clipboard()->text(), displayed);
        QApplication::clipboard()->setText(QStringLiteral("test-only clipboard marker"));
        publicKey->setFocus();
        QTest::keyClick(publicKey, Qt::Key_A, Qt::ControlModifier);
        QTest::keyClick(publicKey, Qt::Key_C, Qt::ControlModifier);
        QTRY_COMPARE(QApplication::clipboard()->text(), displayed);
        language.setLanguage(QStringLiteral("zh_CN"));
        QCOMPARE(publicKey->toPlainText(), displayed);
        QCOMPARE(QApplication::clipboard()->text(), displayed);
        QApplication::clipboard()->clear();
    }
    void commentResumesAfterFailedLoad() {
        LanguageManager language;
        MainWindow window(&language);
        window.show();
        auto *controller = window.controller();
        QTRY_VERIFY(!controller->busy());
        generateTestKey(controller);
        QTRY_VERIFY_WITH_TIMEOUT(!controller->busy(), 10000);
        const QString fingerprint = controller->snapshot().sha256;
        auto *comment = window.findChild<QLineEdit *>(QStringLiteral("comment"));
        comment->selectAll();
        QTest::keyClicks(comment, "pending-comment-invalid-load");
        const QString pending = comment->text();
        QFile invalid(directory_ + QStringLiteral("/comment-invalid.ppk"));
        QVERIFY(invalid.open(QIODevice::WriteOnly));
        invalid.write("invalid input"); invalid.close();
        QTimer dismiss;
        dismiss.setInterval(10);
        int warnings = 0;
        connect(&dismiss, &QTimer::timeout, this, [&] {
            if (auto *message = qobject_cast<QMessageBox *>(QApplication::activeModalWidget())) { ++warnings; message->accept(); }
        });
        dismiss.start();
        window.loadPath(invalid.fileName(), true);
        QTRY_VERIFY_WITH_TIMEOUT(warnings > 0 && !controller->busy(), 10000);
        dismiss.stop();
        QTRY_COMPARE_WITH_TIMEOUT(controller->snapshot().comment, pending, 2000);
        QVERIFY(controller->snapshot().publicText.contains(pending));
        QCOMPARE(controller->snapshot().sha256, fingerprint);
    }
    void commentResumesAfterWrongPassphraseCancellation() {
        LanguageManager language;
        MainWindow window(&language);
        window.show();
        auto *controller = window.controller();
        QTRY_VERIFY(!controller->busy());
        generateTestKey(controller);
        QTRY_VERIFY_WITH_TIMEOUT(!controller->busy(), 10000);
        const QString fingerprint = controller->snapshot().sha256;
        const QString path = directory_ + QStringLiteral("/comment-encrypted.ppk");
        controller->save(path, PG_SAVE_PRIVATE, QByteArrayLiteral("test-only"), controller->snapshot().comment, {3, PG_ARGON2_ID, 8192, 0, 1, 1});
        QTRY_VERIFY_WITH_TIMEOUT(!controller->busy(), 10000);
        auto *comment = window.findChild<QLineEdit *>(QStringLiteral("comment"));
        comment->selectAll();
        QTest::keyClicks(comment, "pending-comment-cancelled-load");
        const QString pending = comment->text();
        QTimer answer;
        answer.setInterval(10);
        int prompts = 0;
        connect(&answer, &QTimer::timeout, this, [&] {
            if (auto *dialog = qobject_cast<QInputDialog *>(QApplication::activeModalWidget())) {
                ++prompts;
                if (prompts == 1) { dialog->setTextValue(QStringLiteral("incorrect")); dialog->accept(); }
                else dialog->reject();
            }
        });
        answer.start();
        window.loadPath(path, true);
        QTRY_VERIFY_WITH_TIMEOUT(prompts == 2 && !controller->busy(), 10000);
        answer.stop();
        QTRY_COMPARE_WITH_TIMEOUT(controller->snapshot().comment, pending, 2000);
        QVERIFY(controller->snapshot().publicText.contains(pending));
        QCOMPARE(controller->snapshot().sha256, fingerprint);
    }
    void qtFileDialogTranslations() {
        LanguageManager language;
        QTranslator available;
        const bool officialTranslations = available.load(QStringLiteral("qtbase_zh_CN"), QLibraryInfo::path(QLibraryInfo::TranslationsPath));
        QFileDialog dialog;
        dialog.setOption(QFileDialog::DontUseNativeDialog);
        dialog.setAcceptMode(QFileDialog::AcceptSave);
        dialog.show();
        QCoreApplication::processEvents();
        auto *newFolder = dialog.findChild<QToolButton *>(QStringLiteral("newFolderButton"));
        QVERIFY(newFolder);
        if (officialTranslations) {
            QVERIFY(newFolder->toolTip().contains(QStringLiteral("文件夹")));
            QVERIFY(!newFolder->toolTip().contains(QStringLiteral("Create New Folder")));
        } else {
            auto *buttons = dialog.findChild<QDialogButtonBox *>();
            QVERIFY(buttons);
            QVERIFY(buttons->button(QDialogButtonBox::Save)->text().contains(QStringLiteral("保存")));
        }
        language.setLanguage(QStringLiteral("en"));
        QCoreApplication::processEvents();
        QCOMPARE(newFolder->toolTip(), QStringLiteral("Create New Folder"));
        language.setLanguage(QStringLiteral("zh_CN"));
        QCoreApplication::processEvents();
        if (officialTranslations) QVERIFY(newFolder->toolTip().contains(QStringLiteral("文件夹")));
        dialog.reject();
    }
    void closeWaitsWithoutDestroyingWorker() {
        LanguageManager language;
        MainWindow window(&language);
        window.show();
        QTRY_VERIFY(!window.controller()->busy());
        generateTestKey(window.controller());
        QVERIFY(window.controller()->busy());
        QVERIFY(!window.close());
        QVERIFY(window.isVisible());
        QTRY_VERIFY_WITH_TIMEOUT(!window.isVisible(), 10000);
        QVERIFY(!window.controller()->busy());
    }
};
QTEST_MAIN(GuiTest)
#include "gui_test.moc"
