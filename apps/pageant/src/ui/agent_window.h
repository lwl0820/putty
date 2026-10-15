#ifndef PAGEANT_AGENT_WINDOW_H
#define PAGEANT_AGENT_WINDOW_H
#include "app/agent_controller.h"
#include "app/language.h"
#include <QMainWindow>
#include <QPointer>
#include <QQueue>
#include <functional>
class QAction;
class QDialog;
class QLabel;
class QLineEdit;
class QMenu;
class QSystemTrayIcon;
class QTableWidget;
class QTimer;
namespace pageant {
class AgentWindow final : public QMainWindow {
    Q_OBJECT
public:
    AgentWindow(AgentController *controller, Language *language,
                std::function<bool()> trayAvailable = {}, QWidget *parent = nullptr);
    void requestExit();
protected:
    void closeEvent(QCloseEvent *event) override;
private:
    struct Prompt { quint64 id; QString comment; };
    QVector<Identity> selection() const;
    void chooseFiles(bool encrypted);
    void translate();
    void updateState();
    void updateStatus();
    void updateSnapshot();
    void showPrompt();
    void dismiss(quint64 request);
    void clearPrompts();
    void checkTray();
    AgentController *controller_;
    Language *language_;
    std::function<bool()> trayAvailable_;
    QTableWidget *table_;
    QVector<Identity> rows_;
    QLabel *socketLabel_, *status_, *promptLabel_ = nullptr, *promptError_ = nullptr;
    QLineEdit *socket_, *passphrase_ = nullptr;
    QSystemTrayIcon *tray_;
    QMenu *trayMenu_, *keyMenu_, *languageMenu_;
    QTimer *trayTimer_;
    QAction *add_, *addEncrypted_, *remove_, *clear_, *reencrypt_, *reencryptAll_,
            *refresh_, *copy_, *show_, *exit_, *zh_, *en_;
    QQueue<Prompt> prompts_;
    QPointer<QDialog> dialog_;
    QPointer<QDialog> closeQuestion_;
    quint64 promptId_ = 0;
    QString promptComment_;
    QString statusDetail_;
    int statusCode_ = -1;
    int promptErrorCode_ = 0;
    bool exiting_ = false;
};
}
#endif
