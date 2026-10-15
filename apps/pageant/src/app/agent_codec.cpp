#include "agent_codec.h"
#include <cstring>
namespace pageant {
QByteArray input(pa_action action, quint32 flags, QByteArray path,
                 QByteArray passphrase, const Identity &key)
{
    if (path.size() > 65536 || passphrase.size() > 65536 ||
        key.blob.size() > 1024 * 1024) return {};
    pa_input value{PA_MAGIC, quint32(action), flags, key.version,
        quint32(path.size()), quint32(passphrase.size()), quint32(key.blob.size()), 0};
    QByteArray bytes(reinterpret_cast<const char *>(&value), sizeof(value));
    bytes += path;
    bytes += passphrase;
    bytes += key.blob;
    passphrase.fill('\0');
    return bytes;
}
bool decode(const QByteArray &bytes, Packet &packet)
{
    packet = {};
    if (bytes.size() < qsizetype(sizeof(pa_packet))) return false;
    memcpy(&packet.header, bytes.constData(), sizeof(pa_packet));
    const auto &h = packet.header;
    if (h.magic != PA_MAGIC || h.length != bytes.size() - sizeof(h) ||
        h.length > PA_MAX_PUBLIC_BYTES || h.kind < PA_ENDPOINT || h.kind > PA_DISMISS)
        return false;
    qsizetype offset = sizeof(h);
    if (h.kind != PA_SNAPSHOT) { packet.text = bytes.mid(offset); return true; }
    if (h.count > h.length / sizeof(pa_public_key)) return false;
    for (quint32 i = 0; i < h.count; ++i) {
        if (bytes.size() - offset < qsizetype(sizeof(pa_public_key))) return false;
        pa_public_key row;
        memcpy(&row, bytes.constData() + offset, sizeof(row));
        offset += sizeof(row);
        const quint64 total = quint64(row.blob_size) + row.comment_size + row.fingerprint_size;
        if (total > quint64(bytes.size() - offset) || (row.ssh_version != 1 && row.ssh_version != 2))
            return false;
        Identity key;
        key.version = row.ssh_version; key.flags = row.flags;
        key.blob = bytes.mid(offset, row.blob_size); offset += row.blob_size;
        key.comment = QString::fromUtf8(bytes.constData() + offset, row.comment_size);
        offset += row.comment_size;
        key.fingerprint = QString::fromUtf8(bytes.constData() + offset, row.fingerprint_size);
        offset += row.fingerprint_size;
        packet.keys.push_back(std::move(key));
    }
    return offset == bytes.size();
}
}
