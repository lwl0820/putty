#include "ppk_dialog.h"
#include "app/session_controller.h"
#include <QComboBox>
#include <QDialogButtonBox>
#include <QFormLayout>
#include <QLineEdit>
#include <QMessageBox>
#include <QRegularExpressionValidator>
#include <QVBoxLayout>
#include <limits>
PpkDialog::PpkDialog(const pg_ppk_params &params, SessionController *controller, QWidget *parent) : QDialog(parent), params_(params), pending_(params), controller_(controller) {
    setWindowTitle(tr("Private Key File Parameters"));
    setObjectName(QStringLiteral("ppkDialog"));
    auto *layout = new QVBoxLayout(this);
    auto *form = new QFormLayout;
    layout->addLayout(form);
    version_ = new QComboBox(this);
    version_->setObjectName(QStringLiteral("ppkVersion"));
    version_->addItems({QStringLiteral("2"), QStringLiteral("3")});
    version_->setCurrentIndex(params.version == 2 ? 0 : 1);
    argon2_ = new QComboBox(this);
    argon2_->setObjectName(QStringLiteral("argon2"));
    argon2_->addItems({QStringLiteral("Argon2id"), QStringLiteral("Argon2i"), QStringLiteral("Argon2d")});
    argon2_->setCurrentIndex(int(params.argon2));
    const auto number = [this](const char *name, uint32_t value) {
        auto *edit = new QLineEdit(QString::number(value), this);
        edit->setObjectName(QString::fromLatin1(name));
        edit->setValidator(new QRegularExpressionValidator(QRegularExpression(QStringLiteral("[0-9]{1,10}")), edit));
        return edit;
    };
    memory_ = number("argonMemory", params.memory_kib);
    time_ = number("argonTime", params.passes_or_ms);
    parallelism_ = number("argonParallelism", params.parallelism);
    mode_ = new QComboBox(this);
    mode_->setObjectName(QStringLiteral("argonMode"));
    mode_->addItems({tr("Milliseconds (automatic passes)"), tr("Fixed passes")});
    mode_->setCurrentIndex(params.passes_auto ? 0 : 1);
    if (params.passes_auto) automaticTime_ = params.passes_or_ms;
    else fixedPasses_ = params.passes_or_ms;
    connect(mode_, &QComboBox::currentIndexChanged, this, [this](int index) {
        bool valid = false;
        const uint value = time_->text().toUInt(&valid);
        if (index == 0) {
            if (valid) fixedPasses_ = value;
            time_->setText(QString::number(automaticTime_));
        } else {
            if (valid) automaticTime_ = value;
            time_->setText(QString::number(fixedPasses_));
        }
    });
    form->addRow(tr("PPK file &version:"), version_);
    form->addRow(tr("Key derivation &function:"), argon2_);
    form->addRow(tr("&Memory for passphrase hash (KiB):"), memory_);
    form->addRow(tr("&Time or passes:"), time_);
    form->addRow(tr("Time &mode:"), mode_);
    form->addRow(tr("&Parallelism:"), parallelism_);
    buttons_ = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, this);
    layout->addWidget(buttons_);
    connect(buttons_, &QDialogButtonBox::accepted, this, &PpkDialog::accept);
    connect(buttons_, &QDialogButtonBox::rejected, this, &PpkDialog::reject);
    connect(version_, &QComboBox::currentIndexChanged, this, &PpkDialog::updateEnabled);
    connect(controller_, &SessionController::completed, this, [this](int operation, int error, const QString &detail) {
        if (operation != SessionController::ValidateParameters) return;
        buttons_->setEnabled(true);
        if (error == PG_OK) {
            params_ = pending_;
            QDialog::accept();
        } else {
            QMessageBox box(QMessageBox::Critical, tr("Save parameters invalid"), tr("Invalid key or file parameters."), QMessageBox::Ok, this);
            box.setDetailedText(detail);
            box.exec();
        }
    });
    updateEnabled();
}
void PpkDialog::updateEnabled() {
    const bool active = version_->currentIndex() == 1;
    QWidget *widgets[] = {argon2_, memory_, time_, parallelism_, mode_};
    for (QWidget *widget : widgets) widget->setEnabled(active);
}
void PpkDialog::accept() {
    uint32_t values[3];
    QLineEdit *edits[] = {memory_, time_, parallelism_};
    for (int i = 0; i < 3; ++i) {
        bool ok = false;
        const auto value = edits[i]->text().toULongLong(&ok);
        if (!ok || value > std::numeric_limits<uint32_t>::max()) {
            QMessageBox::warning(this, tr("PuTTYgen Warning"), tr("Enter an integer between 0 and 4294967295."));
            edits[i]->setFocus();
            return;
        }
        values[i] = uint32_t(value);
    }
    pending_ = {version_->currentIndex() == 0 ? 2u : 3u, pg_argon2(argon2_->currentIndex()),
               values[0], mode_->currentIndex() == 0, values[1], values[2]};
    buttons_->setEnabled(false);
    controller_->validateParameters(pending_);
}
