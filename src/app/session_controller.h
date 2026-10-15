#pragma once
#include "core/pg_core.h"
#include <QByteArray>
#include <QObject>
#include <QThread>
#include <QString>
#include <functional>

struct KeySnapshot {
    bool exists = false;
    pg_algorithm algorithm = PG_RSA;
    unsigned bits = 0;
    bool ssh1 = false, certificate = false, addCertificate = false, openssh = false, sshcom = false;
    QString algorithmName, comment, publicText, sha256, md5, certificateInfo;
};

class SessionController final : public QObject {
    Q_OBJECT
public:
    enum Operation { Initialize, Probe, Load, Generate, Save, Comment, AddCertificate, RemoveCertificate, Validate, ValidateParameters };
    explicit SessionController(QObject *parent = nullptr);
    ~SessionController() override;
    bool busy() const { return busy_; }
    const KeySnapshot &snapshot() const { return snapshot_; }
    void probe(const QString &path);
    void load(const QString &path, QByteArray passphrase);
    void generate(pg_generate_params params, QByteArray entropy);
    void save(const QString &path, pg_save_format format, QByteArray passphrase,
              const QString &comment, pg_ppk_params params);
    void setComment(const QString &comment);
    void addCertificate(const QString &path, const QString &comment);
    void removeCertificate(const QString &comment);
    void validate(pg_ppk_params params, QByteArray passphrase);
    void validateParameters(pg_ppk_params params);
signals:
    void busyChanged(bool busy);
    void completed(int operation, int error, const QString &detail);
    void snapshotChanged();
    void defaultsReady(pg_ppk_params params, unsigned entropyBits);
    void probed(bool encrypted, bool foreignFormat, bool obsoleteFormat, const QString &comment, const QString &format);
    void progress(unsigned completed, unsigned total);
private:
    void execute(Operation operation, std::function<bool(pg_error &)> work);
    KeySnapshot readSnapshot(pg_error &error);
    static void progressCallback(void *context, unsigned completed, unsigned total);
    QThread thread_;
    QObject *worker_ = nullptr;
    pg_key *key_ = nullptr;
    bool initialized_ = false, busy_ = false;
    KeySnapshot snapshot_;
};
