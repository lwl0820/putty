"""小补丁显式选择的 MIT 提供方；不替换 Paramiko 的认证状态机。"""
import struct

from .native import GssError, KERBEROS_OID, ssh_mic_payload
from tools.suite.windows.support import ValidationError

DER_KRB5 = b"\x06" + bytes([len(KERBEROS_OID)]) + KERBEROS_OID
GSS_EXCEPTIONS = (GssError, ValidationError)
_session = None


class Provider:
    def __init__(self, api, *, tamper_client_mic=False):
        global _session
        if _session is not None:
            raise ValidationError("每个 GSS SSH 子进程只允许一个提供方生命周期")
        self.api, self.tamper_client_mic = api, tamper_client_mic
        self.adapters = []
        self.closed = False
        _session = self

    def close(self):
        global _session
        failed = False
        for adapter in self.adapters:
            try:
                adapter.close()
            except ValidationError:
                failed = True
        self.closed = True
        _session = None
        if failed:
            raise ValidationError("GSS SSH 提供方关闭失败")

    def counts(self):
        return {"wrong_username_rejections": sum(a.rejected_username for a in self.adapters),
                "mic_rejections": sum(a.mic_rejections for a in self.adapters),
                "initiator_completed": sum(a._gss_ctxt_status for a in self.adapters),
                "acceptor_completed": sum(a._gss_srv_ctxt_status for a in self.adapters)}


def GSSAuth(auth_method, gss_deleg_creds=True):
    # Paramiko 服务端调用默认参数为 True；本提供方始终只请求 MUTUAL/INTEG。
    if _session is None or _session.closed or auth_method != "gssapi-with-mic":
        raise ValidationError("GSS SSH 提供方尚未显式初始化或认证方法不符")
    result = Adapter(_session)
    _session.adapters.append(result)
    return result


class Adapter:
    def __init__(self, provider):
        self.provider, self.context = provider, None
        self.rejected_username, self.mic_rejections = False, 0
        self._gss_ctxt_status = self._gss_srv_ctxt_status = False

    def ssh_gss_oids(self, mode="client"):
        if mode not in ("client", "server"):
            raise ValidationError("GSS SSH 机制列表模式无效")
        value = struct.pack(">I", len(DER_KRB5)) + DER_KRB5
        return value if mode == "server" else struct.pack(">I", 1) + value

    def ssh_check_mech(self, desired):
        return desired == DER_KRB5

    def ssh_accept_sec_context(self, hostname, token, username):
        if username != "suite" or hostname not in (None, "localhost"):
            self.rejected_username = True
            raise ValidationError("GSS SSH 接受端拒绝非固定测试用户名")
        if self.context is None:
            self.context = self.provider.api.context(acceptor=True)
        result = self.context.step(token)
        self._gss_srv_ctxt_status = self.context.complete
        return result or None

    def ssh_init_sec_context(self, target, desired_mech=None, username=None, recv_token=None):
        if target != "localhost" or username not in ("suite", "wrong") or desired_mech not in (None, DER_KRB5):
            raise ValidationError("GSS SSH 发起端身份、目标或机制无效")
        if self.context is None:
            self.context = self.provider.api.context(acceptor=False)
        result = self.context.step(recv_token or b"")
        self._gss_ctxt_status = self.context.complete
        return result or None

    def ssh_get_mic(self, session_id, gss_kex=False):
        if gss_kex or self.context is None or self.context.acceptor:
            raise ValidationError("GSS SSH 拒绝密钥交换或无效 MIC 状态")
        result = self.context.mic(ssh_mic_payload(session_id))
        if self.provider.tamper_client_mic:
            result = result[:-1] + bytes([result[-1] ^ 1])
        return result

    def ssh_check_mic(self, mic, session_id, username=None):
        if username != "suite" or self.context is None or not self.context.acceptor:
            raise ValidationError("GSS SSH MIC 接受端状态无效")
        try:
            self.context.verify(ssh_mic_payload(session_id), mic)
        except GssError:
            self.mic_rejections += 1
            raise

    def close(self):
        if self.context is not None:
            self.context.close()
            self.context = None
