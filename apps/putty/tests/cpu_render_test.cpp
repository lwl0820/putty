#include "render/cpu_renderer.h"
#include <QApplication>
#include <QElapsedTimer>
#include <QFontDatabase>
#include <QImage>
#include <QThread>
#include <atomic>
#include <cstdio>
#include <cstring>
#include <stdexcept>
#include <vector>
#include "test_diagnostics.h"

#define CHECK(value) do { if (!(value)) throw std::runtime_error(#value); } while (0)

struct Delivery {
    pt_packet header;
    QByteArray data;
};
struct Probe {
    QObject receiver;
    QThread *gui = QThread::currentThread();
    std::vector<Delivery> deliveries;
    int rejected = 0;
    std::atomic_bool finished{false};
    std::atomic<const char *> callbackFailure{nullptr};
    QString failure;
};

extern "C" pr_status pt_publish(pr_core *core, pt_packet header, const void *data,
                                 size_t size, uint64_t request)
{
    auto &probe = *reinterpret_cast<Probe *>(core);
    if (QThread::currentThread() == probe.gui || request != 0 || size > 16 * 1024 * 1024) {
        probe.callbackFailure = "发布回调的线程、请求或容量非法";
        return PR_PLATFORM_ERROR;
    }
    if (header.type == PT_FRAME && probe.rejected < 2) {
        ++probe.rejected;
        return PR_BACKPRESSURE;
    }
    /* C 边界不能向 MSVC /EHc 的调用者传播 C++ 异常。 */
    try {
        Delivery delivery{header, QByteArray(static_cast<const char *>(data), qsizetype(size))};
        if (!QMetaObject::invokeMethod(&probe.receiver,
            [&probe, delivery = std::move(delivery)]() mutable {
                if (QThread::currentThread() != probe.gui)
                    probe.failure = QStringLiteral("图像未在接收者线程投递");
                probe.deliveries.push_back(std::move(delivery));
            }, Qt::QueuedConnection)) {
            probe.callbackFailure = "无法排队投递图像";
            return PR_PLATFORM_ERROR;
        }
    } catch (...) {
        probe.callbackFailure = "发布回调发生 C++ 异常";
        return PR_PLATFORM_ERROR;
    }
    return PR_OK;
}

static void draw(const pt_draw_ops &ops, void *renderer, const wchar_t *text, int count)
{
    CHECK(ops.begin(renderer));
    CHECK(ops.width(renderer, 'A') == 1);
    CHECK(ops.width(renderer, 0x4e2d) >= 1);
    CHECK(ops.width(renderer, 0x1f642) >= 1);
    pt_style style = {0xf0e0d0, 0x102030, PT_UNDERLINE, 0};
    ops.text(renderer, 0, 0, text, count, style);
    style.flags |= PT_CURSOR_ACTIVE;
    ops.cursor(renderer, 1, 1, L"X", 1, style);
    ops.sigil(renderer, 3, 1);
    ops.end(renderer);
}

static void render(Probe &probe, pt_render_config config)
{
    terminal_test_stage("工作线程创建 CPU 渲染器");
    const auto options = pt_cpu_options(config);
    const auto &ops = options.draw;
    void *renderer = ops.create(reinterpret_cast<pr_core *>(&probe), &config);
    CHECK(renderer);
    terminal_test_stage("CPU 渲染器配置完成");
    try {
        auto invalid = config;
        invalid.columns = 241;
        CHECK(!ops.configure(renderer, &invalid));
        const wchar_t unicode[] = L"ASCII 中文 e\u0301 \U0001f642";
        terminal_test_stage("绘制首个 Unicode 帧");
        draw(ops, renderer, unicode, int(sizeof(unicode) / sizeof(*unicode)) - 1);
        CHECK(ops.revision(renderer) == 1);
        draw(ops, renderer, L"SECOND", 6);
        CHECK(ops.revision(renderer) == 2);
        ops.drained(renderer);
        draw(ops, renderer, L"THIRD", 5);
        config.generation = 2;
        config.columns = 240; config.rows = 2;
        config.dpi = 144; config.dpr_milli = 2000;
        CHECK(ops.configure(renderer, &config));
        terminal_test_stage("绘制高 DPI 宽帧");
        draw(ops, renderer, unicode, int(sizeof(unicode) / sizeof(*unicode)) - 1);
        int width = 0, height = 0;
        ops.pixels(renderer, &width, &height);
        CHECK(width > 4096 && qint64(width) * height <= 4 * 1024 * 1024);
        CHECK(ops.revision(renderer) == 4);
        ops.destroy(renderer);
    } catch (...) {
        ops.destroy(renderer);
        throw;
    }
}

