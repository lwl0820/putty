#include "session_controller.h"
#include <QMetaObject>
#include <utility>

SessionController::SessionController(QObject *parent) : QObject(parent), worker_(new QObject) {
    worker_->moveToThread(&thread_);
    connect(&thread_, &QThread::finished, worker_, &QObject::deleteLater);
    thread_.start();
    execute(Initialize, [this](pg_error &) {
        pg_ppk_params defaults;
        pg_ppk_default_params(&defaults);
        const unsigned entropyBits = pg_entropy_bits();
        QMetaObject::invokeMethod(this, [this, defaults, entropyBits] { emit defaultsReady(defaults, entropyBits); }, Qt::QueuedConnection);
        return true;
    });
}
SessionController::~SessionController() {
    // 等待串行队列收束，密钥与随机核心均在所属工作线程销毁。
    QMetaObject::invokeMethod(worker_, [this] {
        pg_key_free(key_);
        key_ = nullptr;
        if (initialized_) pg_core_cleanup();
    }, Qt::BlockingQueuedConnection);
    thread_.quit();
    thread_.wait();
}
KeySnapshot SessionController::readSnapshot(pg_error &error) {
    KeySnapshot result;
    if (!key_) return result;
    pg_key_info info{};
    if (!pg_key_get_info(key_, &info, &error)) return result;
    result.exists = true;
    result.algorithm = info.algorithm;
    result.bits = info.bits;
    result.ssh1 = info.ssh1;
    result.certificate = info.certificate;
    result.addCertificate = info.can_add_certificate;
    result.openssh = info.can_export_openssh;
    result.sshcom = info.can_export_sshcom;
    result.algorithmName = QString::fromUtf8(info.algorithm_name);
    result.comment = QString::fromUtf8(info.comment);
    result.publicText = QString::fromUtf8(info.public_text);
    result.sha256 = QString::fromUtf8(info.fingerprint_sha256);
    result.md5 = QString::fromUtf8(info.fingerprint_md5);
    result.certificateInfo = QString::fromUtf8(info.certificate_info);
    pg_key_info_free(&info);
    return result;
}
void SessionController::execute(Operation operation, std::function<bool(pg_error &)> work) {
    if (busy_) return;
    busy_ = true;
    emit busyChanged(true);
    QMetaObject::invokeMethod(worker_, [this, operation, work = std::move(work)] {
        pg_error error{};
        if (!initialized_) initialized_ = pg_core_init(&error);
        bool ok = initialized_ && work(error);
        KeySnapshot snapshot;
        if (ok && operation != Probe && operation != Validate && operation != ValidateParameters) {
            snapshot = readSnapshot(error);
            ok = error.code == PG_OK;
        }
        const QString detail = QString::fromUtf8(error.detail);
        const int code = ok ? PG_OK : (error.code == PG_OK ? PG_ERR_FORMAT : error.code);
        QMetaObject::invokeMethod(this, [this, operation, code, detail, snapshot] {
            if (code == PG_OK && operation != Probe && operation != Validate && operation != ValidateParameters) {
                snapshot_ = snapshot;
                emit snapshotChanged();
            }
            busy_ = false;
            emit busyChanged(false);
            emit completed(operation, code, detail);
        }, Qt::QueuedConnection);
    }, Qt::QueuedConnection);
}
void SessionController::probe(const QString &path) {
    execute(Probe, [this, path](pg_error &error) {
        pg_file_info info{};
        bool ok = pg_probe_file(path.toUtf8().constData(), &info, &error);
        if (ok) {
            const bool encrypted = info.encrypted, foreign = info.foreign_format, obsolete = info.obsolete_format;
            const QString comment = QString::fromUtf8(info.comment), format = QString::fromUtf8(info.format_name);
            QMetaObject::invokeMethod(this, [this, encrypted, foreign, obsolete, comment, format] {
                emit probed(encrypted, foreign, obsolete, comment, format);
            }, Qt::QueuedConnection);
        }
        pg_file_info_free(&info);
        return ok;
    });
}
void SessionController::load(const QString &path, QByteArray passphrase) {
    execute(Load, [this, path, passphrase = std::move(passphrase)](pg_error &error) mutable {
        pg_key *replacement = nullptr;
        bool ok = pg_load(path.toUtf8().constData(), passphrase.constData(), &replacement, &error);
        pg_secure_clear(passphrase.data(), size_t(passphrase.size()));
        if (ok) { pg_key_free(key_); key_ = replacement; }
        return ok;
    });
}
void SessionController::progressCallback(void *context, unsigned completed, unsigned total) {
    auto *self = static_cast<SessionController *>(context);
    QMetaObject::invokeMethod(self, [self, completed, total] { emit self->progress(completed, total); }, Qt::QueuedConnection);
}
void SessionController::generate(pg_generate_params params, QByteArray entropy) {
    execute(Generate, [this, params, entropy = std::move(entropy)](pg_error &error) mutable {
        pg_key *replacement = nullptr;
        bool ok = pg_generate(&params, entropy.constData(), size_t(entropy.size()), progressCallback, this, &replacement, &error);
        pg_secure_clear(entropy.data(), size_t(entropy.size()));
        if (ok) { pg_key_free(key_); key_ = replacement; }
        return ok;
    });
}
void SessionController::save(const QString &path, pg_save_format format, QByteArray passphrase,
                             const QString &comment, pg_ppk_params params) {
    execute(Save, [this, path, format, passphrase = std::move(passphrase), comment, params](pg_error &error) mutable {
        bool ok = pg_key_set_comment(key_, comment.toUtf8().constData(), &error)
            && pg_save(key_, path.toUtf8().constData(), format, passphrase.constData(), &params, &error);
        pg_secure_clear(passphrase.data(), size_t(passphrase.size()));
        return ok;
    });
}
void SessionController::setComment(const QString &comment) {
    execute(Comment, [this, comment](pg_error &error) { return pg_key_set_comment(key_, comment.toUtf8().constData(), &error); });
}
void SessionController::addCertificate(const QString &path, const QString &comment) {
    execute(AddCertificate, [this, path, comment](pg_error &error) {
        return pg_key_set_comment(key_, comment.toUtf8().constData(), &error)
            && pg_add_certificate(key_, path.toUtf8().constData(), &error);
    });
}
void SessionController::removeCertificate(const QString &comment) {
    execute(RemoveCertificate, [this, comment](pg_error &error) {
        return pg_key_set_comment(key_, comment.toUtf8().constData(), &error)
            && pg_remove_certificate(key_, &error);
    });
}
void SessionController::validate(pg_ppk_params params, QByteArray passphrase) {
    execute(Validate, [params, passphrase = std::move(passphrase)](pg_error &error) mutable {
        bool ok = pg_ppk_validate(&params, passphrase.constData(), &error);
        pg_secure_clear(passphrase.data(), size_t(passphrase.size()));
        return ok;
    });
}

void SessionController::validateParameters(pg_ppk_params params) {
    execute(ValidateParameters, [params](pg_error &error) {
        // 与原版参数对话框一致，使用固定长度 64 的占位口令执行完整检查。
        const QByteArray passphrase(64, 'x');
        return pg_ppk_validate(&params, passphrase.constData(), &error);
    });
}
