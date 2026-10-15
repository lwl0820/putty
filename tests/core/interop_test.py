"""使用独立上游工具与 OpenSSH 验证测试密钥；不处理用户密钥。"""
import argparse
import base64
import hashlib
import os
from pathlib import Path
import subprocess

parser = argparse.ArgumentParser()
parser.add_argument("--root", required=True)
parser.add_argument("--core", required=True)
parser.add_argument("--reference")
parser.add_argument("--ssh-keygen", required=True)
args = parser.parse_args()
root = Path(args.root).resolve()
root.mkdir(parents=True, exist_ok=True)
env = os.environ.copy()
env.update(HOME=str(root / "home"), USERPROFILE=str(root / "home"), TMPDIR=str(root), TEMP=str(root), TMP=str(root))
(root / "home").mkdir(exist_ok=True)

def run(*command, data=None):
    result = subprocess.run([str(c) for c in command], input=data, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise AssertionError(f"工具运行失败（{result.returncode}）：{command[0]} {result.stderr.decode(errors='replace')}")
    return result.stdout.decode("utf-8").strip()

def pub_fields(text):
    fields = text.split()
    return fields[:3] if fields and fields[0].isdigit() else fields[:2]

passfiles = {}
for label, value in (("empty", ""), ("ordinary", "test-only"), ("unicode", "口令 test-only")):
    path = root / f"pass-{label}"
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as f:
        f.write(value + "\n")
    passfiles[label] = path

for n in range(8):
    expected = (root / f"key-{n}.openssh.pub").read_text(encoding="utf-8").strip()
    fingerprints = (root / f"key-{n}.fingerprints").read_text(encoding="utf-8").splitlines()
    if n != 7:
        blob = base64.b64decode(expected.split()[1])
        assert ("SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode().rstrip("=")) in fingerprints[0]
        md5 = ":".join(f"{byte:02x}" for byte in hashlib.md5(blob).digest())
        assert md5 in fingerprints[1]
    if args.reference:
        files = [(f"key-{n}.ppk", "empty"), (f"key-{n}-encrypted.ppk", "unicode"), (f"key-{n}-v2.ppk", "ordinary")]
        if n != 7:
            files += [(f"key-{n}-auto", "empty"), (f"key-{n}-new", "empty"), (f"key-{n}-new-encrypted", "ordinary")]
        if n in (0, 1):
            files += [(f"key-{n}-sshcom", "empty"), (f"key-{n}-sshcom-encrypted", "ordinary")]
        for name, password in files:
            actual = run(args.reference, root / name, "--old-passphrase", passfiles[password], "-L")
            assert pub_fields(actual) == pub_fields(expected), name
            for flag, fingerprint in (("sha256", fingerprints[0]), ("md5", fingerprints[1])):
                actual_fp = run(args.reference, root / name, "--old-passphrase", passfiles[password], "-l", "-E", flag)
                assert (actual_fp.split()[:2] == fingerprint.split()[:2]) if n == 7 else (actual_fp == fingerprint), (name, flag, actual_fp, fingerprint)
        # 上游读取 RFC4716 保存的公钥，验证公钥按钮与展示区格式不同但密钥一致。
        actual = run(args.reference, root / f"key-{n}.pub", "-L")
        assert pub_fields(actual) == pub_fields(expected)
    if n not in (1, 6, 7):
        # 当前 OpenSSH 不支持 SSH-1/Ed448，部分版本也已经移除 DSA；这三项由固定上游验证。
        actual = run(args.ssh_keygen, "-y", "-f", root / f"key-{n}-new", "-P", "")
        assert pub_fields(actual) == pub_fields(expected)
        keyfile = root / f"key-{n}-new"
        message = root / f"message-{n}"
        message.write_bytes(b"PuTTYgen cross-platform interoperability test\n")
        signature = Path(str(message) + ".sig")
        signature.unlink(missing_ok=True)
        run(args.ssh_keygen, "-Y", "sign", "-f", keyfile, "-n", "puttygen-test", message)
        allowed = root / f"allowed-signers-{n}"
        allowed.write_text("test " + " ".join(expected.split()[:2]) + "\n", encoding="utf-8")
        run(args.ssh_keygen, "-Y", "verify", "-f", allowed, "-I", "test", "-n", "puttygen-test", "-s", signature, data=message.read_bytes())

ca = root / "test-ca"
for name in (ca, Path(str(ca) + ".pub")):
    name.unlink(missing_ok=True)
run(args.ssh_keygen, "-q", "-t", "ed25519", "-N", "", "-f", ca)
for n in (0, 2, 3, 4, 5):
    public = root / f"key-{n}.openssh.pub"
    run(args.ssh_keygen, "-q", "-s", ca, "-I", "test-certificate", "-n", "test-user", "-V", "+1h", public)
run(args.core, root, "--make-certificate", root / "key-1.ppk", ca, root / "key-1.openssh-cert.pub")
for n in (0, 1, 2, 3, 4, 5):
    cert = root / f"key-{n}.openssh-cert.pub"
    other = root / f"key-{5 if n != 5 else 0}.openssh-cert.pub"
    run(args.core, root, root / f"key-{n}.ppk", cert, other)
    if args.reference:
        info = run(args.reference, root / "certificate.ppk", "--cert-info")
        assert "test-certificate" in info
        actual = run(args.reference, root / "certificate.pub", "-L")
        assert pub_fields(actual) == pub_fields(cert.read_text(encoding="utf-8"))
        for export in (0, 1):
            actual = run(args.reference, root / f"certificate-export-{export}", "-L")
            assert pub_fields(actual) == pub_fields((root / f"key-{n}.openssh.pub").read_text(encoding="utf-8"))
print("全部算法/格式、独立指纹、签名与证书互操作通过")
