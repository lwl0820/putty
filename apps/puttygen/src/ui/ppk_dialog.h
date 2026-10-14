#pragma once
#include "core/pg_core.h"
#include <QDialog>
class QComboBox;
class QDialogButtonBox;
class SessionController;
class QLineEdit;
class PpkDialog final : public QDialog {
    Q_OBJECT
public:
    explicit PpkDialog(const pg_ppk_params &params, SessionController *controller, QWidget *parent = nullptr);
    pg_ppk_params parameters() const { return params_; }
private:
    void accept() override;
    void updateEnabled();
    pg_ppk_params params_, pending_;
    SessionController *controller_;
    QDialogButtonBox *buttons_;
    uint32_t automaticTime_ = 100, fixedPasses_ = 13;
    QComboBox *version_, *argon2_, *mode_;
    QLineEdit *memory_, *time_, *parallelism_;
};
