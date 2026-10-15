#ifndef PAGEANT_LANGUAGE_H
#define PAGEANT_LANGUAGE_H
#include <QObject>
#include <QTranslator>
namespace pageant {
class Language final : public QObject {
    Q_OBJECT
public:
    explicit Language(QObject *parent = nullptr);
    bool english() const { return english_; }
    QString text(const char *zh, const char *en) const;
    void setEnglish(bool english);
signals:
    void changed();
private:
    bool english_ = false;
    QTranslator qt_;
};
}
#endif
