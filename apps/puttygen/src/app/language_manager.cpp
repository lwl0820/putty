#include "language_manager.h"
#include <QCoreApplication>
#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLibraryInfo>
#include <QSettings>

ResourceTranslator::ResourceTranslator() {
    Q_INIT_RESOURCE(resources);
    QFile file(QStringLiteral(":/i18n/zh_CN.json"));
    if (file.open(QIODevice::ReadOnly)) {
        const auto object = QJsonDocument::fromJson(file.readAll()).object();
        for (auto it = object.begin(); it != object.end(); ++it) translations_.insert(it.key(), it.value().toString());
    }
}
QString ResourceTranslator::translate(const char *, const char *sourceText, const char *, int) const {
    return translations_.value(QString::fromUtf8(sourceText));
}
LanguageManager::LanguageManager(QObject *parent) : QObject(parent) {
    setLanguage(QSettings().value(QStringLiteral("language"), QStringLiteral("zh_CN")).toString());
}
void LanguageManager::setLanguage(const QString &language) {
    const QString selected = language == QStringLiteral("en") ? QStringLiteral("en") : QStringLiteral("zh_CN");
    if (selected == language_) return;
    QCoreApplication::removeTranslator(&translator_);
    QCoreApplication::removeTranslator(&qtTranslator_);
    language_ = selected;
    if (language_ == QStringLiteral("zh_CN")) {
        // Qt 标准控件使用官方翻译；应用翻译后安装，覆盖本程序专有文字。
        if (qtTranslator_.load(QStringLiteral("qtbase_zh_CN"), QLibraryInfo::path(QLibraryInfo::TranslationsPath)))
            QCoreApplication::installTranslator(&qtTranslator_);
        QCoreApplication::installTranslator(&translator_);
    }
    QSettings().setValue(QStringLiteral("language"), language_);
    emit languageChanged();
}
