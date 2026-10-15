#include "agent_controller.h"
namespace pageant {
AgentController::AgentController(pa_options options, QObject *parent)
    : QObject(parent), runtime_(pa_agent_module,
        QByteArray(reinterpret_cast<const char *>(&options), sizeof(options)))
{
    connect(&runtime_, &putty_runtime::Runtime::eventReceived, this, &AgentController::receive);
    connect(&runtime_, &putty_runtime::Runtime::capacityAvailable, this, &AgentController::pump);
    connect(&runtime_, &putty_runtime::Runtime::closed, this, [this] {
        ready_ = false; closing_ = true; closed_ = true;
        emit promptsCleared(); emit stateChanged(); emit closed();
    });
}
AgentController::~AgentController() { close(); }
bool AgentController::enqueue(pa_action action, quint32 flags, const QByteArray &path,
                              QByteArray passphrase, const Identity &key, quint64 request)
{
    auto bytes = input(action, flags, path, passphrase, key);
    passphrase.fill('\0');
    if (closing_ || bytes.isEmpty() || commands_.size() >= 1024 ||
        bytes.size() > 4 * 1024 * 1024 - pendingBytes_) {
        bytes.fill('\0'); emit failed(QString()); return false;
    }
    putty_runtime::Command command;
    command.generation = runtime_.generation(); command.object = 1;
    command.request = request ? request : ++next_;
    command.type = action == PA_REPLY ? PR_COMMAND_REPLY : PR_COMMAND_CUSTOM;
    command.data = std::move(bytes);
    pendingBytes_ += command.data.size();
    const auto id = command.request;
    commands_.insert(id, std::move(command));
    actions_.insert(id, action);
    queue_.enqueue(id);
    pump();
    return true;
}
void AgentController::pump()
{
    if (closing_ || !ready_) return;
    while (!queue_.isEmpty()) {
        const auto id = queue_.head();
        if (!commands_.contains(id)) { queue_.dequeue(); continue; }
        const auto status = runtime_.submit(commands_[id]);
        if (status == PR_BACKPRESSURE) return;
        queue_.dequeue();
        if (status != PR_OK) {
            const auto action = actions_.value(id, PA_REFRESH);
            erase(id); actions_.remove(id);
            if (action == PA_ADD) { currentAdd_ = false; addPaths_.clear(); emit stateChanged(); }
            emit failed(QString());
        }
    }
}
void AgentController::erase(quint64 id)
{
    auto found = commands_.find(id);
    if (found == commands_.end()) return;
    pendingBytes_ -= found->data.size();
    found->data.fill('\0'); commands_.erase(found);
}
void AgentController::sendNextAdd()
{
    if (closing_ || currentAdd_ || addPaths_.isEmpty()) return;
    currentAdd_ = true;
    const auto path = addPaths_.takeFirst().toUtf8();
    if (!enqueue(PA_ADD, encrypted_ ? PA_ENCRYPTED : 0, path)) currentAdd_ = false;
    emit stateChanged();
}
void AgentController::addFiles(QStringList paths, bool encrypted)
{
    if (!isReady() || adding()) return;
    addPaths_ = std::move(paths); encrypted_ = encrypted;
    sendNextAdd();
}
void AgentController::remove(const QVector<Identity> &keys)
{ for (const auto &key : keys) if (!enqueue(PA_DELETE, 0, {}, {}, key)) break; }
void AgentController::reencrypt(const QVector<Identity> &keys)
{ for (const auto &key : keys) if (!enqueue(PA_REENCRYPT, 0, {}, {}, key)) break; }
void AgentController::clear() { addPaths_.clear(); enqueue(PA_DELETE_ALL); emit stateChanged(); }
void AgentController::reencryptAll() { enqueue(PA_REENCRYPT_ALL); }
void AgentController::refresh() { enqueue(PA_REFRESH); }
bool AgentController::reply(quint64 request, QByteArray passphrase, bool cancel)
{
    const bool queued = enqueue(PA_REPLY, cancel ? PA_CANCEL : 0, {}, passphrase, {}, request);
    passphrase.fill('\0');
    return queued;
}
void AgentController::close()
{
    if (closing_) return;
    closing_ = true; ready_ = false;
    addPaths_.clear(); queue_.clear();
    for (auto &command : commands_) command.data.fill('\0');
    commands_.clear(); actions_.clear(); pendingBytes_ = 0;
    emit promptsCleared(); emit stateChanged();
    runtime_.close();
}
void AgentController::receive(const putty_runtime::Event &event)
{
    if (closing_) return;
    if (event.type == PR_EVENT_READY) { ready_ = true; emit stateChanged(); pump(); return; }
    if (event.type == PR_EVENT_ERROR) {
        fatal_ = true; emit failed(QString()); close(); return;
    }
    if (event.type == PR_EVENT_COMPLETED) {
        const auto action = actions_.value(event.request, PA_REFRESH);
        if (event.status == PR_BACKPRESSURE && commands_.contains(event.request)) {
            queue_.enqueue(event.request);
            return;
        }
        erase(event.request);
        if (event.status != PR_OK) {
            actions_.remove(event.request);
            if (action == PA_ADD) { currentAdd_ = false; sendNextAdd(); }
            if (action != PA_REPLY || event.status != PR_STALE) emit failed(QString());
            emit stateChanged();
        } else if (action == PA_REPLY || action == PA_REFRESH) actions_.remove(event.request);
        return;
    }
    Packet packet;
    if (!decode(event.data, packet)) { fatal_ = true; emit failed(QString()); close(); return; }
    switch (packet.header.kind) {
    case PA_ENDPOINT: endpoint_ = QString::fromUtf8(packet.text); emit stateChanged(); break;
    case PA_SNAPSHOT:
        if (packet.header.result == PA_SUCCESS) {
            keys_ = std::move(packet.keys); emit snapshotChanged();
        } else emit failed(QString());
        break;
    case PA_PROMPT: emit prompt(event.request, QString::fromUtf8(packet.text)); break;
    case PA_DISMISS: emit dismiss(event.request); break;
    case PA_RESULT:
        actions_.remove(event.request);
        emit completed(int(packet.header.action), int(packet.header.result), QString::fromUtf8(packet.text));
        if (packet.header.action == PA_ADD) { currentAdd_ = false; sendNextAdd(); }
        emit stateChanged();
        break;
    }
}
}
