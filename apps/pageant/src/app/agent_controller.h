#ifndef PAGEANT_AGENT_CONTROLLER_H
#define PAGEANT_AGENT_CONTROLLER_H
#include "agent_codec.h"
#include <QHash>
#include <QQueue>
#include <QStringList>

namespace pageant {
class AgentController final : public QObject {
    Q_OBJECT
public:
    explicit AgentController(pa_options options, QObject *parent = nullptr);
    ~AgentController() override;
    const QVector<Identity> &keys() const { return keys_; }
    QString endpoint() const { return endpoint_; }
    bool isReady() const { return ready_ && !closing_; }
    bool isClosed() const { return closed_; }
    bool failedFatally() const { return fatal_; }
    bool adding() const { return currentAdd_ || !addPaths_.isEmpty(); }
    void addFiles(QStringList files, bool encrypted);
    void remove(const QVector<Identity> &keys);
    void reencrypt(const QVector<Identity> &keys);
    void clear();
    void reencryptAll();
    void refresh();
    bool reply(quint64 request, QByteArray passphrase, bool cancel);
    void close();
signals:
    void stateChanged();
    void snapshotChanged();
    void prompt(quint64 request, const QString &comment);
    void dismiss(quint64 request);
    void promptsCleared();
    void completed(int action, int result, const QString &detail);
    void failed(const QString &detail);
    void closed();
private:
    bool enqueue(pa_action action, quint32 flags = 0, const QByteArray &path = {},
                 QByteArray passphrase = {}, const Identity &key = {}, quint64 reply = 0);
    void sendNextAdd();
    void pump();
    void receive(const putty_runtime::Event &event);
    void erase(quint64 id);
    putty_runtime::Runtime runtime_;
    QVector<Identity> keys_;
    QQueue<quint64> queue_;
    QHash<quint64, putty_runtime::Command> commands_;
    QHash<quint64, pa_action> actions_;
    QStringList addPaths_;
    QString endpoint_;
    quint64 next_ = 1000000;
    qsizetype pendingBytes_ = 0;
    bool ready_ = false, closing_ = false, closed_ = false, fatal_ = false;
    bool currentAdd_ = false, encrypted_ = false;
};
}
#endif
