#ifndef PAGEANT_AGENT_CODEC_H
#define PAGEANT_AGENT_CODEC_H
#include "core/pa_agent.h"
#include "putty_runtime/runtime.h"
#include <QString>
#include <QVector>
namespace pageant {
struct Identity {
    quint32 version = 0, flags = 0;
    QByteArray blob;
    QString comment, fingerprint;
};
struct Packet {
    pa_packet header{};
    QByteArray text;
    QVector<Identity> keys;
};
QByteArray input(pa_action action, quint32 flags = 0, QByteArray path = {},
                 QByteArray passphrase = {}, const Identity &key = {});
bool decode(const QByteArray &bytes, Packet &packet);
}
#endif
