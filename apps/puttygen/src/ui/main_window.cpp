#include "main_window.h"
#include "ppk_dialog.h"
#include <QAction>
#include <QActionGroup>
#include <QApplication>
#include <QButtonGroup>
#include <QClipboard>
#include <QCloseEvent>
#include <QComboBox>
#include <QDesktopServices>
#include <QDialog>
#include <QDialogButtonBox>
#include <QFile>
#include <QFileDialog>
#include <QFileInfo>
#include <QFontDatabase>
#include <QFormLayout>
#include <QGridLayout>
#include <QGroupBox>
#include <QInputDialog>
#include <QIcon>
#include <QLabel>
#include <QLineEdit>
#include <QMenuBar>
#include <QMessageBox>
#include <QMouseEvent>
#include <QPlainTextEdit>
#include <QProgressBar>
#include <QPushButton>
#include <QRadioButton>
#include <QStackedWidget>
#include <QStatusBar>
#include <QTextBrowser>
#include <QUrl>
#include <QVBoxLayout>
#include <algorithm>
#include <limits>

MainWindow::MainWindow(LanguageManager *languages, QWidget *parent) : QMainWindow(parent), languages_(languages), session_(this) {
    setObjectName(QStringLiteral("mainWindow"));
    setWindowTitle(QStringLiteral("PuTTYgen"));
    setWindowIcon(QIcon(QStringLiteral(":/icons/puttygen-gui.png")));
    buildMenus();
    buildUi();
    commentTimer_.setSingleShot(true);
    commentTimer_.setInterval(300);
    connect(&commentTimer_, &QTimer::timeout, this, [this] {
        if (!session_.busy() && !collecting_ && session_.snapshot().exists && session_.snapshot().comment != comment_->text())
            session_.setComment(comment_->text());
    });
    connect(comment_, &QLineEdit::textEdited, &commentTimer_, qOverload<>(&QTimer::start));
    connect(&session_, &SessionController::busyChanged, this, [this](bool busy) {
        if (busy) commentTimer_.stop();
        updateState();
    });
    connect(&session_, &SessionController::snapshotChanged, this, [this] {
        const auto &s = session_.snapshot();
        publicKey_->setPlainText(s.publicText);
        fingerprint_->setText(fingerprintMd5_ ? s.md5 : s.sha256);
        certificateLabel_->setText(s.certificateInfo.left(450));
        updateState();
    });
    connect(&session_, &SessionController::defaultsReady, this, [this](pg_ppk_params params, unsigned capacity) {
        ppk_ = params; entropyCapacity_ = capacity;
    });
    connect(&session_, &SessionController::probed, this, [this](bool encrypted, bool foreign, bool obsolete, const QString &comment, const QString &format) {
        encrypted_ = encrypted; foreign_ = foreign; obsolete_ = obsolete; fileComment_ = comment; fileFormat_ = format;
    });
    connect(&session_, &SessionController::completed, this, &MainWindow::operationCompleted);
    connect(&session_, &SessionController::progress, this, [this](unsigned completed, unsigned total) {
        if (total == 0) progress_->setRange(0, 0);
        else {
            progress_->setRange(0, 1000);
            progress_->setValue(int(std::min(1000.0, 1000.0 * completed / total)));
        }
    });
    connect(languages_, &LanguageManager::languageChanged, this, &MainWindow::retranslate);
    qApp->installEventFilter(this);
    retranslate();
    updateAlgorithm(PG_RSA);
    updateState();
    resize(660, 600);
}
QAction *MainWindow::makeAction(const char *name, QMenu *menu, const char *text, const std::function<void()> &handler) {
    auto *action = menu->addAction(QString::fromUtf8(text));
    action->setObjectName(QString::fromLatin1(name));
    action->setProperty("sourceText", QString::fromUtf8(text));
    connect(action, &QAction::triggered, this, handler);
    return action;
}
void MainWindow::buildMenus() {
    fileMenu_ = menuBar()->addMenu(QString());
    keyMenu_ = menuBar()->addMenu(QString());
    conversionMenu_ = menuBar()->addMenu(QString());
    languageMenu_ = menuBar()->addMenu(QString());
    helpMenu_ = menuBar()->addMenu(QString());
    load_ = makeAction("load", fileMenu_, "&Load private key...", [this] { beginLoad(false); });
    load_->setShortcut(QKeySequence::Open);
    savePublic_ = makeAction("savePublic", fileMenu_, "Save p&ublic key...", [this] { requestSave(PG_SAVE_PUBLIC); });
    savePrivate_ = makeAction("savePrivate", fileMenu_, "&Save private key...", [this] { requestSave(PG_SAVE_PRIVATE); });
    savePrivate_->setShortcut(QKeySequence::Save);
    fileMenu_->addSeparator();
    auto *quit = makeAction("quit", fileMenu_, "E&xit", [this] { close(); });
    quit->setShortcut(QKeySequence::Quit);
    generate_ = makeAction("generate", keyMenu_, "&Generate key pair", [this] { beginGenerate(); });
    addCertificate_ = makeAction("addCertificate", keyMenu_, "Add &certificate...", [this] {
        commentTimer_.stop();
        const QString path = QFileDialog::getOpenFileName(this, tr("Load certificate"), {}, tr("All files (*)"));
        if (!path.isEmpty()) session_.addCertificate(path, comment_->text());
        else resumeCommentUpdate();
    });
    removeCertificate_ = makeAction("removeCertificate", keyMenu_, "&Remove certificate", [this] { session_.removeCertificate(comment_->text()); });
    keyMenu_->addSeparator();
    auto *algorithmActions = new QActionGroup(this);
    const char *names[] = {"RSA", "DSA", "ECDSA", "EdDSA", "SSH-1 (RSA)"};
    for (int i = 0; i < 5; ++i) {
        algorithmActions_[size_t(i)] = makeAction(qPrintable(QStringLiteral("algorithm%1").arg(i)), keyMenu_, names[i], [this, i] { updateAlgorithm(i); });
        algorithmActions_[size_t(i)]->setCheckable(true);
        algorithmActions->addAction(algorithmActions_[size_t(i)]);
    }
    keyMenu_->addSeparator();
    primeMenu_ = keyMenu_->addMenu(QString());
    auto *primeGroup = new QActionGroup(this);
    const char *primes[] = {"&Probable primes", "P&roven primes", "Proven primes, &even distribution"};
    for (int i = 0; i < 3; ++i) {
        primeActions_[size_t(i)] = makeAction(qPrintable(QStringLiteral("prime%1").arg(i)), primeMenu_, primes[i], [] {});
        primeActions_[size_t(i)]->setCheckable(true);
        primeGroup->addAction(primeActions_[size_t(i)]);
    }
    primeActions_[0]->setChecked(true);
    strongRsa_ = makeAction("strongRsa", keyMenu_, "Use &strong primes for RSA", [] {});
    strongRsa_->setCheckable(true);
    ppkAction_ = makeAction("ppkParameters", keyMenu_, "&Parameters for saving key files...", [this] {
        commentTimer_.stop();
        PpkDialog dialog(ppk_, &session_, this);
        if (dialog.exec() == QDialog::Accepted) ppk_ = dialog.parameters();
        resumeCommentUpdate();
    });
    fingerprintMenu_ = keyMenu_->addMenu(QString());
    auto *fingerprintGroup = new QActionGroup(this);
    shaAction_ = makeAction("fingerprintSha256", fingerprintMenu_, "SHA256", [this] { fingerprintMd5_ = false; fingerprint_->setText(session_.snapshot().sha256); });
    md5Action_ = makeAction("fingerprintMd5", fingerprintMenu_, "MD5", [this] { fingerprintMd5_ = true; fingerprint_->setText(session_.snapshot().md5); });
    for (auto *action : {shaAction_, md5Action_}) { action->setCheckable(true); fingerprintGroup->addAction(action); }
    shaAction_->setChecked(true);
    import_ = makeAction("import", conversionMenu_, "&Import key...", [this] { beginLoad(true); });
    conversionMenu_->addSeparator();
    opensshAuto_ = makeAction("exportOpenSshAuto", conversionMenu_, "Export &OpenSSH key...", [this] { requestSave(PG_EXPORT_OPENSSH_AUTO); });
    opensshNew_ = makeAction("exportOpenSshNew", conversionMenu_, "Export OpenSSH key (force &new file format)...", [this] { requestSave(PG_EXPORT_OPENSSH_NEW); });
    sshcom_ = makeAction("exportSshCom", conversionMenu_, "Export &ssh.com key...", [this] { requestSave(PG_EXPORT_SSHCOM); });
    auto *languageGroup = new QActionGroup(this);
    chinese_ = makeAction("languageChinese", languageMenu_, "简体中文", [this] { languages_->setLanguage(QStringLiteral("zh_CN")); });
    english_ = makeAction("languageEnglish", languageMenu_, "English", [this] { languages_->setLanguage(QStringLiteral("en")); });
    for (auto *action : {chinese_, english_}) { action->setCheckable(true); languageGroup->addAction(action); }
    auto *help = makeAction("help", helpMenu_, "&Help", [this] { showHelp(); });
    help->setShortcut(QKeySequence::HelpContents);
    makeAction("about", helpMenu_, "&About PuTTYgen", [this] { showAbout(); });
}
void MainWindow::buildUi() {
    auto *central = new QWidget(this);
    auto *layout = new QVBoxLayout(central);
    setCentralWidget(central);
    keyGroup_ = new QGroupBox(central);
    layout->addWidget(keyGroup_, 1);
    auto *keyLayout = new QVBoxLayout(keyGroup_);
    keyPages_ = new QStackedWidget(keyGroup_);
    keyLayout->addWidget(keyPages_);
    auto *emptyPage = new QWidget;
    auto *emptyLayout = new QVBoxLayout(emptyPage);
    emptyLabel_ = new QLabel(emptyPage);
    emptyLayout->addWidget(emptyLabel_);
    emptyLayout->addStretch();
    keyPages_->addWidget(emptyPage);
    auto *progressPage = new QWidget;
    auto *progressLayout = new QVBoxLayout(progressPage);
    progressLabel_ = new QLabel(progressPage);
    progressLabel_->setWordWrap(true);
    progress_ = new QProgressBar(progressPage);
    progress_->setObjectName(QStringLiteral("generationProgress"));
    progressLayout->addWidget(progressLabel_);
    progressLayout->addWidget(progress_);
    progressLayout->addStretch();
    keyPages_->addWidget(progressPage);
    loadedPage_ = new QWidget;
    auto *loadedLayout = new QVBoxLayout(loadedPage_);
    publicPanel_ = new QWidget;
    auto *publicLayout = new QVBoxLayout(publicPanel_);
    publicLayout->setContentsMargins(0, 0, 0, 0);
    publicLabel_ = new QLabel(publicPanel_);
    publicKey_ = new QPlainTextEdit(publicPanel_);
    publicKey_->setObjectName(QStringLiteral("publicKey"));
    publicKey_->setReadOnly(true);
    publicKey_->setFont(QFontDatabase::systemFont(QFontDatabase::FixedFont));
    publicKey_->setMaximumHeight(120);
    publicLabel_->setBuddy(publicKey_);
    publicLayout->addWidget(publicLabel_);
    publicLayout->addWidget(publicKey_);
    loadedLayout->addWidget(publicPanel_);
    certificatePanel_ = new QWidget;
    auto *certificateLayout = new QVBoxLayout(certificatePanel_);
    certificateLayout->setContentsMargins(0, 0, 0, 0);
    certificateLabel_ = new QLabel(certificatePanel_);
    certificateLabel_->setTextFormat(Qt::PlainText);
    certificateLabel_->setWordWrap(true);
    certificateLabel_->setTextInteractionFlags(Qt::TextSelectableByMouse);
    certificateButton_ = new QPushButton(certificatePanel_);
    certificateButton_->setObjectName(QStringLiteral("certificateInfo"));
    certificateLayout->addWidget(certificateLabel_);
    certificateLayout->addWidget(certificateButton_, 0, Qt::AlignHCenter);
    connect(certificateButton_, &QPushButton::clicked, this, [this] { showText(tr("Certificate information"), session_.snapshot().certificateInfo); });
    loadedLayout->addWidget(certificatePanel_);
    auto *form = new QFormLayout;
    auto addEdit = [this, form](QLabel *&label, QLineEdit *&edit, const char *name, bool password) {
        label = new QLabel(loadedPage_);
        label->setObjectName(QString::fromLatin1(name) + QStringLiteral("Label"));
        label->setSizePolicy(QSizePolicy::Minimum, QSizePolicy::Preferred);
        edit = new QLineEdit(loadedPage_);
        edit->setObjectName(QString::fromLatin1(name));
        if (password) edit->setEchoMode(QLineEdit::Password);
        label->setBuddy(edit);
        form->addRow(label, edit);
    };
    addEdit(fingerprintLabel_, fingerprint_, "fingerprint", false);
    fingerprint_->setReadOnly(true);
    addEdit(commentLabel_, comment_, "comment", false);
    addEdit(passphraseLabel_, passphrase_, "passphrase", true);
    addEdit(confirmLabel_, confirm_, "confirmPassphrase", true);
    loadedLayout->addLayout(form);
    keyPages_->addWidget(loadedPage_);
    actionsGroup_ = new QGroupBox(central);
    layout->addWidget(actionsGroup_);
    auto *actions = new QGridLayout(actionsGroup_);
    generateLabel_ = new QLabel(actionsGroup_);
    loadLabel_ = new QLabel(actionsGroup_);
    saveLabel_ = new QLabel(actionsGroup_);
    generateButton_ = new QPushButton(actionsGroup_);
    generateButton_->setObjectName(QStringLiteral("generateButton"));
    loadButton_ = new QPushButton(actionsGroup_);
    loadButton_->setObjectName(QStringLiteral("loadButton"));
    savePublicButton_ = new QPushButton(actionsGroup_);
    savePublicButton_->setObjectName(QStringLiteral("savePublicButton"));
    savePrivateButton_ = new QPushButton(actionsGroup_);
    savePrivateButton_->setObjectName(QStringLiteral("savePrivateButton"));
    actions->addWidget(generateLabel_, 0, 0);
    actions->addWidget(generateButton_, 0, 1, 1, 2);
    actions->addWidget(loadLabel_, 1, 0);
    actions->addWidget(loadButton_, 1, 1, 1, 2);
    actions->addWidget(saveLabel_, 2, 0);
    actions->addWidget(savePublicButton_, 2, 1);
    actions->addWidget(savePrivateButton_, 2, 2);
    connect(generateButton_, &QPushButton::clicked, generate_, &QAction::trigger);
    connect(loadButton_, &QPushButton::clicked, load_, &QAction::trigger);
    connect(savePrivateButton_, &QPushButton::clicked, savePrivate_, &QAction::trigger);
    connect(savePublicButton_, &QPushButton::clicked, savePublic_, &QAction::trigger);
    paramsGroup_ = new QGroupBox(central);
    layout->addWidget(paramsGroup_);
    auto *paramsLayout = new QVBoxLayout(paramsGroup_);
    typeLabel_ = new QLabel(paramsGroup_);
    paramsLayout->addWidget(typeLabel_);
    auto *radioLayout = new QHBoxLayout;
    algorithmGroup_ = new QButtonGroup(this);
    for (int i = 0; i < 5; ++i) {
        auto *radio = new QRadioButton(paramsGroup_);
        radio->setObjectName(QStringLiteral("algorithmRadio%1").arg(i));
        algorithmRadios_[size_t(i)] = radio;
        algorithmGroup_->addButton(radio, i);
        radioLayout->addWidget(radio);
    }
    connect(algorithmGroup_, &QButtonGroup::idClicked, this, &MainWindow::updateAlgorithm);
    paramsLayout->addLayout(radioLayout);
    parameterOptions_ = new QWidget(paramsGroup_);
    auto *options = new QGridLayout(parameterOptions_);
    options->setContentsMargins(0, 0, 0, 0);
    bitsLabel_ = new QLabel(parameterOptions_);
    bits_ = new QLineEdit(QStringLiteral("2048"), parameterOptions_);
    bits_->setObjectName(QStringLiteral("bits"));
    bits_->setMaximumWidth(110);
    bitsLabel_->setBuddy(bits_);
    options->addWidget(bitsLabel_, 0, 0);
    options->addWidget(bits_, 0, 1);
    curveLabel_ = new QLabel(parameterOptions_);
    nistCurve_ = new QComboBox(parameterOptions_);
    nistCurve_->setObjectName(QStringLiteral("nistCurve"));
    for (int bits : {256, 384, 521}) nistCurve_->addItem(QStringLiteral("nistp%1").arg(bits), bits);
    edCurve_ = new QComboBox(parameterOptions_);
    edCurve_->setObjectName(QStringLiteral("edCurve"));
    edCurve_->addItem(QStringLiteral("Ed25519 (255 bits)"), 255);
    edCurve_->addItem(QStringLiteral("Ed448 (448 bits)"), 448);
    options->addWidget(curveLabel_, 1, 0);
    options->addWidget(nistCurve_, 1, 1);
    options->addWidget(edCurve_, 1, 1);
    options->setColumnStretch(0, 1);
    paramsLayout->addWidget(parameterOptions_);
    statusBar()->setSizeGripEnabled(true);
    for (auto *widget : findChildren<QWidget *>()) widget->setMouseTracking(true);
    central->setMouseTracking(true);
    setMouseTracking(true);
}
void MainWindow::retranslate() {
    fileMenu_->setTitle(tr("&File")); keyMenu_->setTitle(tr("&Key"));
    conversionMenu_->setTitle(tr("&Conversions")); helpMenu_->setTitle(tr("&Help"));
    languageMenu_->setTitle(tr("&Language")); primeMenu_->setTitle(tr("Prime generation &policy"));
    fingerprintMenu_->setTitle(tr("Fingerprint &type"));
    for (auto *action : findChildren<QAction *>()) {
        const QString source = action->property("sourceText").toString();
        if (!source.isEmpty()) action->setText(QCoreApplication::translate("MainWindow", source.toUtf8().constData()));
    }
    keyGroup_->setTitle(tr("Key")); actionsGroup_->setTitle(tr("Actions")); paramsGroup_->setTitle(tr("Parameters"));
    emptyLabel_->setText(tr("No key."));
    publicLabel_->setText(tr("&Public key for pasting into authorized_keys file:"));
    certificateButton_->setText(tr("Certificate &info..."));
    fingerprintLabel_->setText(tr("Key f&ingerprint:")); commentLabel_->setText(tr("Key &comment:"));
    passphraseLabel_->setText(tr("Key p&assphrase:")); confirmLabel_->setText(tr("C&onfirm passphrase:"));
    generateLabel_->setText(tr("Generate a public/private key pair")); loadLabel_->setText(tr("Load an existing private key file"));
    saveLabel_->setText(tr("Save the generated key"));
    generateButton_->setText(tr("&Generate")); loadButton_->setText(tr("&Load"));
    savePublicButton_->setText(tr("Save p&ublic key")); savePrivateButton_->setText(tr("&Save private key"));
    typeLabel_->setText(tr("Type of key to generate:"));
    const char *types[] = {"&RSA", "&DSA", "&ECDSA", "EdD&SA", "SSH-&1 (RSA)"};
    for (int i = 0; i < 5; ++i) algorithmRadios_[size_t(i)]->setText(tr(types[i]));
    bitsLabel_->setText(tr("Number of &bits in a generated key:"));
    curveLabel_->setText(tr("Cur&ve to use for generating this key:"));
    chinese_->setChecked(languages_->language() == QStringLiteral("zh_CN"));
    english_->setChecked(languages_->language() == QStringLiteral("en"));
    for (auto *label : {fingerprintLabel_, commentLabel_, passphraseLabel_, confirmLabel_})
        label->setMinimumWidth(label->sizeHint().width());
    loadedPage_->layout()->activate();
    centralWidget()->layout()->activate();
    updateState();
}
void MainWindow::changeEvent(QEvent *event) {
    if (event->type() == QEvent::LanguageChange) retranslate();
    QMainWindow::changeEvent(event);
}
void MainWindow::updateAlgorithm(int algorithm) {
    algorithm_ = algorithm;
    algorithmActions_[size_t(algorithm)]->setChecked(true);
    algorithmRadios_[size_t(algorithm)]->setChecked(true);
    const bool curve = algorithm == PG_ECDSA || algorithm == PG_EDDSA;
    bits_->setVisible(!curve); bitsLabel_->setVisible(!curve);
    curveLabel_->setVisible(curve);
    nistCurve_->setVisible(algorithm == PG_ECDSA); edCurve_->setVisible(algorithm == PG_EDDSA);
    curveLabel_->setBuddy(algorithm == PG_ECDSA ? static_cast<QWidget *>(nistCurve_) : edCurve_);
}
void MainWindow::updateState() {
    const bool busy = collecting_ || session_.busy() || closePending_;
    const auto &s = session_.snapshot();
    const bool ready = !busy && s.exists;
    keyPages_->setCurrentIndex(busy ? 1 : (s.exists ? 2 : 0));
    progressLabel_->setText(closePending_ ? tr("Waiting for the current operation to finish before closing...")
        : collecting_ ? tr("Please generate some randomness by moving the mouse over the blank area.")
        : tr("Processing key. Please wait..."));
    generate_->setEnabled(!busy); load_->setEnabled(!busy); import_->setEnabled(!busy);
    generateButton_->setEnabled(!busy); loadButton_->setEnabled(!busy);
    for (auto *action : {savePrivate_, savePublic_}) action->setEnabled(ready);
    savePrivateButton_->setEnabled(ready); savePublicButton_->setEnabled(ready);
    paramsGroup_->setEnabled(!busy);
    for (auto *action : algorithmActions_) action->setEnabled(!busy);
    for (auto *action : primeActions_) action->setEnabled(!busy);
    strongRsa_->setEnabled(!busy); ppkAction_->setEnabled(!busy);
    opensshAuto_->setEnabled(ready && s.openssh); opensshNew_->setEnabled(ready && s.openssh);
    sshcom_->setEnabled(ready && s.sshcom);
    addCertificate_->setEnabled(ready && s.addCertificate); removeCertificate_->setEnabled(ready && s.certificate);
    publicPanel_->setVisible(!s.certificate); certificatePanel_->setVisible(s.certificate);
    loadedPage_->setEnabled(ready);
    if (busy && !collecting_ && progress_->maximum() != 0) progress_->setRange(0, 0);
}
void MainWindow::beginGenerate() {
    if (session_.busy() || collecting_) return;
    unsigned bits = 0;
    if (algorithm_ == PG_ECDSA) bits = nistCurve_->currentData().toUInt();
    else if (algorithm_ == PG_EDDSA) bits = edCurve_->currentData().toUInt();
    else {
        bool valid = false;
        const auto entered = bits_->text().toUInt(&valid);
        bits = valid ? entered : 2048;
        if (bits < 256) {
            if (QMessageBox::warning(this, tr("PuTTYgen Warning"), tr("PuTTYgen will not generate a key smaller than 256 bits.\nKey length reset to default 2048. Continue?"), QMessageBox::Ok | QMessageBox::Cancel, QMessageBox::Cancel) != QMessageBox::Ok) return;
            bits = 2048; bits_->setText(QStringLiteral("2048"));
        } else if (bits < 2048) {
            if (QMessageBox::warning(this, tr("PuTTYgen Warning"), tr("Keys shorter than 2048 bits are not recommended. Really generate this key?"), QMessageBox::Ok | QMessageBox::Cancel, QMessageBox::Cancel) != QMessageBox::Ok) return;
        }
    }
    int prime = 0;
    for (int i = 0; i < 3; ++i) if (primeActions_[size_t(i)]->isChecked()) prime = i;
    pendingGeneration_ = {pg_algorithm(algorithm_), bits, pg_prime_policy(prime), strongRsa_->isChecked()};
    collecting_ = true;
    entropy_.clear(); entropyCount_ = 0;
    entropyRequired_ = int(std::min(bits / 2 * 2, entropyCapacity_));
    entropyClock_.start(); lastEntropyTime_ = 0;
    progress_->setRange(0, entropyRequired_); progress_->setValue(0);
    updateState();
}
bool MainWindow::eventFilter(QObject *object, QEvent *event) {
    if (collecting_ && event->type() == QEvent::MouseMove) {
        auto *widget = qobject_cast<QWidget *>(object);
        if (widget && (widget == this || isAncestorOf(widget))) {
            auto *mouse = static_cast<QMouseEvent *>(event);
            const QPoint p = mapFromGlobal(mouse->globalPosition().toPoint());
            const qint64 now = entropyClock_.nsecsElapsed();
            const qint32 values[] = {p.x(), p.y()};
            entropy_.append(reinterpret_cast<const char *>(values), int(sizeof(values)));
            entropy_.append(reinterpret_cast<const char *>(&now), int(sizeof(now)));
            if (now / 1000000 - lastEntropyTime_ > 10) {
                entropyCount_ += 2; lastEntropyTime_ = now / 1000000;
                progress_->setValue(entropyCount_);
            }
            if (entropyCount_ >= entropyRequired_) {
                collecting_ = false;
                session_.generate(pendingGeneration_, std::move(entropy_));
                updateState();
            }
        }
    }
    return QMainWindow::eventFilter(object, event);
}
void MainWindow::beginLoad(bool import) {
    commentTimer_.stop();
    const QString path = QFileDialog::getOpenFileName(this, tr("Load private key"), {}, import ? tr("All files (*)") : tr("PuTTY private keys (*.ppk);;All files (*)"));
    if (!path.isEmpty()) loadPath(path, import);
    else resumeCommentUpdate();
}
void MainWindow::loadPath(const QString &path, bool import) {
    if (session_.busy() || collecting_) return;
    clearLoadPassphrase();
    loadPath_ = path; importing_ = import;
    session_.probe(path);
}
void MainWindow::clearLoadPassphrase() {
    loadPassphrase_.fill('\0');
    loadPassphrase_.clear();
}
void MainWindow::resumeCommentUpdate() {
    // 完整操作或模态选择结束后再恢复，避免注释任务抢占口令重试等后续请求。
    if (!session_.busy() && !collecting_ && !closePending_ && !QApplication::activeModalWidget()
        && session_.snapshot().exists && session_.snapshot().comment != comment_->text())
        commentTimer_.start();
}
void MainWindow::promptLoad(bool retry) {
    if (encrypted_ || retry) {
        bool ok = false;
        const QString title = retry ? tr("Wrong passphrase. Try again.") : tr("Enter passphrase");
        QString value = QInputDialog::getText(this, title, tr("Passphrase for key:\n%1").arg(fileComment_), QLineEdit::Password, {}, &ok);
        if (!ok) { clearLoadPassphrase(); resumeCommentUpdate(); return; }
        clearLoadPassphrase();
        loadPassphrase_ = value.toUtf8();
        value.fill(QChar(0));
    }
    session_.load(loadPath_, loadPassphrase_);
}
void MainWindow::requestSave(pg_save_format format) {
    if (session_.busy() || collecting_ || !session_.snapshot().exists) return;
    commentTimer_.stop();
    pendingSave_ = format;
    if (format != PG_SAVE_PUBLIC) {
        if (passphrase_->text() != confirm_->text()) {
            QMessageBox::critical(this, tr("PuTTYgen Error"), tr("The two passphrases given do not match."));
            resumeCommentUpdate();
            return;
        }
        if (passphrase_->text().isEmpty() && QMessageBox::warning(this, tr("PuTTYgen Warning"),
            tr("Are you sure you want to save this key without a passphrase to protect it?"), QMessageBox::Yes | QMessageBox::No, QMessageBox::No) != QMessageBox::Yes) {
            resumeCommentUpdate();
            return;
        }
        if (format == PG_SAVE_PRIVATE && !session_.snapshot().ssh1) {
            session_.validate(ppk_, passphrase_->text().toUtf8());
            return;
        }
    }
    chooseSavePath();
}
void MainWindow::chooseSavePath() {
    const bool pub = pendingSave_ == PG_SAVE_PUBLIC;
    const QString path = QFileDialog::getSaveFileName(this, pub ? tr("Save public key as") : tr("Save private key as"), {},
        pendingSave_ == PG_SAVE_PRIVATE && !session_.snapshot().ssh1 ? tr("PuTTY private keys (*.ppk);;All files (*)") : tr("All files (*)"), nullptr, QFileDialog::DontConfirmOverwrite);
    if (path.isEmpty()) { resumeCommentUpdate(); return; }
    if (QFileInfo::exists(path) && QMessageBox::warning(this, tr("PuTTYgen Warning"), tr("Overwrite existing file\n%1?").arg(path),
        QMessageBox::Yes | QMessageBox::No, QMessageBox::No) != QMessageBox::Yes) {
        resumeCommentUpdate();
        return;
    }
    session_.save(path, pendingSave_, pub ? QByteArray() : passphrase_->text().toUtf8(), comment_->text(), ppk_);
}
void MainWindow::operationCompleted(int operation, int error, const QString &detail) {
    if (closePending_) {
        clearLoadPassphrase();
        closePending_ = false;
        close();
        return;
    }
    if (operation == SessionController::ValidateParameters) { resumeCommentUpdate(); return; }
    if (error != PG_OK) {
        if (operation == SessionController::Load && error == PG_ERR_PASSPHRASE) { promptLoad(true); return; }
        clearLoadPassphrase();
        showError(error, detail);
        resumeCommentUpdate();
        return;
    }
    switch (operation) {
    case SessionController::Probe: promptLoad(false); break;
    case SessionController::Load:
    case SessionController::Generate: {
        const auto &s = session_.snapshot();
        comment_->setText(s.comment);
        const QString pass = operation == SessionController::Load ? QString::fromUtf8(loadPassphrase_) : QString();
        passphrase_->setText(pass); confirm_->setText(pass);
        clearLoadPassphrase();
        updateAlgorithm(int(s.algorithm));
        if (s.algorithm == PG_ECDSA) nistCurve_->setCurrentIndex(nistCurve_->findData(s.bits));
        else if (s.algorithm == PG_EDDSA) edCurve_->setCurrentIndex(edCurve_->findData(s.bits));
        else bits_->setText(QString::number(s.bits));
        if (operation == SessionController::Load && foreign_ && !importing_)
            QMessageBox::information(this, tr("PuTTYgen"), tr("Successfully imported foreign key (%1).\nTo use this key with PuTTY, choose Save private key to save it in PuTTY's own format.").arg(fileFormat_));
        if (operation == SessionController::Load && obsolete_)
            QMessageBox::information(this, tr("PuTTYgen"), tr("This private key file uses an old format. Save it again to convert it to the current PuTTY format."));
        break;
    }
    case SessionController::Validate: chooseSavePath(); break;
    default: break;
    }
    updateState();
    resumeCommentUpdate();
}
void MainWindow::showError(int error, const QString &detail) {
    QString message;
    switch (pg_error_code(error)) {
    case PG_ERR_ARGUMENT: message = tr("Invalid key or file parameters."); break;
    case PG_ERR_IO: message = tr("Unable to read or write the key file."); break;
    case PG_ERR_FORMAT: message = tr("Unable to load this key file (invalid format)."); break;
    case PG_ERR_PASSPHRASE: message = tr("Wrong passphrase."); break;
    case PG_ERR_RANDOM: message = tr("Unable to obtain secure random data from the operating system."); break;
    case PG_ERR_CERTIFICATE: message = tr("Invalid certificate."); break;
    case PG_ERR_CERT_MISMATCH: message = tr("The certificate does not match the current private key."); break;
    case PG_ERR_UNSUPPORTED: message = tr("This key does not support the selected format or operation."); break;
    case PG_OK: return;
    }
    QMessageBox box(QMessageBox::Critical, tr("PuTTYgen Error"), message, QMessageBox::Ok, this);
    if (!detail.isEmpty()) box.setDetailedText(detail);
    box.exec();
}
void MainWindow::closeEvent(QCloseEvent *event) {
    if (session_.busy()) {
        closePending_ = true;
        updateState();
        event->ignore();
        return;
    }
    collecting_ = false;
    entropy_.fill('\0'); entropy_.clear();
    clearLoadPassphrase();
    passphrase_->clear(); confirm_->clear();
    event->accept();
}
void MainWindow::showText(const QString &title, const QString &text, bool html) {
    QDialog dialog(this);
    dialog.setWindowTitle(title);
    auto *layout = new QVBoxLayout(&dialog);
    auto *browser = new QTextBrowser(&dialog);
    browser->setOpenExternalLinks(true);
    if (html) browser->setHtml(text); else browser->setPlainText(text);
    layout->addWidget(browser);
    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Close, &dialog);
    connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    layout->addWidget(buttons);
    dialog.resize(700, 520);
    dialog.exec();
}
void MainWindow::showHelp() {
    QFile file(languages_->language() == QStringLiteral("en") ? QStringLiteral(":/help/en.html") : QStringLiteral(":/help/zh_CN.html"));
    if (file.open(QIODevice::ReadOnly)) showText(tr("PuTTYgen Help"), QString::fromUtf8(file.readAll()), true);
}
void MainWindow::showLicence() {
    QFile file(QStringLiteral(":/licences/putty.txt"));
    if (file.open(QIODevice::ReadOnly)) showText(tr("PuTTY Licence"), QString::fromUtf8(file.readAll()));
}
void MainWindow::showAbout() {
    QDialog dialog(this);
    dialog.setWindowTitle(tr("About PuTTYgen"));
    auto *layout = new QVBoxLayout(&dialog);
    auto *text = new QLabel(tr("PuTTYgen GUI 0.1.0\nPuTTY key engine 0.85\n\nPuTTY SSH key generation and conversion tool.\nQt %1").arg(QString::fromLatin1(qVersion())), &dialog);
    text->setAlignment(Qt::AlignCenter);
    text->setWordWrap(true);
    layout->addWidget(text);
    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Close, &dialog);
    auto *licence = buttons->addButton(tr("View &Licence"), QDialogButtonBox::ActionRole);
    auto *website = buttons->addButton(tr("Visit &Web Site"), QDialogButtonBox::ActionRole);
    connect(licence, &QPushButton::clicked, this, &MainWindow::showLicence);
    connect(website, &QPushButton::clicked, this, [] { QDesktopServices::openUrl(QUrl(QStringLiteral("https://www.chiark.greenend.org.uk/~sgtatham/putty/"))); });
    connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    layout->addWidget(buttons);
    dialog.resize(460, 210);
    dialog.exec();
}
