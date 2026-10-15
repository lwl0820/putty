#include "terminal_controller.h"
#include "render/cpu_renderer.h"
#include <QPointer>
#include <algorithm>
#include <cstring>

TerminalController::TerminalController(pt_render_config config, QObject *parent)
    : QObject(parent), config_(config)
{
    const auto options = pt_cpu_options(config);
    putty_runtime::Limits limits;
    limits.commands = 16; limits.commandBytes = 128 * 1024;
    limits.events = 4; limits.eventBytes = 32 * 1024 * 1024;
    runtime_ = new putty_runtime::Runtime(pt_terminal_module,
        QByteArray(reinterpret_cast<const char *>(&options), sizeof(options)), limits, this);
    connect(runtime_, &putty_runtime::Runtime::eventReceived, this, &TerminalController::receive);
    connect(runtime_, &putty_runtime::Runtime::capacityAvailable, this, &TerminalController::pumpInput);
    connect(runtime_, &putty_runtime::Runtime::closed, this, [this] {
        clearInput();
        emit closed();
    });
    inputRetry_.setInterval(10);
    inputRetry_.setSingleShot(true);
    connect(&inputRetry_, &QTimer::timeout, this, &TerminalController::pumpInput);
}
TerminalController::~TerminalController()
{
    clearInput();
}
void TerminalController::clearInput()
{
    for (auto &operation : input_) operation.data.fill('\0');
    input_.clear(); inFlight_.data.fill('\0'); inFlight_ = {};
    inputBytes_ = 0; inputRequest_ = 0;
}
pr_status TerminalController::rejectInput()
{
    emit errorOccurred(tr("输入缓冲区已满，请稍后重试。"));
    return PR_BACKPRESSURE;
}
pr_status TerminalController::submit(pr_command_type type, QByteArray data, int64_t argument, uint64_t request)
{
    if (closing_) return PR_CLOSING;
    putty_runtime::Command command;
    command.generation = runtime_->generation(); command.object = 1;
    command.type = type; command.data = std::move(data); command.argument = argument; command.request = request;
    return runtime_->submit(std::move(command));
}
pr_status TerminalController::open(const pt_connection &connection)
{ return submit(PR_COMMAND_OPEN, QByteArray(reinterpret_cast<const char *>(&connection), sizeof(connection))); }
pr_status TerminalController::sendInput(const QByteArray &input)
{
    if (closing_) return PR_CLOSING;
    if (input.isEmpty()) return PR_OK;
    const bool append = !input_.empty() && !input_.back().key;
    if (size_t(input.size()) > 65536 - inputBytes_ ||
        (!append && input_.size() + (inputRequest_ ? 1 : 0) >= 256)) return rejectInput();
    if (append) input_.back().data.append(input);
    else input_.push_back({QByteArray(input.constData(), input.size()), 0});
    inputBytes_ += size_t(input.size());
    inputHigh_ = std::max(inputHigh_, pendingInputBytes());
    pumpInput();
    return PR_OK;
}
void TerminalController::pumpInput()
{
    if (closing_ || !ready_ || inputRequest_ || input_.empty()) return;
    const auto &operation = input_.front();
    const auto chunk = operation.key ? QByteArray(reinterpret_cast<const char *>(&operation.key), sizeof(operation.key)) :
        operation.data.first(std::min(qsizetype(4096), operation.data.size()));
    const uint64_t request = ++nextRequest_;
    const auto status = submit(operation.key ? PR_COMMAND_CUSTOM : PR_COMMAND_INPUT,
                               chunk, operation.key ? PT_KEY : 0, request);
    if (status == PR_BACKPRESSURE) { ++inputRetries_; inputRetry_.start(); return; }
    if (status != PR_OK) {
        QPointer<TerminalController> guard(this);
        emit errorOccurred(tr("输入未能发送。"));
        if (guard) close();
        return;
    }
    inFlight_ = {operation.key ? QByteArray() : chunk, operation.key};
    if (operation.key || operation.data.size() == chunk.size()) input_.pop_front();
    else input_.front().data.remove(0, chunk.size());
    inputRequest_ = request;
}
pr_status TerminalController::sendKey(pt_key key)
{
    if (closing_) return PR_CLOSING;
    if (key < PT_KEY_UP || key > PT_KEY_RIGHT) return PR_ARGUMENT;
    if (inputBytes_ > 65536 - sizeof(int) || input_.size() + (inputRequest_ ? 1 : 0) >= 256)
        return rejectInput();
    input_.push_back({{}, int(key)}); inputBytes_ += sizeof(int);
    inputHigh_ = std::max(inputHigh_, inputBytes_);
    pumpInput(); return PR_OK;
}
pr_status TerminalController::configure(pt_render_config config)
{
    if (config.generation <= config_.generation) return PR_STALE;
    if (renderRequest_) return PR_BUSY;
    const auto request = ++nextRequest_;
    const auto status = submit(PR_COMMAND_CUSTOM,
        QByteArray(reinterpret_cast<const char *>(&config), sizeof(config)), PT_SET_RENDER, request);
    if (status == PR_OK) { pendingRender_ = config; renderRequest_ = request; }
    return status;
}
pr_status TerminalController::reply(uint64_t request, int answer)
{ return submit(PR_COMMAND_REPLY, {}, answer, request); }
pr_status TerminalController::requestScreen() { return submit(PR_COMMAND_CUSTOM, {}, PT_GET_SCREEN); }
void TerminalController::close()
{
    closing_ = true; inputRetry_.stop(); clearInput(); runtime_->close();
}
void TerminalController::receive(const putty_runtime::Event &event)
{
    if (event.type == PR_EVENT_READY) { ready_ = true; emit ready(); return; }
    if (event.type == PR_EVENT_ERROR) {
        emit errorOccurred(QString::fromUtf8(event.data)); return;
    }
    if (event.type == PR_EVENT_COMPLETED) {
        if (renderRequest_ && renderRequest_ == event.request) {
            if (event.status == PR_OK) config_ = pendingRender_;
            renderRequest_ = 0;
        }
        if (inputRequest_ && inputRequest_ == event.request) {
            inputRequest_ = 0;
            if (event.status == PR_BACKPRESSURE && !closing_) {
                ++inputRetries_; input_.push_front(std::move(inFlight_));
            } else {
                inputBytes_ -= inFlight_.key ? sizeof(int) : size_t(inFlight_.data.size());
            }
            if (event.status != PR_OK && event.status != PR_BACKPRESSURE && !closing_) {
                QPointer<TerminalController> guard(this);
                emit errorOccurred(tr("输入未能发送。"));
                if (!guard) return;
            }
            inFlight_.data.fill('\0'); inFlight_ = {};
            if (!input_.empty() && !closing_) inputRetry_.start();
        } else if (event.status != PR_OK && event.status != PR_CLOSING && !closing_) {
            emit errorOccurred(tr("操作未能完成（%1）。").arg(int(event.status)));
        }
        return;
    }
    if (event.data.size() < qsizetype(sizeof(pt_packet))) return;
    TerminalPacket packet = {};
    memcpy(&packet.header, event.data.constData(), sizeof(packet.header));
    if (packet.header.magic != PT_PACKET_MAGIC ||
        packet.header.length != size_t(event.data.size()) - sizeof(pt_packet)) {
        emit errorOccurred(tr("收到无效的终端数据。")); return;
    }
    packet.data = event.data.sliced(sizeof(pt_packet)); packet.request = event.request;
    if (packet.header.type == PT_FRAME) {
        const auto &h = packet.header;
        if (h.render_generation < config_.generation) return;
        if (h.a < 1 || h.b < 1 || qint64(h.a) * h.b > 4 * 1024 * 1024 ||
            h.c != qint64(h.a) * 4 || qint64(h.c) * h.b != packet.data.size() ||
            h.d < 1000 || h.d > 3000) {
            emit errorOccurred(tr("收到无效的画面尺寸。")); return;
        }
        frame_ = QImage(reinterpret_cast<const uchar *>(packet.data.constData()),
            h.a, h.b, h.c, QImage::Format_RGBA8888).copy();
        frame_.setDevicePixelRatio(h.d / 1000.0);
        frameGeneration_ = h.render_generation;
        frameRevision_ = h.revision;
        emit frameChanged();
        return;
    }
    emit packetReceived(packet);
}
