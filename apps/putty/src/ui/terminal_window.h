#ifndef PT_TERMINAL_WINDOW_H
#define PT_TERMINAL_WINDOW_H
#include "app/terminal_controller.h"
#include <QMainWindow>
class TerminalCanvas final : public QWidget {
    Q_OBJECT
public:
    explicit TerminalCanvas(TerminalController *controller, QWidget *parent = nullptr);
protected:
    void paintEvent(QPaintEvent *) override;
    void keyPressEvent(QKeyEvent *) override;
    void inputMethodEvent(QInputMethodEvent *) override;
private:
    TerminalController *controller_;
};
class TerminalWindow final : public QMainWindow {
    Q_OBJECT
public:
    explicit TerminalWindow(TerminalController *controller);
protected:
    void closeEvent(QCloseEvent *) override;
private:
    TerminalController *controller_;
    bool closing_ = false;
};
#endif
