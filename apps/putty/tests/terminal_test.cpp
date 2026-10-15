#include "app/terminal_controller.h"
#include "ui/terminal_window.h"
#include <QApplication>
#include <QCommandLineParser>
#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>
#include <QFontDatabase>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QThread>
#include <cstdio>
#include <cstring>
#include <functional>
#include <stdexcept>
#ifdef PUTTY_TERMINAL_WINDOWS_GUARDED
#include "windows/hive_bootstrap.h"
#include <QSettings>
#endif

#ifdef PUTTY_TERMINAL_WINDOWS_GUARDED
#define CHECK(value) do { if (!(value)) { \
    std::fprintf(stderr, "终端测试检查失败：行=%d\n", __LINE__); std::fflush(stderr); \
    throw std::runtime_error(#value); } } while (0)
static void guardedStage(const char *stage)
{
    std::fprintf(stderr, "终端测试阶段：%s\n", stage); std::fflush(stderr);
}
#else
#define CHECK(value) do { if (!(value)) throw std::runtime_error(#value); } while (0)
#endif
static bool until(const std::function<bool()> &predicate, int timeout = 15000)
{
    QElapsedTimer clock; clock.start();
    while (!predicate() && clock.elapsed() < timeout) {
        QCoreApplication::processEvents(QEventLoop::AllEvents, 10); QThread::msleep(1);
    }
    return predicate();
}
static void copy(char *target, size_t size, const QString &text)
{
    const auto bytes = text.toUtf8(); CHECK(size_t(bytes.size()) < size);
    memcpy(target, bytes.constData(), size_t(bytes.size()));
}
static pt_render_config rendering(uint64_t generation = 1)
{
#ifdef PUTTY_TERMINAL_WINDOWS_GUARDED
    pt_render_config config = {generation, 80, 24, 14, 96, 1000, "Consolas"};
#else
    pt_render_config config = {generation, 80, 24, 14, 96, 1000, "Liberation Mono"};
#endif
    return config;
}
struct Observation {
    bool started = false, exited = false, disconnected = false, prompt = false, workBegan = false;
    int workMilliseconds = 0, exitCode = -999;
    uint32_t screenRevision = 0;
    QByteArray screen;
    QStringList errors, families;
    uint64_t hostRequest = 0;
    int metricsQueries = 0;
    bool gssTried = false, gssSucceeded = false;
};
struct CloseAtScopeExit {
    TerminalController &controller;
    ~CloseAtScopeExit() {
        controller.close();
        /* 失败也通过事件循环等到安全清理，避免 Qt 字体系统先于核心销毁。 */
        if (!until([&] { return controller.isClosed(); }, 20000)) std::terminate();
    }
};
static void observe(TerminalController &controller, Observation &seen, const QByteArray &passphrase,
                    bool acceptHost = true, bool answerPrompt = true)
{
    QObject::connect(&controller, &TerminalController::errorOccurred, &controller,
        [&seen](const QString &error) { seen.errors.push_back(error); });
    QObject::connect(&controller, &TerminalController::packetReceived, &controller,
        [&controller, &seen, passphrase, acceptHost, answerPrompt](const TerminalPacket &packet) {
            switch (packet.header.type) {
              case PT_HOST_KEY:
                seen.hostRequest = packet.request;
                if (controller.reply(packet.request, acceptHost ? 2 : 0) != PR_OK)
                    seen.errors.push_back(QStringLiteral("主机密钥回复未入队"));
                break;
              case PT_WEAK_CRYPTO:
                if (controller.reply(packet.request, 0) != PR_OK) seen.errors.push_back(QStringLiteral("弱算法拒绝未入队"));
                break;
              case PT_AUTH_PROMPT:
                seen.prompt = true;
                if (answerPrompt && controller.sendInput(passphrase + "\r") != PR_OK)
                    seen.errors.push_back(QStringLiteral("口令回复未入队"));
                break;
              case PT_AUTH_WORK_BEGIN: seen.workBegan = true; break;
              case PT_AUTH_WORK_END: seen.workMilliseconds = packet.header.a; break;
              case PT_SESSION_STARTED: seen.started = true; break;
              case PT_REMOTE_EXIT: seen.exited = true; seen.exitCode = packet.header.a; break;
              case PT_DISCONNECTED: seen.disconnected = true; break;
              case PT_ERROR: seen.errors.push_back(QString::fromUtf8(packet.data)); break;
              case PT_LOG:
                if (packet.data.contains("Trying gssapi-with-mic")) seen.gssTried = true;
                if (packet.data.contains("GSSAPI authentication loop finished OK")) seen.gssSucceeded = true;
                break;
              case PT_SCREEN: seen.screen = packet.data; seen.screenRevision = uint32_t(packet.header.a); break;
              case PT_METRICS:
                seen.metricsQueries = std::max(seen.metricsQueries, packet.header.c);
                seen.families = QString::fromUtf8(packet.data).split('\n', Qt::SkipEmptyParts);
                break;
              default: break;
            }
        });
}
static QJsonObject workload(const pt_connection &connection, const QByteArray &passphrase, const QString &root,
                            bool gss = false)
{
    TerminalController controller(rendering()); Observation seen;
    CloseAtScopeExit cleanup{controller};
    observe(controller, seen, passphrase);
    TerminalCanvas canvas(&controller); canvas.resize(900, 600); canvas.show();
    int samples = 0, maximum = 0;
    QElapsedTimer total, previous; total.start(); previous.start();
    QTimer heartbeat;
    heartbeat.setTimerType(Qt::PreciseTimer); heartbeat.setInterval(50);
    QObject::connect(&heartbeat, &QTimer::timeout, &canvas, [&] {
        maximum = std::max(maximum, int(previous.restart())); ++samples; canvas.update();
    });
    heartbeat.start();
    CHECK(until([&] { return controller.isReady(); }));
    CHECK(controller.open(connection) == PR_OK);
    CHECK(until([&] { return seen.started || !seen.errors.isEmpty(); }, 15000));
#ifndef PUTTY_TERMINAL_WINDOWS_GUARDED
    if (!seen.errors.isEmpty()) std::fprintf(stderr, "%s\n", qPrintable(seen.errors.join('\n')));
#endif
    CHECK(seen.errors.isEmpty()); CHECK(seen.started);
    CHECK(controller.renderGeneration() == 1);
    if (gss) {
        CHECK(!seen.prompt && !seen.workBegan && seen.gssTried && seen.gssSucceeded);
    } else {
        CHECK(seen.prompt && seen.workBegan);
        CHECK(until([&] { return seen.workMilliseconds > 0; }));
        CHECK(seen.workMilliseconds >= 500);
    }
    CHECK(controller.sendInput(QByteArray("terminal-input-\xe4\xb8\xad\xe6\x96\x87\n")) == PR_OK);
    CHECK(controller.sendInput(QByteArray(65537, 'x')) == PR_BACKPRESSURE);
    seen.errors.clear();
    /* 消费者短暂停顿让完整帧队列饱和；恢复后仍必须得到最终完整屏幕。 */
    QThread::msleep(80);
    auto highDpi = rendering(2); highDpi.dpi = 144; highDpi.dpr_milli = 2000;
#ifdef PUTTY_TERMINAL_WINDOWS_GUARDED
    copy(highDpi.font_family, sizeof(highDpi.font_family), QStringLiteral("Courier New"));
#else
    copy(highDpi.font_family, sizeof(highDpi.font_family), QStringLiteral("DejaVu Sans Mono"));
#endif
    CHECK(controller.configure(highDpi) == PR_OK);
    CHECK(until([&] { return controller.frameGeneration() == 2; }));
    const QImage immutable = controller.frame();
    const auto hash = QByteArray(reinterpret_cast<const char *>(immutable.constBits()), immutable.sizeInBytes());
    CHECK(until([&] { return seen.exited || !seen.errors.isEmpty(); }, 20000));
    CHECK(seen.errors.isEmpty()); CHECK(seen.exitCode == 0);
    CHECK(total.elapsed() >= 10000); CHECK(samples >= 180); CHECK(maximum <= 250);
    CHECK(controller.requestScreen() == PR_OK);
    CHECK(until([&] { return !seen.screen.isEmpty() && controller.frameRevision() >= seen.screenRevision; }));
    CHECK(controller.renderGeneration() == 2);
    CHECK(seen.screen.contains("FINAL")); CHECK(seen.screen.contains("INPUT_OK"));
    CHECK(seen.screen.contains(QByteArray::fromHex("e4b8ade69687")));
#ifdef PUTTY_TERMINAL_WINDOWS_GUARDED
    CHECK(seen.screen.contains(QByteArray::fromHex("f09f9880")));
#endif
    CHECK(seen.metricsQueries > 0); CHECK(seen.families.size() >= 2);
    CHECK(controller.frameGeneration() == 2);
    CHECK(controller.frame().devicePixelRatio() == 2);
    CHECK(hash == QByteArray(reinterpret_cast<const char *>(immutable.constBits()), immutable.sizeInBytes()));
    CHECK(controller.commandHighWater() <= 16); CHECK(controller.eventHighWater() <= 4);
    CHECK(controller.inputHighWater() <= 65536);
    CHECK(controller.frame().save(root + "/logs/terminal-final.png"));
    const auto elapsed = total.elapsed();
    QJsonObject result{{"duration_ms", elapsed}, {"samples", samples}, {"maximum_heartbeat_ms", maximum},
        {"gss_tried", seen.gssTried}, {"gss_succeeded", seen.gssSucceeded},
        {"real_auth_work_ms", seen.workMilliseconds}, {"frame_generation", int(controller.frameGeneration())},
        {"pixel_width", controller.frame().width()}, {"pixel_height", controller.frame().height()},
        {"metric_queries", seen.metricsQueries}, {"font_families", QJsonArray::fromStringList(seen.families)},
        {"event_high_water", int(controller.eventHighWater())}, {"command_high_water", int(controller.commandHighWater())},
        {"input_high_water", int(controller.inputHighWater())}};
    heartbeat.stop(); controller.close(); CHECK(until([&] { return controller.isClosed(); }));
    return result;
}
static QJsonObject closeDuringAuthentication(pt_connection connection, const QByteArray &passphrase)
{
    TerminalController controller(rendering()); Observation seen;
    CloseAtScopeExit cleanup{controller};
    observe(controller, seen, passphrase);
    CHECK(until([&] { return controller.isReady(); }));
    CHECK(controller.open(connection) == PR_OK);
    CHECK(until([&] { return seen.workBegan || !seen.errors.isEmpty(); }));
    CHECK(seen.errors.isEmpty()); CHECK(seen.workBegan);
    int heartbeats = 0;
    QTimer heartbeat; heartbeat.setInterval(50);
    QObject::connect(&heartbeat, &QTimer::timeout, &controller, [&] { ++heartbeats; }); heartbeat.start();
    QElapsedTimer delay; delay.start();
    CHECK(until([&] { return delay.elapsed() >= 50; }));
    CHECK(controller.sendInput("cancel-pending") == PR_OK);
    CHECK(controller.sendKey(PT_KEY_UP) == PR_OK && controller.pendingInputBytes() > 0);
    QElapsedTimer call; call.start(); controller.close(); const auto callTime = call.elapsed();
    CHECK(controller.pendingInputBytes() == 0 && controller.sendKey(PT_KEY_UP) == PR_CLOSING);
    CHECK(callTime < 25); CHECK(!controller.isClosed());
    CHECK(until([&] { return controller.isClosed(); }, 15000));
    CHECK(delay.elapsed() >= 300); CHECK(heartbeats >= 4);
    return {{"close_call_ms", callTime}, {"safe_cleanup_ms", delay.elapsed()}, {"heartbeats_while_closing", heartbeats}};
}
static void rejectHostKey(const pt_connection &connection)
{
    TerminalController controller(rendering()); Observation seen;
    CloseAtScopeExit cleanup{controller};
    observe(controller, seen, {}, false, false);
    CHECK(until([&] { return controller.isReady(); }));
    CHECK(controller.open(connection) == PR_OK);
    CHECK(until([&] { return seen.hostRequest && (!seen.errors.isEmpty() || seen.disconnected); }));
    CHECK(!seen.started && !seen.prompt);
}
static void closeAtPrompt(const pt_connection &connection)
{
    TerminalController controller(rendering()); Observation seen;
    CloseAtScopeExit cleanup{controller};
    observe(controller, seen, {}, true, false);
    CHECK(until([&] { return controller.isReady(); }));
    CHECK(controller.open(connection) == PR_OK);
    CHECK(until([&] { return seen.prompt || !seen.errors.isEmpty(); }));
    CHECK(seen.prompt && seen.errors.isEmpty() && !seen.started && !seen.workBegan);
    controller.close();
    CHECK(controller.sendInput("late-secret") == PR_CLOSING);
    CHECK(until([&] { return controller.isClosed(); }));
}
static void renderBounds()
{
    auto config = rendering();
    config.columns = 240; config.rows = 2; config.dpi = 144; config.dpr_milli = 2000;
    TerminalController controller(config);
    CloseAtScopeExit cleanup{controller};
    QStringList errors;
    QObject::connect(&controller, &TerminalController::errorOccurred, &controller,
        [&](const QString &message) { errors.push_back(message); });
    CHECK(until([&] { return controller.frameGeneration() == 1 || !errors.isEmpty(); }));
    CHECK(errors.isEmpty());
    CHECK(controller.frame().width() > 4096);
    CHECK(qint64(controller.frame().width()) * controller.frame().height() <= 4 * 1024 * 1024);
}
static QJsonObject orderedInputDuringAuthentication(pt_connection connection, const QByteArray &passphrase)
{
    QByteArray expected("BEGIN");
    for (int i = 0; i < 64; ++i) expected += QByteArray("\x1b[Ax");
    for (int i = 0; i < 127; ++i) expected += QByteArray("\x1b[D");
#ifdef PUTTY_TERMINAL_WINDOWS_GUARDED
    const auto program = QStringLiteral("suite-terminal-ordered-input-v1");
#else
    const auto program = QStringLiteral(
        "/usr/bin/python3 -B -c 'import os\nexpected=bytes.fromhex(\"%1\")\ndata=b\"\"\n"
        "while len(data)<len(expected):\n chunk=os.read(0,len(expected)-len(data))\n"
        " if not chunk: raise SystemExit(8)\n data+=chunk\n"
        "os.write(1,b\"ORDER_OK\" if data==expected else b\"ORDER_BAD\")\n"
        "raise SystemExit(0 if data==expected else 9)'").arg(QString::fromLatin1(expected.toHex()));
#endif
    memset(connection.command, 0, sizeof(connection.command));
    copy(connection.command, sizeof(connection.command), program);
    TerminalController controller(rendering()); Observation seen;
    CloseAtScopeExit cleanup{controller}; observe(controller, seen, passphrase);
    CHECK(until([&] { return controller.isReady(); })); CHECK(controller.open(connection) == PR_OK);
    CHECK(until([&] { return seen.workBegan || !seen.errors.isEmpty(); }));
    CHECK(seen.workBegan && seen.errors.isEmpty());
    /* 真实 PPK 同步计算占住核心，填满其16项命令队列，强制输入提交背压。 */
    bool pressure = false;
    for (int i = 0; i < 128; ++i) {
        const auto status = controller.requestScreen();
        if (status == PR_BACKPRESSURE) { pressure = true; break; }
        CHECK(status == PR_OK);
    }
    CHECK(pressure && controller.commandHighWater() == 16);
    CHECK(controller.sendInput("BEGIN") == PR_OK);
    for (int i = 0; i < 64; ++i) {
        CHECK(controller.sendKey(PT_KEY_UP) == PR_OK);
        CHECK(controller.sendInput("x") == PR_OK);
    }
    for (int i = 0; i < 127; ++i) CHECK(controller.sendKey(PT_KEY_LEFT) == PR_OK);
    CHECK(controller.sendKey(PT_KEY_RIGHT) == PR_BACKPRESSURE);
    CHECK(seen.errors.size() == 1); seen.errors.clear();
    CHECK(controller.inputBackpressureRetries() > 0);
    CHECK(until([&] { return seen.exited || !seen.errors.isEmpty(); }, 20000));
    CHECK(seen.errors.isEmpty() && seen.started && seen.exitCode == 0);
    CHECK(controller.pendingInputBytes() == 0);
    seen.screen.clear(); CHECK(controller.requestScreen() == PR_OK);
    CHECK(until([&] { return seen.screen.contains("ORDER_OK"); }));
    CHECK(controller.renderGeneration() == 1);
    return {{"accepted_wire_bytes", expected.size()}, {"command_high_water", int(controller.commandHighWater())},
        {"input_backpressure_retries", int(controller.inputBackpressureRetries())},
        {"capacity_refusal_observed", true}, {"order_verified_by_remote", true}};
}
static QJsonObject closeDuringGSS(const pt_connection &connection, const QString &marker)
{
    TerminalController controller(rendering()); Observation seen;
    CloseAtScopeExit cleanup{controller}; observe(controller, seen, {}, true, false);
    int heartbeats = 0, maximum = 0;
    QElapsedTimer previous; previous.start();
    QTimer heartbeat; heartbeat.setInterval(50); heartbeat.setTimerType(Qt::PreciseTimer);
    QObject::connect(&heartbeat, &QTimer::timeout, &controller, [&] {
        maximum = std::max(maximum, int(previous.restart())); ++heartbeats;
    });
    heartbeat.start();
    CHECK(until([&] { return controller.isReady(); }));
    CHECK(controller.open(connection) == PR_OK);
    CHECK(until([&] { return QFileInfo::exists(marker) || !seen.errors.isEmpty(); }));
    CHECK(seen.errors.isEmpty() && QFileInfo::exists(marker));
    /* 标记来自代理已收取的真实 TGS 请求；核心阻塞时尚未入队的日志
     * 只能等安全点恢复，不能以日志投递时机冒充网络工作开始。 */
    CHECK(!seen.started && !seen.prompt);
    QElapsedTimer delay; delay.start();
    CHECK(until([&] { return delay.elapsed() >= 50; }));
    QElapsedTimer call; call.start(); controller.close(); const auto callTime = call.elapsed();
    CHECK(callTime < 25 && !controller.isClosed());
    CHECK(until([&] { return controller.isClosed(); }, 15000));
    CHECK(delay.elapsed() >= 1000 && heartbeats >= 20 && maximum <= 250);
    return {{"close_call_ms", callTime}, {"safe_cleanup_ms", delay.elapsed()},
        {"heartbeats_while_closing", heartbeats}, {"maximum_heartbeat_ms", maximum}};
}
#ifdef PUTTY_TERMINAL_WINDOWS_GUARDED
int terminal_test_entry(int argc, char **argv)
#else
int main(int argc, char **argv)
#endif
{
    const QString isolated = qEnvironmentVariable("PUTTY_TERMINAL_ISOLATED_ROOT");
    const QString canonical = QFileInfo(isolated).canonicalFilePath();
    if (canonical.isEmpty() || !canonical.startsWith(QStringLiteral(PUTTY_TERMINAL_PROJECT_TMP) + '/')) {
        std::fputs("必须通过项目私有隔离运行器启动终端测试\n", stderr); return 1;
    }
#ifdef PUTTY_TERMINAL_WINDOWS_GUARDED
    guardedStage("Qt 偏好隔离");
    QSettings::setDefaultFormat(QSettings::IniFormat);
    for (auto scope : {QSettings::UserScope, QSettings::SystemScope})
        QSettings::setPath(QSettings::IniFormat, scope, isolated + "/config");
#endif
    QApplication app(argc, argv); QFontDatabase::families();
#ifdef PUTTY_TERMINAL_WINDOWS_GUARDED
    guardedStage("Qt 初始化完成");
    /* 全部核心已安全关闭后在 QApplication 析构前接回操作权；映射从未解除。 */
    struct MainHiveOwner {
        ~MainHiveOwner() { if (suite_terminal_claim_main()) std::terminate(); }
    } mainHiveOwner;
#endif
    QCommandLineParser parser;
    for (const char *name : {"port", "key", "remote-command", "passphrase-file", "mode", "kdc-marker"})
        parser.addOption({QString::fromLatin1(name), QString(), QStringLiteral("value")});
    parser.process(app);
    try {
        pt_connection connection = {};
        copy(connection.host, sizeof(connection.host), QStringLiteral("127.0.0.1"));
        copy(connection.user, sizeof(connection.user), QStringLiteral("suite"));
        copy(connection.keyfile, sizeof(connection.keyfile), parser.value("key"));
        copy(connection.command, sizeof(connection.command), parser.value("remote-command"));
        connection.port = parser.value("port").toInt();
        const auto mode = parser.value("mode");
#ifdef PUTTY_TERMINAL_WINDOWS_GUARDED
        CHECK(mode.isEmpty());
        CHECK(parser.value("remote-command") == QStringLiteral("suite-terminal-workload-v1"));
        CHECK(connection.port >= 1 && connection.port <= 65535);
#endif
        if (mode == "render-bounds") {
            renderBounds();
            std::puts("终端：宽图像按总像素限额呈现通过");
            return 0;
        }
        const bool gss = mode.startsWith("gss-");
        QByteArray passphrase;
        if (!gss) {
            QFile secret(parser.value("passphrase-file")); CHECK(secret.open(QIODevice::ReadOnly));
            passphrase = secret.readAll().trimmed(); secret.close();
        } else CHECK(parser.value("key").isEmpty() && parser.value("passphrase-file").isEmpty());
        QJsonObject load, close, orderedInput;
        if (gss) {
            if (mode == "gss-workload") load = workload(connection, {}, isolated, true);
            else if (mode == "gss-close") close = closeDuringGSS(connection, parser.value("kdc-marker"));
            else CHECK(false);
        } else {
#ifdef PUTTY_TERMINAL_WINDOWS_GUARDED
            guardedStage("主机密钥拒绝");
#endif
            rejectHostKey(connection);
#ifdef PUTTY_TERMINAL_WINDOWS_GUARDED
            guardedStage("真实密钥负载与呈现");
#endif
            load = workload(connection, passphrase, isolated);
#ifdef PUTTY_TERMINAL_WINDOWS_GUARDED
            guardedStage("密钥计算中关闭");
#endif
            close = closeDuringAuthentication(connection, passphrase);
#ifdef PUTTY_TERMINAL_WINDOWS_GUARDED
            guardedStage("口令提示取消");
#endif
            closeAtPrompt(connection);
#ifdef PUTTY_TERMINAL_WINDOWS_GUARDED
            guardedStage("有序输入与背压");
#endif
            orderedInput = orderedInputDuringAuthentication(connection, passphrase);
        }
        passphrase.fill('\0');
        QFile output(isolated + "/logs/terminal-result.json"); CHECK(output.open(QIODevice::WriteOnly));
        QJsonObject report{{"passed", true}, {"qt", QString::fromLatin1(qVersion())},
            {"platform", QGuiApplication::platformName()}, {"workload", load}, {"authentication_close", close},
            {"ordered_input", orderedInput},
            {"host_key_rejection", !gss}, {"prompt_cancellation", !gss},
            {"windows_native_verified", false}, {"gss_verified", gss}};
#ifdef PUTTY_TERMINAL_WINDOWS_GUARDED
        report.insert("windows_guarded_ppk", true);
        report.insert("non_bmp_screen_verified", true);
        report.insert("client_build_config", QStringLiteral(PUTTY_TERMINAL_BUILD_CONFIG));
        report.insert("client_compiler", QStringLiteral(PUTTY_TERMINAL_COMPILER));
#endif
        CHECK(output.write(QJsonDocument(report).toJson()) > 0);
#ifdef PUTTY_TERMINAL_WINDOWS_GUARDED
        guardedStage("终端摘要完成");
#else
        std::puts(gss ? "终端：真实 GSS／KDC 延迟与 GUI 响应通过" :
                        "终端：真实 SSH／PPK、十秒心跳、Unicode／DPI 帧及异步关闭通过");
#endif
        return 0;
    } catch (const std::exception &error) {
#ifdef PUTTY_TERMINAL_WINDOWS_GUARDED
        (void)error;
        std::fputs("终端纵切失败：检查未通过\n", stderr); return 1;
#else
        std::fprintf(stderr, "终端纵切失败：%s\n", error.what()); return 1;
#endif
    }
}
