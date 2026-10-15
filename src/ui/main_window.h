#pragma once
#include "app/language_manager.h"
#include "app/session_controller.h"
#include <QElapsedTimer>
#include <QMainWindow>
#include <QTimer>
#include <array>
class QAction;
class QActionGroup;
class QButtonGroup;
class QComboBox;
class QGroupBox;
class QLabel;
class QLineEdit;
class QMenu;
class QProgressBar;
class QPushButton;
class QPlainTextEdit;
class QRadioButton;
class QStackedWidget;
class MainWindow final : public QMainWindow {
    Q_OBJECT
public:
    explicit MainWindow(LanguageManager *languages, QWidget *parent = nullptr);
    SessionController *controller() { return &session_; }
    bool collectingEntropy() const { return collecting_; }
    void loadPath(const QString &path, bool import = false);
protected:
    bool eventFilter(QObject *object, QEvent *event) override;
    void changeEvent(QEvent *event) override;
    void closeEvent(QCloseEvent *event) override;
private:
    QAction *makeAction(const char *name, QMenu *menu, const char *text, const std::function<void()> &handler);
    void buildMenus();
    void buildUi();
    void retranslate();
    void updateState();
    void updateAlgorithm(int algorithm);
    void beginGenerate();
    void beginLoad(bool import);
    void promptLoad(bool retry);
    void requestSave(pg_save_format format);
    void chooseSavePath();
    void operationCompleted(int operation, int error, const QString &detail);
    void showError(int error, const QString &detail);
    void showText(const QString &title, const QString &text, bool html = false);
    void showHelp();
    void showAbout();
    void showLicence();
    void clearLoadPassphrase();
    void resumeCommentUpdate();
    LanguageManager *languages_;
    SessionController session_;
    std::array<QAction *, 5> algorithmActions_{};
    std::array<QRadioButton *, 5> algorithmRadios_{};
    std::array<QAction *, 3> primeActions_{};
    QAction *generate_, *load_, *savePrivate_, *savePublic_, *import_, *addCertificate_, *removeCertificate_;
    QAction *opensshAuto_, *opensshNew_, *sshcom_, *strongRsa_, *ppkAction_, *shaAction_, *md5Action_, *chinese_, *english_;
    QMenu *fileMenu_, *keyMenu_, *conversionMenu_, *helpMenu_, *languageMenu_, *primeMenu_, *fingerprintMenu_;
    QGroupBox *keyGroup_, *actionsGroup_, *paramsGroup_;
    QLabel *emptyLabel_, *progressLabel_, *publicLabel_, *certificateLabel_;
    QLabel *fingerprintLabel_, *commentLabel_, *passphraseLabel_, *confirmLabel_, *typeLabel_, *bitsLabel_, *curveLabel_;
    QLabel *generateLabel_, *loadLabel_, *saveLabel_;
    QLineEdit *fingerprint_, *comment_, *passphrase_, *confirm_, *bits_;
    QPlainTextEdit *publicKey_;
    QProgressBar *progress_;
    QStackedWidget *keyPages_;
    QWidget *loadedPage_, *publicPanel_, *certificatePanel_, *parameterOptions_;
    QPushButton *generateButton_, *loadButton_, *savePrivateButton_, *savePublicButton_, *certificateButton_;
    QComboBox *nistCurve_, *edCurve_;
    QButtonGroup *algorithmGroup_;
    QTimer commentTimer_;
    QElapsedTimer entropyClock_;
    QByteArray entropy_, loadPassphrase_;
    QString loadPath_, fileComment_, fileFormat_;
    bool collecting_ = false, closePending_ = false, importing_ = false, foreign_ = false, encrypted_ = false, obsolete_ = false;
    bool fingerprintMd5_ = false;
    int algorithm_ = PG_RSA, entropyCount_ = 0, entropyRequired_ = 0;
    qint64 lastEntropyTime_ = 0;
    unsigned entropyCapacity_ = 912;
    pg_generate_params pendingGeneration_{};
    pg_ppk_params ppk_{3, PG_ARGON2_ID, 8192, 1, 100, 1};
    pg_save_format pendingSave_ = PG_SAVE_PRIVATE;
};
