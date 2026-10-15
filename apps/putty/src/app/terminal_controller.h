#ifndef PT_TERMINAL_CONTROLLER_H
#define PT_TERMINAL_CONTROLLER_H
#include "core/pt_terminal.h"
#include "putty_runtime/runtime.h"
#include <QImage>
#include <QTimer>
#include <deque>

struct TerminalPacket { pt_packet header; QByteArray data; uint64_t request = 0; };
class TerminalController final : public QObject {
    Q_OBJECT
public:
    explicit TerminalController(pt_render_config config, QObject *parent = nullptr);
    ~TerminalController() override;
    pr_status open(const pt_connection &connection);
    pr_status sendInput(const QByteArray &input);
    pr_status sendKey(pt_key key);
    pr_status configure(pt_render_config config);
    pr_status reply(uint64_t request, int answer);
    pr_status requestScreen();
    void close();
    bool isClosed() const { return runtime_->isClosed(); }
    bool isReady() const { return ready_; }
    const QImage &frame() const { return frame_; }
    uint64_t frameGeneration() const { return frameGeneration_; }
    uint32_t frameRevision() const { return frameRevision_; }
    uint64_t renderGeneration() const { return config_.generation; }
    size_t commandHighWater() const { return runtime_->commandHighWater(); }
    size_t eventHighWater() const { return runtime_->eventHighWater(); }
    size_t inputHighWater() const { return inputHigh_; }
    size_t pendingInputBytes() const { return inputBytes_; }
    size_t inputBackpressureRetries() const { return inputRetries_; }
signals:
    void ready();
    void frameChanged();
    void packetReceived(const TerminalPacket &packet);
    void errorOccurred(const QString &message);
    void closed();
private:
    pr_status submit(pr_command_type type, QByteArray data = {}, int64_t argument = 0, uint64_t request = 0);
    void receive(const putty_runtime::Event &event);
    void pumpInput();
    void clearInput();
    pr_status rejectInput();
    putty_runtime::Runtime *runtime_;
    pt_render_config config_;
    QImage frame_;
    uint64_t frameGeneration_ = 0, nextRequest_ = 100, inputRequest_ = 0;
    uint32_t frameRevision_ = 0;
    uint64_t renderRequest_ = 0;
    pt_render_config pendingRender_ = {};
    struct InputOperation { QByteArray data; int key = 0; };
    std::deque<InputOperation> input_;
    InputOperation inFlight_;
    size_t inputBytes_ = 0, inputRetries_ = 0;
    size_t inputHigh_ = 0;
    QTimer inputRetry_;
    bool ready_ = false, closing_ = false;
};
Q_DECLARE_METATYPE(TerminalPacket)
#endif