int main(int argc, char **argv)
{
    terminal_test_diagnostics();
    terminal_test_stage("创建 QApplication");
    QApplication app(argc, argv);
    terminal_test_stage("枚举字体");
    const auto families = QFontDatabase::families();
    std::fprintf(stderr, "CPU 测试可用字体族：%lld\n", static_cast<long long>(families.size()));
    std::fflush(stderr);
    if (families.isEmpty()) {
        std::fputs("CPU 渲染测试没有可用字体，无法验证 Unicode 绘制\n", stderr);
        return 1;
    }
    terminal_test_stage("读取固定字体");
    Probe probe;
    pt_render_config config = {1, 80, 24, 14, 96, 1000, ""};
    const auto family = QFontDatabase::systemFont(QFontDatabase::FixedFont).family().toUtf8();
    if (size_t(family.size()) >= sizeof(config.font_family)) return 1;
    memcpy(config.font_family, family.constData(), size_t(family.size()));
    QThread *worker = QThread::create([&] {
        QString failure;
        try { render(probe, config); }
        catch (const std::exception &error) { failure = QString::fromUtf8(error.what()); }
        QMetaObject::invokeMethod(&probe.receiver, [&probe, failure] {
            if (!failure.isEmpty()) probe.failure = failure;
            probe.finished = true;
        }, Qt::QueuedConnection);
    });
    worker->start();
    terminal_test_stage("启动工作线程并处理 GUI 事件");
    QElapsedTimer timeout; timeout.start();
    while (!probe.finished && timeout.elapsed() < 10000) {
        QCoreApplication::processEvents(QEventLoop::AllEvents, 10);
        QThread::msleep(1);
    }
    if (!probe.finished) {
        std::fputs("CPU 渲染测试未在限时内完成\n", stderr);
        return 1;
    }
    worker->wait(); delete worker;
    terminal_test_stage("工作线程已完成，核对帧与度量");
    try {
        if (probe.callbackFailure)
            throw std::runtime_error(probe.callbackFailure.load());
        CHECK(probe.failure.isEmpty());
        CHECK(probe.rejected == 2);
        std::vector<Delivery> frames;
        int metrics = 0;
        for (auto &delivery : probe.deliveries) {
            if (delivery.header.type == PT_FRAME) frames.push_back(std::move(delivery));
            if (delivery.header.type == PT_METRICS) {
                ++metrics;
                CHECK(delivery.header.a > 0 && delivery.header.b > 0 && delivery.header.c >= 3);
                CHECK(!delivery.data.isEmpty());
            }
        }
        CHECK(frames.size() == 3 && metrics >= 2);
        CHECK(frames[0].header.revision == 2 && frames[1].header.revision == 3);
        CHECK(frames[0].data != frames[1].data);
        CHECK(frames[2].header.render_generation == 2 && frames[2].header.revision == 4);
        CHECK(frames[2].header.a > 4096 && frames[2].header.d == 2000);
        for (const auto &frame : frames) {
            const auto &h = frame.header;
            CHECK(h.c == h.a * 4 && frame.data.size() == qint64(h.c) * h.b);
            QImage image(reinterpret_cast<const uchar *>(frame.data.constData()), h.a, h.b, h.c,
                         QImage::Format_RGBA8888);
            CHECK(!image.isNull());
            CHECK(image.pixelColor(0, 0) != QColor(Qt::black));
        }
        std::puts("CPU 终端：工作线程 Unicode／DPI、背压保留最新完整帧和异步投递通过；未链接存储和 PRNG");
        return 0;
    } catch (const std::exception &error) {
        std::fprintf(stderr, "CPU 渲染回归失败：%s；%s\n", error.what(), qPrintable(probe.failure));
        return 1;
    }
}
