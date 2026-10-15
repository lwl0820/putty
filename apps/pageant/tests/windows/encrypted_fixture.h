/* 固定 RFC8032 测试向量，测试口令 suite-passphrase；不含用户身份。 */
#ifndef PAGEANT_ENCRYPTED_FIXTURE_H
#define PAGEANT_ENCRYPTED_FIXTURE_H
static const char encryptedFixture[] = R"PPK(PuTTY-User-Key-File-3: ssh-ed25519
Encryption: aes256-cbc
Comment: Windows IPC test vector
Public-Lines: 2
AAAAC3NzaC1lZDI1NTE5AAAAINdamAGCsQq31Uv+08lkBzoO4XLz2qYjJa8CGmj3
B1Ea
Key-Derivation: Argon2id
Argon2-Memory: 1024
Argon2-Passes: 2
Argon2-Parallelism: 1
Argon2-Salt: 9360007039d6a66d126ef30c4b120027
Private-Lines: 1
CUlTVbUBhSt8RTGISGOAn+CMF4QNrbTEfS+t1sUrF4SEuDu72jKHf42Bbv/2Xj2E
Private-MAC: 6db4de523d5775d1397d870d945b3b59d4138c0e3b0a96e90f04f1a2abf7460d
)PPK";
#endif
