#include "terminal_window.h"
#include <QCloseEvent>
#include <QInputMethodEvent>
#include <QKeyEvent>
#include <QMessageBox>
#include <QPainter>
#include <QPushButton>
#include <QStatusBar>

TerminalCanvas::TerminalCanvas(TerminalController *controller, QWidget *parent)
    : QWidget(parent), controller_(controller)
{
    setFocusPolicy(Qt::StrongFocus); setAttribute(Qt::WA_InputMethodEnabled);
    connect(controller_, &TerminalController::frameChanged, this, qOverload<>(&QWidget::update));
}
void TerminalCanvas::paintEvent(QPaintEvent *)
{
    QPainter painter(this); painter.fillRect(rect(), Qt::black);
    if (!controller_->frame().isNull()) painter.drawImage(QPointF(0, 0), controller_->frame());
}
void TerminalCanvas::keyPressEvent(QKeyEvent *event)
{
    pt_key arrow;
    switch (event->key()) {
      case Qt::Key_Up: arrow = PT_KEY_UP; break;
      case Qt::Key_Down: arrow = PT_KEY_DOWN; break;
      case Qt::Key_Left: arrow = PT_KEY_LEFT; break;
      case Qt::Key_Right: arrow = PT_KEY_RIGHT; break;
      default: {
        QByteArray text = event->text().toUtf8();
        if (event->key() == Qt::Key_Return || event->key() == Qt::Key_Enter) text = "\r";
        if (event->key() == Qt::Key_Backspace) text = "\x7f";
        if (!text.isEmpty()) controller_->sendInput(text);
        return;
      }
    }
    controller_->sendKey(arrow);
}
void TerminalCanvas::inputMethodEvent(QInputMethodEvent *event)
{
    if (!event->commitString().isEmpty()) controller_->sendInput(event->commitString().toUtf8());
    event->accept();
}
TerminalWindow::TerminalWindow(TerminalController *controller) : controller_(controller)
{
    setWindowTitle(QStringLiteral("PuTTY")); resize(800, 550);
    setCentralWidget(new TerminalCanvas(controller_, this));
    statusBar()->showMessage(tr("尚未连接"));
    connect(controller_, &TerminalController::packetReceived, this, [this](const TerminalPacket &packet) {
        const auto text = QString::fromUtf8(packet.data);
        switch (packet.header.type) {
          case PT_TITLE: setWindowTitle(text + QStringLiteral(" - PuTTY")); break;
          case PT_TRUST: statusBar()->showMessage(packet.header.a ? tr("本地身份验证") : tr("远程会话")); break;
          case PT_SESSION_STARTED: statusBar()->showMessage(tr("已连接")); break;
          case PT_REMOTE_EXIT: statusBar()->showMessage(tr("远程程序已退出（%1）").arg(packet.header.a)); break;
          case PT_DISCONNECTED: statusBar()->showMessage(tr("连接已断开")); break;
          case PT_ERROR: statusBar()->showMessage(text); break;
          case PT_HOST_KEY:
          case PT_WEAK_CRYPTO:
          case PT_LOG_APPEND: {
            auto *dialog = new QMessageBox(QMessageBox::Warning, tr("安全确认"), text,
                QMessageBox::NoButton, this);
            dialog->setTextFormat(Qt::PlainText); dialog->setAttribute(Qt::WA_DeleteOnClose);
            auto *accept = dialog->addButton(packet.header.type == PT_LOG_APPEND ? tr("追加") : tr("仅本次连接"), QMessageBox::AcceptRole);
            dialog->addButton(tr("取消"), QMessageBox::RejectRole);
            connect(dialog, &QMessageBox::finished, this, [this, dialog, accept, request = packet.request](int) {
                if (!closing_) controller_->reply(request, dialog->clickedButton() == accept ? 1 : 0);
            });
            dialog->open(); break;
          }
          default: break;
        }
    });
    connect(controller_, &TerminalController::errorOccurred, this, [this](const QString &text) { statusBar()->showMessage(text); });
    connect(controller_, &TerminalController::closed, this, [this] { close(); });
}
void TerminalWindow::closeEvent(QCloseEvent *event)
{
    if (controller_->isClosed()) { event->accept(); return; }
    closing_ = true; statusBar()->showMessage(tr("正在关闭连接…"));
    controller_->close(); event->ignore();
}
