#include "app/language_manager.h"
#include "ui/main_window.h"
#include <QApplication>
#include <QIcon>
int main(int argc, char **argv) {
    QApplication application(argc, argv);
    QCoreApplication::setOrganizationName(QStringLiteral("PuTTYgenGUI"));
    QCoreApplication::setApplicationName(QStringLiteral("PuTTYgenGUI"));
    QCoreApplication::setApplicationVersion(QStringLiteral("0.1.0"));
    application.setWindowIcon(QIcon(QStringLiteral(":/icons/puttygen-gui.png")));
    LanguageManager languages;
    MainWindow window(&languages);
    window.show();
    return application.exec();
}
