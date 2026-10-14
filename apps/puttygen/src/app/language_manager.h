#pragma once
#include <QObject>
#include <QHash>
#include <QTranslator>
class ResourceTranslator final : public QTranslator {
public:
    ResourceTranslator();
    bool isEmpty() const override { return translations_.isEmpty(); }
    QString translate(const char *context, const char *sourceText, const char *disambiguation = nullptr, int n = -1) const override;
private:
    QHash<QString, QString> translations_;
};
class LanguageManager final : public QObject {
    Q_OBJECT
public:
    explicit LanguageManager(QObject *parent = nullptr);
    QString language() const { return language_; }
    void setLanguage(const QString &language);
signals:
    void languageChanged();
private:
    QString language_;
    QTranslator qtTranslator_;
    ResourceTranslator translator_;
};
