#include "cpu_renderer.h"
#include <QFont>
#include <QFontMetricsF>
#include <QGlyphRun>
#include <QHash>
#include <QImage>
#include <QPainter>
#include <QRawFont>
#include <QSet>
#include <QTextBoundaryFinder>
#include <QTextLayout>
#include <QThread>
#include <algorithm>
#include <cmath>
#include <cstring>
#include <memory>

namespace {
class Renderer {
public:
    explicit Renderer(pr_core *core) : core_(core), owner_(QThread::currentThread()) {}
    ~Renderer() { owner(); }
    bool configure(const pt_render_config &config)
    {
        owner();
        if (config.columns < 2 || config.columns > 240 || config.rows < 2 || config.rows > 120 ||
            config.font_pixels < 8 || config.font_pixels > 48 || config.dpi < 72 || config.dpi > 240 ||
            config.dpr_milli < 1000 || config.dpr_milli > 3000 ||
            !memchr(config.font_family, 0, sizeof(config.font_family))) return false;
        QFont font(QString::fromUtf8(config.font_family));
        font.setStyleHint(QFont::Monospace);
        font.setFixedPitch(true);
        font.setPixelSize(std::max(1, int(std::lround(config.font_pixels * config.dpi / 96.0))));
        QImage metricDevice(8, 8, QImage::Format_RGBA8888);
        metricDevice.setDotsPerMeterX(int(config.dpi / 0.0254));
        metricDevice.setDotsPerMeterY(int(config.dpi / 0.0254));
        const QFontMetricsF metrics(font, &metricDevice);
        const int cellWidth = std::max(1, int(std::ceil(metrics.horizontalAdvance(QLatin1Char('M')))));
        const int cellHeight = std::max(1, int(std::ceil(metrics.height())));
        const double dpr = config.dpr_milli / 1000.0;
        const int width = int(std::ceil(cellWidth * config.columns * dpr));
        const int height = int(std::ceil(cellHeight * config.rows * dpr));
        if (qint64(width) * height > 4 * 1024 * 1024) return false;
        QImage image(width, height, QImage::Format_RGBA8888);
        if (image.isNull()) return false;
        image.setDevicePixelRatio(dpr);
        image.setDotsPerMeterX(int(config.dpi / 0.0254));
        image.setDotsPerMeterY(int(config.dpi / 0.0254));
        image.fill(Qt::black);
        config_ = config; font_ = font; image_ = std::move(image); pending_ = {};
        metrics_ = std::make_unique<QFontMetricsF>(font_, &image_);
        cellWidth_ = cellWidth; cellHeight_ = cellHeight;
        widths_.clear(); families_.clear(); clusters_.clear(); misses_ = 0; metricsDirty_ = true;
        return true;
    }
    int begin()
    {
        owner();
        Q_ASSERT(!painter_);
        /* 上一帧可仍被 pending 持有；detach 先建立单线程可写的独立缓冲。 */
        image_.detach();
        painter_ = std::make_unique<QPainter>(&image_);
        painter_->setRenderHint(QPainter::TextAntialiasing);
        return painter_->isActive();
    }
    int width(uint32_t codepoint)
    {
        owner(); Q_ASSERT(painter_);
        const auto known = widths_.constFind(codepoint);
        if (known != widths_.cend()) return *known;
        const char32_t character = char32_t(codepoint);
        const QString text = QString::fromUcs4(&character, 1);
        const auto advance = metrics_->horizontalAdvance(text);
        const int width = advance > cellWidth_ * 1.5 ? 2 : 1;
        if (widths_.size() >= 2048) widths_.clear();
        widths_.insert(codepoint, width);
        ++misses_; metricsDirty_ = true;
        QTextLayout layout(text, font_, &image_);
        layout.beginLayout(); layout.createLine(); layout.endLayout();
        for (const auto &run : layout.glyphRuns())
            if (families_.size() < 16) families_.insert(run.rawFont().familyName());
        return width;
    }
    void text(int x, int y, const wchar_t *characters, int length, pt_style style)
    {
        owner(); Q_ASSERT(painter_);
        QString text = QString::fromWCharArray(characters, length);
        const int lineScale = style.line_mode ? 2 : 1;
        const int cellScale = style.flags & PT_WIDE ? 2 : 1;
        const int advance = cellWidth_ * cellScale;
        QFont font = font_;
        font.setBold(style.flags & PT_BOLD);
        font.setUnderline(style.flags & PT_UNDERLINE);
        font.setStrikeOut(style.flags & PT_STRIKE);
        painter_->save();
        painter_->setClipRect(QRectF(0, y * cellHeight_, config_.columns * cellWidth_, cellHeight_));
        painter_->translate(x * cellWidth_ * lineScale, y * cellHeight_);
        painter_->scale(lineScale, style.line_mode >= 2 ? 2 : 1);
        if (style.line_mode == 3) painter_->translate(0, -cellHeight_ / 2.0);
        QTextBoundaryFinder boundaries(QTextBoundaryFinder::Grapheme, text);
        int start = 0, column = 0;
        while (true) {
            const int end = boundaries.toNextBoundary();
            if (end < 0) break;
            const QString cluster = text.mid(start, end - start);
            if (!clusters_.contains(cluster)) {
                if (clusters_.size() >= 256) clusters_.clear();
                clusters_.insert(cluster);
                QTextLayout layout(cluster, font, &image_);
                layout.beginLayout(); layout.createLine(); layout.endLayout();
                for (const auto &run : layout.glyphRuns()) {
                    if (families_.size() < 16 && !families_.contains(run.rawFont().familyName())) {
                        families_.insert(run.rawFont().familyName()); metricsDirty_ = true;
                    }
                }
            }
            painter_->fillRect(QRectF(column * advance, 0, advance, cellHeight_), QColor::fromRgb(style.background));
            painter_->save();
            painter_->setClipRect(QRectF(column * advance, 0, advance, cellHeight_));
            painter_->translate(column * advance, 0);
            const QFontMetricsF metrics(font, &image_);
            const double actual = metrics.horizontalAdvance(cluster);
            if ((style.flags & PT_NARROW) && actual > advance) painter_->scale(advance / actual, 1);
            painter_->setFont(font);
            painter_->setPen(QColor::fromRgb(style.foreground));
            painter_->drawText(QPointF(0, metrics_->ascent()), cluster);
            painter_->restore();
            start = end; ++column;
        }
        painter_->restore();
    }
    void cursor(int x, int y, const wchar_t *characters, int length, pt_style style)
    {
        owner(); Q_ASSERT(painter_);
        if (style.flags & PT_CURSOR_ACTIVE) {
            std::swap(style.foreground, style.background);
            text(x, y, characters, length, style);
        } else {
            painter_->setPen(QColor(220, 220, 220));
            painter_->drawRect(QRectF(x * cellWidth_, y * cellHeight_, cellWidth_ - 1, cellHeight_ - 1));
        }
    }
    void sigil(int x, int y)
    {
        owner(); Q_ASSERT(painter_);
        /* 与远端文字不同的实心图形，保留上游可信提示的可见边界。 */
        painter_->fillRect(QRectF(x * cellWidth_, y * cellHeight_, 2 * cellWidth_, cellHeight_), QColor(22, 100, 62));
        painter_->setPen(Qt::white);
        painter_->drawRect(QRectF(x * cellWidth_ + 3, y * cellHeight_ + 3, cellWidth_, cellHeight_ - 6));
    }
    void end()
    {
        owner(); Q_ASSERT(painter_);
        painter_->end(); painter_.reset();
        ++revision_;
        pending_ = image_;
        flush();
    }
    void flush()
    {
        owner();
        if (metricsDirty_) {
            pt_packet metrics = {};
            metrics.type = PT_METRICS; metrics.render_generation = config_.generation;
            metrics.a = cellWidth_; metrics.b = cellHeight_; metrics.c = int(misses_);
            QStringList families(families_.begin(), families_.end());
            std::sort(families.begin(), families.end());
            const auto names = families.join(QLatin1Char('\n')).toUtf8();
            if (pt_publish(core_, metrics, names.constData(), size_t(names.size()), 0) == PR_OK) metricsDirty_ = false;
        }
        if (pending_.isNull()) return;
        pt_packet header = {};
        header.type = PT_FRAME; header.render_generation = config_.generation;
        header.revision = revision_;
        header.a = pending_.width(); header.b = pending_.height();
        header.c = pending_.bytesPerLine(); header.d = config_.dpr_milli;
        if (pt_publish(core_, header, pending_.constBits(), size_t(pending_.sizeInBytes()), 0) == PR_OK)
            pending_ = {};
    }
    void pixels(int *width, int *height)
    { owner(); *width = image_.width(); *height = image_.height(); }
    uint32_t revision() { owner(); return revision_; }
private:
    void owner() const { Q_ASSERT(QThread::currentThread() == owner_); }
    pr_core *core_;
    QThread *owner_;
    pt_render_config config_ = {};
    QFont font_;
    QImage image_, pending_;
    std::unique_ptr<QFontMetricsF> metrics_;
    std::unique_ptr<QPainter> painter_;
    QHash<uint32_t, int> widths_;
    QSet<QString> families_;
    QSet<QString> clusters_;
    int cellWidth_ = 0, cellHeight_ = 0;
    unsigned misses_ = 0;
    uint32_t revision_ = 0;
    bool metricsDirty_ = false;
};
void *create(pr_core *core, const pt_render_config *config)
{
    auto renderer = std::make_unique<Renderer>(core);
    if (!renderer->configure(*config)) return nullptr;
    return renderer.release();
}
}
pt_options pt_cpu_options(const pt_render_config &config)
{
    pt_options options = {};
    options.render = config;
    options.draw = {
        create,
        [](void *p) { delete static_cast<Renderer *>(p); },
        [](void *p, const pt_render_config *c) { return int(static_cast<Renderer *>(p)->configure(*c)); },
        [](void *p) { return static_cast<Renderer *>(p)->begin(); },
        [](void *p, int x, int y, const wchar_t *t, int n, pt_style s) { static_cast<Renderer *>(p)->text(x, y, t, n, s); },
        [](void *p, int x, int y, const wchar_t *t, int n, pt_style s) { static_cast<Renderer *>(p)->cursor(x, y, t, n, s); },
        [](void *p, int x, int y) { static_cast<Renderer *>(p)->sigil(x, y); },
        [](void *p, uint32_t c) { return static_cast<Renderer *>(p)->width(c); },
        [](void *p) { static_cast<Renderer *>(p)->end(); },
        [](void *p) { static_cast<Renderer *>(p)->flush(); },
        [](void *p, int *w, int *h) { static_cast<Renderer *>(p)->pixels(w, h); },
        [](void *p) { return static_cast<Renderer *>(p)->revision(); },
    };
    return options;
}
