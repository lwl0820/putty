#include "agent_window.h"
#include <QAction>
#include <QActionGroup>
#include <QApplication>
#include <QClipboard>
#include <QCloseEvent>
#include <QDialogButtonBox>
#include <QFileDialog>
#include <QHeaderView>
#include <QLabel>
#include <QLineEdit>
#include <QMenu>
#include <QMenuBar>
#include <QMessageBox>
#include <QPushButton>
#include <QStyle>
#include <QSignalBlocker>
#include <QSystemTrayIcon>
#include <QTableWidget>
#include <QTimer>
#include <QToolBar>
#include <QVBoxLayout>
#include <QtEndian>
namespace pageant {
namespace {
class PassphraseDialog final : public QDialog {
public:
    using QDialog::QDialog;
    std::function<bool(int)> submit;
    void done(int result) override
    {
        // 确定、取消及窗口关闭都须先成功排队，避免遗留不可再回复的核心请求。
        if (completed_ || (submit && !submit(result))) return;
        completed_ = true;
        QDialog::done(result);
    }
private:
    bool completed_ = false;
};
}
AgentWindow::AgentWindow(AgentController *controller, Language *language,
                         std::function<bool()> available, QWidget *parent)
    : QMainWindow(parent), controller_(controller), language_(language),
      trayAvailable_(available ? std::move(available) : [] { return QSystemTrayIcon::isSystemTrayAvailable(); })
{
    setObjectName(QStringLiteral("agentWindow"));
    resize(900, 460);
    auto *central = new QWidget(this);
    auto *layout = new QVBoxLayout(central);
    table_ = new QTableWidget(0, 4, central);
    table_->setObjectName(QStringLiteral("identities"));
    table_->setSelectionBehavior(QAbstractItemView::SelectRows);
    table_->setSelectionMode(QAbstractItemView::ExtendedSelection);
    table_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    table_->verticalHeader()->hide();
    table_->horizontalHeader()->setSectionResizeMode(QHeaderView::ResizeToContents);
    table_->horizontalHeader()->setSectionResizeMode(2, QHeaderView::Stretch);
    layout->addWidget(table_);
    socketLabel_ = new QLabel(central); layout->addWidget(socketLabel_);
    socket_ = new QLineEdit(central);
    socket_->setObjectName(QStringLiteral("agentSocket")); socket_->setReadOnly(true);
    layout->addWidget(socket_);
    status_ = new QLabel(central); status_->setObjectName(QStringLiteral("agentStatus"));
    status_->setTextFormat(Qt::PlainText);
    status_->setTextInteractionFlags(Qt::TextSelectableByMouse);
    status_->setWordWrap(true); layout->addWidget(status_);
    setCentralWidget(central);
    auto action = [this](const char *name) {
        auto *value = new QAction(this); value->setObjectName(QString::fromLatin1(name)); return value;
    };
    add_ = action("addKey"); addEncrypted_ = action("addEncryptedKey");
    remove_ = action("removeKey"); clear_ = action("clearKeys");
    reencrypt_ = action("reencryptKey"); reencryptAll_ = action("reencryptAll");
    refresh_ = action("refreshKeys"); copy_ = action("copySocket");
    show_ = action("showKeys"); exit_ = action("exitAgent");
    zh_ = action("languageChinese"); en_ = action("languageEnglish");
    auto *languages = new QActionGroup(this);
    zh_->setCheckable(true); en_->setCheckable(true);
    languages->addAction(zh_); languages->addAction(en_);
    keyMenu_ = menuBar()->addMenu(QString());
    for (auto *value : {add_, addEncrypted_, remove_, clear_, reencrypt_, reencryptAll_, refresh_})
        keyMenu_->addAction(value);
    keyMenu_->addSeparator(); keyMenu_->addAction(copy_); keyMenu_->addSeparator(); keyMenu_->addAction(exit_);
    languageMenu_ = menuBar()->addMenu(QString()); languageMenu_->addAction(zh_); languageMenu_->addAction(en_);
    auto *bar = addToolBar(QStringLiteral("Pageant")); bar->setMovable(false);
    for (auto *value : {add_, addEncrypted_, remove_, reencrypt_, clear_}) bar->addAction(value);
    tray_ = new QSystemTrayIcon(style()->standardIcon(QStyle::SP_ComputerIcon), this);
    setWindowIcon(tray_->icon());
    trayMenu_ = new QMenu(this);
    for (auto *value : {show_, add_, addEncrypted_, clear_, reencryptAll_}) trayMenu_->addAction(value);
    trayMenu_->addSeparator(); trayMenu_->addAction(exit_);
    tray_->setContextMenu(trayMenu_);
    connect(tray_, &QSystemTrayIcon::activated, this, [this](auto reason) {
        if (reason == QSystemTrayIcon::Trigger || reason == QSystemTrayIcon::DoubleClick) {
            showNormal(); raise(); activateWindow();
        }
    });
    connect(add_, &QAction::triggered, this, [this] { chooseFiles(false); });
    connect(addEncrypted_, &QAction::triggered, this, [this] { chooseFiles(true); });
    connect(remove_, &QAction::triggered, this, [this] { controller_->remove(selection()); });
    connect(reencrypt_, &QAction::triggered, this, [this] { controller_->reencrypt(selection()); });
    connect(reencryptAll_, &QAction::triggered, controller_, &AgentController::reencryptAll);
    connect(clear_, &QAction::triggered, controller_, &AgentController::clear);
    connect(refresh_, &QAction::triggered, controller_, &AgentController::refresh);
    connect(copy_, &QAction::triggered, this, [this] { QApplication::clipboard()->setText(socket_->text()); });
    connect(show_, &QAction::triggered, this, [this] { showNormal(); raise(); activateWindow(); });
    connect(exit_, &QAction::triggered, this, &AgentWindow::requestExit);
    connect(zh_, &QAction::triggered, this, [this] { language_->setEnglish(false); });
    connect(en_, &QAction::triggered, this, [this] { language_->setEnglish(true); });
    connect(language_, &Language::changed, this, &AgentWindow::translate);
    connect(table_, &QTableWidget::itemSelectionChanged, this, &AgentWindow::updateState);
    connect(controller_, &AgentController::stateChanged, this, &AgentWindow::updateState);
    connect(controller_, &AgentController::snapshotChanged, this, &AgentWindow::updateSnapshot);
    connect(controller_, &AgentController::prompt, this, [this](quint64 id, const QString &comment) {
        prompts_.enqueue({id, comment}); showPrompt();
    });
    connect(controller_, &AgentController::dismiss, this, &AgentWindow::dismiss);
    connect(controller_, &AgentController::promptsCleared, this, &AgentWindow::clearPrompts);
    connect(controller_, &AgentController::failed, this, [this](const QString &detail) {
        statusCode_ = 4; statusDetail_ = detail; updateStatus();
    });
    connect(controller_, &AgentController::completed, this, [this](int, int result, const QString &detail) {
        statusCode_ = result; statusDetail_ = detail; updateStatus();
    });
    trayTimer_ = new QTimer(this); trayTimer_->setInterval(1000);
    connect(trayTimer_, &QTimer::timeout, this, &AgentWindow::checkTray); trayTimer_->start();
    translate(); updateSnapshot(); checkTray();
}
QVector<Identity> AgentWindow::selection() const
{
    QVector<Identity> keys;
    for (const auto &index : table_->selectionModel()->selectedRows())
        if (index.row() < rows_.size()) keys.push_back(rows_[index.row()]);
    return keys;
}
void AgentWindow::translate()
{
    const auto t = [this](const char *zh, const char *en) { return language_->text(zh, en); };
    setWindowTitle(t("Pageant 密钥代理", "Pageant Key Agent"));
    keyMenu_->setTitle(t("密钥(&K)", "&Keys")); languageMenu_->setTitle(t("语言(&L)", "&Language"));
    add_->setText(t("添加密钥…", "Add keys…")); addEncrypted_->setText(t("添加加密密钥…", "Add encrypted keys…"));
    remove_->setText(t("删除所选", "Remove selected")); clear_->setText(t("清空密钥", "Remove all keys"));
    reencrypt_->setText(t("重新加密所选", "Re-encrypt selected"));
    reencryptAll_->setText(t("重新加密全部", "Re-encrypt all"));
    refresh_->setText(t("刷新", "Refresh")); copy_->setText(t("复制代理地址", "Copy agent address"));
    show_->setText(t("显示密钥", "Show keys")); exit_->setText(t("退出", "Exit"));
    zh_->setText(QStringLiteral("简体中文")); en_->setText(QStringLiteral("English"));
    zh_->setChecked(!language_->english()); en_->setChecked(language_->english());
    table_->setHorizontalHeaderLabels({t("类型", "Type"), t("指纹", "Fingerprint"),
                                      t("注释", "Comment"), t("状态", "State")});
#ifdef Q_OS_WIN
    socketLabel_->setText(t("代理地址（Windows 命名管道）", "Agent address (Windows named pipe)"));
#else
    socketLabel_->setText(t("代理地址（SSH_AUTH_SOCK）", "Agent address (SSH_AUTH_SOCK)"));
#endif
    tray_->setToolTip(windowTitle());
    if (dialog_) {
        dialog_->setWindowTitle(t("密钥口令", "Key passphrase"));
        promptLabel_->setText(t("请输入以下密钥的口令：\n", "Enter the passphrase for:\n") + promptComment_);
        auto *buttons = dialog_->findChild<QDialogButtonBox *>();
        buttons->button(QDialogButtonBox::Ok)->setText(t("确定", "OK"));
        buttons->button(QDialogButtonBox::Cancel)->setText(t("取消", "Cancel"));
        promptError_->setText(promptErrorCode_ == 1 ?
            t("口令不能超过 65536 个 UTF-8 字节。", "The passphrase cannot exceed 65536 UTF-8 bytes.") :
            promptErrorCode_ == 2 ? t("暂时无法提交口令，请重试。", "Could not submit the passphrase. Please retry.") : QString());
    }
    updateSnapshot(); updateStatus();
}
void AgentWindow::updateStatus()
{
    if (exiting_) status_->setText(language_->text("正在退出…", "Exiting…"));
    else if (statusCode_ == PA_SUCCESS) status_->setText(language_->text("操作完成", "Operation completed"));
    else if (statusCode_ == PA_CANCELLED) status_->setText(language_->text("已取消", "Cancelled"));
    else if (statusCode_ >= 0) status_->setText((statusCode_ == 4 ?
        language_->text("操作未能完成。", "The operation could not be completed.") :
        language_->text("操作未能全部完成。", "The operation could not be fully completed.")) + statusDetail_);
}
void AgentWindow::updateState()
{
    const bool ready = controller_->isReady() && !exiting_;
    add_->setEnabled(ready && !controller_->adding());
    addEncrypted_->setEnabled(add_->isEnabled());
    remove_->setEnabled(ready && !selection().isEmpty());
    bool canReencrypt = false;
    for (const auto &key : selection()) if (key.version == 2 && (key.flags & 1)) canReencrypt = true;
    reencrypt_->setEnabled(ready && canReencrypt);
    bool anyReencrypt = false;
    for (const auto &key : rows_) if (key.version == 2 && (key.flags & 1)) anyReencrypt = true;
    reencryptAll_->setEnabled(ready && anyReencrypt);
    clear_->setEnabled(ready && (!rows_.isEmpty() || controller_->adding()));
    refresh_->setEnabled(ready); copy_->setEnabled(ready && !controller_->endpoint().isEmpty());
    socket_->setText(controller_->endpoint());
}
void AgentWindow::updateSnapshot()
{
    const auto selected = selection();
    const QSignalBlocker blocker(table_);
    rows_ = controller_->keys(); table_->setRowCount(rows_.size());
    for (int row = 0; row < rows_.size(); ++row) {
        const auto &key = rows_[row];
        QString algorithm = QStringLiteral("SSH-1 RSA");
        if (key.version == 2 && key.blob.size() >= 4) {
            quint32 size = qFromBigEndian<quint32>(key.blob.constData());
            if (size <= quint32(key.blob.size() - 4)) algorithm = QString::fromLatin1(key.blob.mid(4, size));
        }
        const auto state = (key.flags & 2) ? language_->text("已加密", "Encrypted") :
            (key.flags & 1) ? language_->text("已解密，可重新加密", "Decrypted, can re-encrypt") :
                             language_->text("已加载", "Loaded");
        const QStringList values{algorithm, key.fingerprint, key.comment, state};
        for (int column = 0; column < 4; ++column) {
            auto *item = new QTableWidgetItem(values[column]);
            item->setToolTip(values[column]); table_->setItem(row, column, item);
        }
        for (const auto &old : selected) if (old.version == key.version && old.blob == key.blob) {
            for (int column = 0; column < 4; ++column) table_->item(row, column)->setSelected(true);
            break;
        }
    }
    updateState();
}
void AgentWindow::chooseFiles(bool encrypted)
{
    auto *dialog = new QFileDialog(this);
    dialog->setAttribute(Qt::WA_DeleteOnClose);
    dialog->setFileMode(QFileDialog::ExistingFiles);
    dialog->setWindowTitle(language_->text("选择密钥", "Choose keys"));
    dialog->setLabelText(QFileDialog::FileName, language_->text("文件名：", "File name:"));
    dialog->setLabelText(QFileDialog::FileType, language_->text("文件类型：", "Files of type:"));
    dialog->setLabelText(QFileDialog::LookIn, language_->text("位置：", "Look in:"));
    dialog->setLabelText(QFileDialog::Accept, language_->text("打开", "Open"));
    dialog->setLabelText(QFileDialog::Reject, language_->text("取消", "Cancel"));
    connect(dialog, &QFileDialog::filesSelected, this,
            [this, encrypted](const QStringList &paths) { controller_->addFiles(paths, encrypted); });
    dialog->open();
}
void AgentWindow::showPrompt()
{
    if (dialog_ || prompts_.isEmpty() || exiting_) return;
    const auto pending = prompts_.dequeue(); promptId_ = pending.id; promptComment_ = pending.comment;
    auto *dialog = new PassphraseDialog(this); dialog_ = dialog;
    dialog->setObjectName(QStringLiteral("passphraseDialog"));
    dialog->setAttribute(Qt::WA_DeleteOnClose);
    auto *layout = new QVBoxLayout(dialog);
    promptLabel_ = new QLabel(dialog); promptLabel_->setWordWrap(true);
    promptLabel_->setTextFormat(Qt::PlainText); layout->addWidget(promptLabel_);
    passphrase_ = new QLineEdit(dialog); passphrase_->setObjectName(QStringLiteral("passphrase"));
    passphrase_->setEchoMode(QLineEdit::Password); passphrase_->setMaxLength(65536);
    layout->addWidget(passphrase_);
    promptErrorCode_ = 0;
    promptError_ = new QLabel(dialog); promptError_->setObjectName(QStringLiteral("passphraseError"));
    promptError_->setTextFormat(Qt::PlainText); promptError_->setWordWrap(true);
    layout->addWidget(promptError_);
    auto *buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, dialog);
    layout->addWidget(buttons);
    connect(buttons, &QDialogButtonBox::accepted, dialog, &QDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, dialog, &QDialog::reject);
    dialog->submit = [this, id = pending.id](int code) {
        const bool cancel = code != QDialog::Accepted;
        auto secret = cancel ? QByteArray() : passphrase_->text().toUtf8();
        const bool tooLong = secret.size() > 65536;
        const bool queued = !tooLong && controller_->reply(id, secret, cancel);
        secret.fill('\0');
        if (!queued) { promptErrorCode_ = tooLong ? 1 : 2; translate(); return false; }
        return true;
    };
    connect(dialog, &QDialog::finished, this, [this](int) {
        passphrase_->clear();
        dialog_.clear(); promptId_ = 0; passphrase_ = nullptr; promptLabel_ = nullptr; promptError_ = nullptr;
        showPrompt();
    });
    translate();
    showNormal(); dialog->show(); dialog->raise(); passphrase_->setFocus();
}
void AgentWindow::dismiss(quint64 request)
{
    for (auto it = prompts_.begin(); it != prompts_.end();) {
        if (it->id == request) it = prompts_.erase(it); else ++it;
    }
    if (dialog_ && promptId_ == request) {
        static_cast<PassphraseDialog *>(dialog_.data())->submit = {};
        dialog_->disconnect(this); passphrase_->clear(); dialog_->close(); dialog_.clear();
        promptId_ = 0; passphrase_ = nullptr; promptLabel_ = nullptr; promptError_ = nullptr;
        showPrompt();
    }
}
void AgentWindow::clearPrompts()
{
    prompts_.clear();
    if (dialog_) dismiss(promptId_);
}
void AgentWindow::checkTray()
{
    if (exiting_) return;
    if (trayAvailable_()) {
        if (!tray_->isVisible()) tray_->show();
    } else {
        tray_->hide();
        if (!isVisible()) { showNormal(); raise(); }
    }
}
void AgentWindow::requestExit()
{
    if (exiting_) return;
    exiting_ = true; trayTimer_->stop(); tray_->hide();
    if (closeQuestion_) closeQuestion_->close();
    clearPrompts();
    updateStatus();
    controller_->close(); updateState();
}
void AgentWindow::closeEvent(QCloseEvent *event)
{
    event->ignore();
    if (exiting_) return;
    if (trayAvailable_() && tray_->isVisible()) { hide(); return; }
    if (closeQuestion_) return;
    auto *question = new QMessageBox(QMessageBox::Question,
        language_->text("关闭 Pageant", "Close Pageant"),
        language_->text("保持代理运行，还是退出并移除内存中的密钥？", "Keep the agent running, or exit and remove its keys?"),
        QMessageBox::NoButton, this);
    question->setObjectName(QStringLiteral("closeQuestion"));
    question->setAttribute(Qt::WA_DeleteOnClose); closeQuestion_ = question;
    auto *keep = question->addButton(language_->text("保持运行", "Keep running"), QMessageBox::RejectRole);
    auto *quit = question->addButton(language_->text("退出", "Exit"), QMessageBox::DestructiveRole);
    question->setDefaultButton(keep);
    connect(question, &QMessageBox::buttonClicked, this, [this, quit](QAbstractButton *button) {
        if (button == quit) requestExit(); else { showNormal(); raise(); }
    });
    question->open();
}
}
