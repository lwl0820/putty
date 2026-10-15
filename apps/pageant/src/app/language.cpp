#include "language.h"
#include <QCoreApplication>
#include <QLibraryInfo>
#include <QSettings>
namespace pageant {
Language::Language(QObject *parent) : QObject(parent)
{
    QSettings settings(QSettings::IniFormat, QSettings::UserScope,
                       QStringLiteral("PuTTYSuite"), QStringLiteral("Pageant"));
    setEnglish(settings.value(QStringLiteral("language"), QStringLiteral("zh_CN")) == QStringLiteral("en"));
}
QString Language::text(const char *zh, const char *en) const
{ return QString::fromUtf8(english_ ? en : zh); }
void Language::setEnglish(bool english)
{
    english_ = english;
    QCoreApplication::removeTranslator(&qt_);
    if (!english_) {
        if (qt_.load(QStringLiteral("qtbase_zh_CN"),
                     QCoreApplication::applicationDirPath() + QStringLiteral("/translations")) ||
            qt_.load(QStringLiteral("qtbase_zh_CN"), QLibraryInfo::path(QLibraryInfo::TranslationsPath)))
            QCoreApplication::installTranslator(&qt_);
    }
    QSettings settings(QSettings::IniFormat, QSettings::UserScope,
                       QStringLiteral("PuTTYSuite"), QStringLiteral("Pageant"));
    settings.setValue(QStringLiteral("language"), english_ ? QStringLiteral("en") : QStringLiteral("zh_CN"));
    emit changed();
}
}
