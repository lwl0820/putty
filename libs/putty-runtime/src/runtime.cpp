#include "putty_runtime/runtime.h"
#include <QCoreApplication>
#include <QMetaObject>
#include <QMutex>
#include <QMutexLocker>
#include <QPointer>
#include <QSocketNotifier>
#include <QThread>
#include <QTimer>
#ifdef Q_OS_WIN
#include <QWinEventNotifier>
#endif
#include <algorithm>
#include <climits>
#include <atomic>
#include <deque>
#include <map>
#include <vector>

namespace putty_runtime {
struct SharedState {
    QMutex mutex;
    Limits limits;
    std::deque<Command> commands;
    std::deque<Event> events;
    size_t commandBytes = 0, eventBytes = 0, commandHigh = 0, eventHigh = 0;
    uint64_t generation;
    bool closing = false, closed = false, eventPosted = false, capacityPending = false, wakeQueued = false;
    Worker *worker = nullptr;
    QPointer<Runtime> frontend;
};
static std::atomic<uint64_t> nextGeneration{0};

class Worker final : public QObject {
public:
    Worker(std::shared_ptr<SharedState> state, pr_module module, QByteArray options)
        : state_(std::move(state)), module_(module), options_(std::move(options)) {}
    void start()
    {
        timer_ = new QTimer(this);
        timer_->setSingleShot(true);
        connect(timer_, &QTimer::timeout, this, [this] {
            if (core_) pr_core_timers(core_);
            refreshHandles();
            wake();
        });
        const pr_host host = {isOwner, wakeHost, timerHost, watchHost, eventHost};
        const auto status = pr_core_create(&host, this, state_->generation,
            &module_, options_.constData(), size_t(options_.size()), &core_);
        options_.fill('\0');
        options_.clear();
        if (status != PR_OK) {
            Event event;
            event.generation = state_->generation;
            event.type = PR_EVENT_ERROR;
            event.status = status;
            enqueue(std::move(event), true);
            finish();
            return;
        }
        Event ready;
        ready.generation = state_->generation;
        ready.type = PR_EVENT_READY;
        enqueue(std::move(ready), true);
        refreshHandles();
        wake();
    }
    void wake()
    {
        if (posted_ || stopping_) return;
        posted_ = true;
        QMetaObject::invokeMethod(this, [this] { posted_ = false; pump(); },
                                  Qt::QueuedConnection);
    }
private:
    static int isOwner(void *ctx)
    {
        return QThread::currentThread() == static_cast<Worker *>(ctx)->thread();
    }
    static void wakeHost(void *ctx) { static_cast<Worker *>(ctx)->wake(); }
    static void timerHost(void *ctx, unsigned long deadline)
    {
        auto *self = static_cast<Worker *>(ctx);
        self->timer_->start(std::max(1, pr_core_timer_delay(deadline, pr_core_ticks())));
    }
    static void watchHost(void *ctx, int fd, int mask, uint64_t registration)
    {
        static_cast<Worker *>(ctx)->watch(fd, mask, registration);
    }
    static pr_status eventHost(void *ctx, const pr_event *source)
    {
        auto *self = static_cast<Worker *>(ctx);
        QMutexLocker lock(&self->state_->mutex);
        /* 先检查当前容量，再触碰调用方内存；复制和入队共用同一把锁。 */
        if (source->size > size_t(INT_MAX) || !self->hasCapacity(source->size, false))
            return PR_BACKPRESSURE;
        Event event;
        event.generation = source->generation;
        event.sequence = source->sequence;
        event.object = source->object_id;
        event.request = source->request_id;
        event.type = source->type;
        event.status = source->status;
        event.data = QByteArray(static_cast<const char *>(source->data), int(source->size));
        self->appendEvent(std::move(event));
        return PR_OK;
    }
    bool hasCapacity(size_t bytes, bool control) const
    {
        const size_t reserve = control ? 0 : 1;
        return state_->events.size() < state_->limits.events - reserve &&
               bytes <= state_->limits.eventBytes - state_->eventBytes;
    }
    pr_status enqueue(Event event, bool control)
    {
        QMutexLocker lock(&state_->mutex);
        if (!hasCapacity(size_t(event.data.size()), control)) return PR_BACKPRESSURE;
        appendEvent(std::move(event));
        return PR_OK;
    }
    void appendEvent(Event event)
    {
        event.sequence = ++sequence_;
        state_->eventBytes += size_t(event.data.size());
        state_->events.push_back(std::move(event));
        state_->eventHigh = std::max(state_->eventHigh, state_->events.size());
        notifyFrontend();
    }
    void notifyFrontend()
    {
        if (state_->eventPosted || !state_->frontend) return;
        state_->eventPosted = true;
        QMetaObject::invokeMethod(state_->frontend, [frontend = state_->frontend] {
            if (frontend) frontend->drainEvents();
        }, Qt::QueuedConnection);
    }
    struct Watch {
        uint64_t registration;
        QSocketNotifier *notifiers[3] = {};
    };
    struct Pending { int mask; uint64_t registration; };
    void watch(int fd, int mask, uint64_t registration)
    {
        auto old = watches_.find(fd);
        if (old != watches_.end()) {
            for (auto *notifier : old->second.notifiers) {
                if (notifier) { notifier->setEnabled(false); notifier->deleteLater(); }
            }
            watches_.erase(old);
        }
        if (!mask || stopping_) return;
        Watch item;
        item.registration = registration;
        const QSocketNotifier::Type types[] = {QSocketNotifier::Read,
            QSocketNotifier::Write, QSocketNotifier::Exception};
        for (int i = 0; i < 3; ++i) {
            const int bit = 1 << i;
            if (!(mask & bit)) continue;
            auto *notifier = new QSocketNotifier(fd, types[i], this);
            item.notifiers[i] = notifier;
            connect(notifier, &QSocketNotifier::activated, this,
                [this, fd, bit, registration] {
                    auto current = watches_.find(fd);
                    if (current == watches_.end() ||
                        current->second.registration != registration) return;
                    pending_[fd] = {pending_.count(fd) ? pending_[fd].mask | bit : bit,
                                    registration};
                    for (auto *entry : current->second.notifiers)
                        if (entry) entry->setEnabled(false);
                    wake();
                });
        }
        watches_.emplace(fd, item);
    }
    void refreshHandles()
    {
#ifdef Q_OS_WIN
        if (!core_ || stopping_) return;
        const size_t count = pr_core_handles(core_, nullptr, 0);
        std::vector<pr_handle_watch> handles(count);
        pr_core_handles(core_, handles.data(), count);
        for (auto it = handles_.begin(); it != handles_.end();) {
            const auto current = std::find_if(handles.begin(), handles.end(),
                [&entry = it->second](const pr_handle_watch &watch) {
                    return watch.registration == entry.watch.registration &&
                           watch.handle == entry.watch.handle;
                });
            if (current == handles.end()) {
                it->second.notifier->setEnabled(false);
                it->second.notifier->deleteLater();
                it = handles_.erase(it);
            } else ++it;
        }
        for (const auto watch : handles) {
            if (handles_.count(watch.registration)) continue;
            auto *notifier = new QWinEventNotifier(
                reinterpret_cast<Qt::HANDLE>(watch.handle), this);
            handles_.emplace(watch.registration, Handle{watch, notifier});
            connect(notifier, &QWinEventNotifier::activated, this,
                [this, watch, notifier] {
                    if (stopping_ || !core_) return;
                    auto entry = handles_.find(watch.registration);
                    if (entry == handles_.end() || entry->second.notifier != notifier ||
                        entry->second.watch.handle != watch.handle) return;
                    notifier->setEnabled(false);
                    /* 即使 Qt 激活已排队，C 边界仍须核对原等待注册身份。 */
                    pr_core_handle_ready(core_, watch);
                    refreshHandles();
                    entry = handles_.find(watch.registration);
                    if (entry != handles_.end()) entry->second.notifier->setEnabled(true);
                    wake();
                });
        }
#endif
    }
    void pump()
    {
        if (stopping_ || !core_) return;
        {
            QMutexLocker lock(&state_->mutex);
            if (state_->closing) { lock.unlock(); stop(); return; }
        }
        bool capacityPending;
        {
            QMutexLocker lock(&state_->mutex);
            capacityPending = state_->capacityPending;
            state_->capacityPending = false;
        }
        if (capacityPending) pr_core_event_capacity(core_);
        auto pending = std::move(pending_);
        pending_.clear();
        for (const auto &[fd, event] : pending) {
            auto current = watches_.find(fd);
            if (current == watches_.end() || current->second.registration != event.registration)
                continue;
            pr_core_fd_ready(core_, fd, event.mask, event.registration);
            current = watches_.find(fd);
            if (current != watches_.end() && current->second.registration == event.registration)
                for (auto *entry : current->second.notifiers) if (entry) entry->setEnabled(true);
        }
        const bool callbacks = pr_core_callbacks(core_, state_->limits.callbackBudget);
        if (pr_core_is_closing(core_)) { stop(); return; }
        bool commands = false;
        for (unsigned i = 0; i < 16; ++i) {
            Command command;
            {
                QMutexLocker lock(&state_->mutex);
                if (state_->closing) { lock.unlock(); stop(); return; }
                if (state_->commands.empty() || state_->events.size() >= state_->limits.events - 1)
                    break;
                command = std::move(state_->commands.front());
                state_->commands.pop_front();
                state_->commandBytes -= size_t(command.data.size());
            }
            pr_command request{command.generation, command.object, command.request,
                command.type, command.data.constData(), size_t(command.data.size()), command.argument};
            const auto status = pr_core_command(core_, &request);
            command.data.fill('\0');
            Event completion;
            completion.generation = state_->generation;
            completion.object = command.object;
            completion.request = command.request;
            completion.type = PR_EVENT_COMPLETED;
            completion.status = status;
            enqueue(std::move(completion), true);
            commands = true;
        }
        refreshHandles();
        bool queued;
        {
            QMutexLocker lock(&state_->mutex);
            queued = !state_->commands.empty() && state_->events.size() < state_->limits.events - 1;
            if (commands && state_->frontend)
                QMetaObject::invokeMethod(state_->frontend, [f = state_->frontend] {
                    if (f) emit f->capacityAvailable();
                }, Qt::QueuedConnection);
        }
        if (callbacks || queued) wake();
    }
    void stop()
    {
        stopping_ = true;
        timer_->stop();
        for (auto &[fd, watch] : watches_)
            for (auto *notifier : watch.notifiers) if (notifier) notifier->setEnabled(false);
#ifdef Q_OS_WIN
        for (auto &[registration, handle] : handles_) handle.notifier->setEnabled(false);
#endif
        pr_core_begin_close(core_);
        pr_core_destroy(core_);
        core_ = nullptr;
        pending_.clear();
        finish();
    }
    void finish()
    {
        stopping_ = true;
        {
            QMutexLocker lock(&state_->mutex);
            for (auto &command : state_->commands) command.data.fill('\0');
            state_->commands.clear();
            state_->commandBytes = 0;
            state_->closing = state_->closed = true;
            state_->worker = nullptr;
            notifyFrontend();
        }
        thread()->quit();
    }
    std::shared_ptr<SharedState> state_;
    pr_module module_;
    QByteArray options_;
    pr_core *core_ = nullptr;
    QTimer *timer_ = nullptr;
    bool posted_ = false, stopping_ = false;
    uint64_t sequence_ = 0;
    std::map<int, Watch> watches_;
    std::map<int, Pending> pending_;
#ifdef Q_OS_WIN
    struct Handle { pr_handle_watch watch; QWinEventNotifier *notifier; };
    std::map<uint64_t, Handle> handles_;
#endif
};

/* 调用方已持锁；跨线程唤醒也只保留一个 Qt 排队项。 */
static void postWake(const std::shared_ptr<SharedState> &state)
{
    if (!state->worker || state->wakeQueued) return;
    state->wakeQueued = true;
    auto *worker = state->worker;
    QMetaObject::invokeMethod(worker, [state, worker] {
        {
            QMutexLocker lock(&state->mutex);
            state->wakeQueued = false;
            if (state->worker != worker) return;
        }
        worker->wake();
    }, Qt::QueuedConnection);
}

Runtime::Runtime(const pr_module &module, QByteArray options, Limits limits, QObject *parent)
    : QObject(parent), state_(std::make_shared<SharedState>())
{
    limits.commands = std::max(size_t(1), limits.commands);
    limits.events = std::max(size_t(2), limits.events);
    limits.callbackBudget = std::max(1U, limits.callbackBudget);
    state_->limits = limits;
    state_->generation = ++nextGeneration;
    state_->frontend = this;
    auto *thread = new QThread;
    auto *worker = new Worker(state_, module, std::move(options));
    state_->worker = worker;
    worker->moveToThread(thread);
    connect(thread, &QThread::started, worker, [worker] { worker->start(); });
    connect(thread, &QThread::finished, worker, &QObject::deleteLater);
    connect(thread, &QThread::finished, thread, &QObject::deleteLater);
    thread->start();
}
Runtime::~Runtime()
{
    close();
    QMutexLocker lock(&state_->mutex);
    state_->frontend = nullptr;
}
uint64_t Runtime::generation() const { return state_->generation; }
pr_status Runtime::submit(Command command)
{
    QMutexLocker lock(&state_->mutex);
    if (state_->closing) return PR_CLOSING;
    if (command.generation != state_->generation) return PR_STALE;
    if (state_->commands.size() >= state_->limits.commands ||
        size_t(command.data.size()) > state_->limits.commandBytes - state_->commandBytes)
        return PR_BACKPRESSURE;
    state_->commandBytes += size_t(command.data.size());
    state_->commands.push_back(std::move(command));
    state_->commandHigh = std::max(state_->commandHigh, state_->commands.size());
    postWake(state_);
    return PR_OK;
}
void Runtime::close()
{
    QMutexLocker lock(&state_->mutex);
    if (state_->closing) return;
    state_->closing = true;
    postWake(state_);
}
bool Runtime::isClosed() const
{
    QMutexLocker lock(&state_->mutex);
    return state_->closed;
}
size_t Runtime::commandHighWater() const
{
    QMutexLocker lock(&state_->mutex);
    return state_->commandHigh;
}
size_t Runtime::eventHighWater() const
{
    QMutexLocker lock(&state_->mutex);
    return state_->eventHigh;
}
void Runtime::drainEvents()
{
    std::deque<Event> events;
    bool closed;
    {
        QMutexLocker lock(&state_->mutex);
        events.swap(state_->events);
        state_->eventBytes = 0;
        state_->eventPosted = false;
        state_->capacityPending = true;
        closed = state_->closed;
        postWake(state_);
    }
    QPointer<Runtime> self = this;
    for (const auto &event : events) {
        emit eventReceived(event);
        if (!self) return;
    }
    if (closed) emit this->closed();
}
}
