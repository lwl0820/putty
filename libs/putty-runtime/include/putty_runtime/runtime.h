#ifndef PUTTY_RUNTIME_RUNTIME_H
#define PUTTY_RUNTIME_RUNTIME_H

#include "putty_runtime/core.h"
#include <QByteArray>
#include <QObject>
#include <memory>

namespace putty_runtime {
struct Command {
    uint64_t generation = 0, object = 0, request = 0;
    pr_command_type type = PR_COMMAND_CUSTOM;
    QByteArray data;
    int64_t argument = 0;
};
struct Event {
    uint64_t generation = 0, sequence = 0, object = 0, request = 0;
    pr_event_type type = PR_EVENT_STATE;
    pr_status status = PR_OK;
    QByteArray data;
};
struct Limits {
    size_t commands = 128, events = 128;
    size_t commandBytes = 1024 * 1024, eventBytes = 4 * 1024 * 1024;
    unsigned callbackBudget = 32;
};
struct SharedState;
class Worker;

/* 每个进程一个 Runtime；模块只在其专用核心线程访问上游对象。 */
class Runtime final : public QObject {
    Q_OBJECT
public:
    explicit Runtime(const pr_module &module, QByteArray options = {},
                     Limits limits = {}, QObject *parent = nullptr);
    ~Runtime() override;
    uint64_t generation() const;
    /* 满队列明确返回背压；调用方保留输入并在 capacityAvailable 后重试。 */
    pr_status submit(Command command);
    void close();
    bool isClosed() const;
    size_t commandHighWater() const;
    size_t eventHighWater() const;
signals:
    void eventReceived(const putty_runtime::Event &event);
    void capacityAvailable();
    void closed();
private:
    void drainEvents();
    std::shared_ptr<SharedState> state_;
    friend class Worker;
};
}
Q_DECLARE_METATYPE(putty_runtime::Event)
#endif
