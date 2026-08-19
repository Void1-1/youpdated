"""Passphrase encryption for the config file and the state database.

Encrypts whole files with AES-256-GCM. Nothing written in plaintext: the config is parsed from a decrypted buffer and
the SQLite database is deserialized into memory, so no readable copy on disk even mid-run.

Container layout
    b"YOUPDENC"   8   magic, also how `is_encrypted` recognises a file
    version       1   container version, currently 1
    kdf           1   KDF id, currently 1 (scrypt)
    log2_n        1   scrypt cost, 2**log2_n
    r             1   scrypt block size
    p             1   scrypt parallelism
    salt         16
    nonce        12
    ciphertext  ...   AES-256-GCM over the payload, 16-byte tag appended

Importable on its own: ``from youpdated import crypto``. Everything in ``__all__`` is a
supported surface, the container format included.
"""

from __future__ import annotations

import getpass
import hashlib
import os
import stat
import sys
from contextlib import suppress
from pathlib import Path

__all__ = [
    "EncryptionError",
    "MAGIC",
    "PASSPHRASE_ENV",
    "VERSION",
    "available",
    "decrypt",
    "encrypt",
    "is_encrypted",
    "is_encrypted_file",
    "prompt_passphrase",
    "require_backend",
    "write_private",
]

MAGIC = b"YOUPDENC"
VERSION = 1
KDF_SCRYPT = 1

_SALT_LEN = 16
_NONCE_LEN = 12
_KEY_LEN = 32
_HEADER_LEN = len(MAGIC) + 5 + _SALT_LEN + _NONCE_LEN

# ~0.2s and ~67 MB to derive a key. Raising log2_n stays readable by older versions b/c the cost is stored in the header.
_LOG2_N = 16
_R = 8
_P = 1

PASSPHRASE_ENV = "YOUPDATED_PASSPHRASE"

_INSTALL_HINT = (
    "encryption needs the `cryptography` package. Install it with "
    "`pip install youpdated[encryption]`."
)


class EncryptionError(Exception):
    """Raised with a message for the user."""


def _aesgcm():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise EncryptionError(_INSTALL_HINT) from exc
    return AESGCM


def require_backend() -> None:
    """Raise the install hint"""
    _aesgcm()


def available() -> bool:
    """Whether encryption can be used in this environment."""
    try:
        require_backend()
    except EncryptionError:
        return False
    return True


def _derive(passphrase: str, salt: bytes, log2_n: int, r: int, p: int) -> bytes:
    n = 1 << log2_n
    # OpenSSL's default memory ceiling is  below what n=2**16 needs, so ask for
    # the working set plus a little slack.
    maxmem = 128 * n * r + (1 << 20)
    try:
        return hashlib.scrypt(
            passphrase.encode("utf-8"),
            salt=salt,
            n=n,
            r=r,
            p=p,
            dklen=_KEY_LEN,
            maxmem=maxmem,
        )
    except ValueError as exc:
        raise EncryptionError(f"cannot derive key: {exc}") from exc


def is_encrypted(blob: bytes) -> bool:
    return blob[: len(MAGIC)] == MAGIC


def is_encrypted_file(path: str | Path) -> bool:
    """if the file exists and carries the container magic"""
    try:
        with open(path, "rb") as handle:
            return handle.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def encrypt(payload: bytes, passphrase: str) -> bytes:
    if not passphrase:
        raise EncryptionError("passphrase must not be empty")
    aesgcm = _aesgcm()
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    header = MAGIC + bytes([VERSION, KDF_SCRYPT, _LOG2_N, _R, _P]) + salt + nonce
    key = _derive(passphrase, salt, _LOG2_N, _R, _P)
    return header + aesgcm(key).encrypt(nonce, payload, header)


def decrypt(blob: bytes, passphrase: str) -> bytes:
    """Recover the payload, or raise EncryptionError for a bad passphrase or tampering."""
    if not is_encrypted(blob):
        raise EncryptionError("not an encrypted Youpdated file")
    if len(blob) < _HEADER_LEN:
        raise EncryptionError("encrypted file is truncated")

    version, kdf, log2_n, r, p = blob[len(MAGIC) : len(MAGIC) + 5]
    if version != VERSION:
        raise EncryptionError(
            f"encrypted with container version {version}, this build understands {VERSION}. "
            "Upgrade youpdated."
        )
    if kdf != KDF_SCRYPT:
        raise EncryptionError(f"unknown key derivation id {kdf}. Upgrade youpdated.")
    if not 1 <= log2_n <= 24 or r < 1 or p < 1:
        raise EncryptionError("encrypted file has invalid key derivation parameters")

    header = blob[:_HEADER_LEN]
    nonce = blob[_HEADER_LEN - _NONCE_LEN : _HEADER_LEN]
    salt = blob[_HEADER_LEN - _NONCE_LEN - _SALT_LEN : _HEADER_LEN - _NONCE_LEN]

    aesgcm = _aesgcm()
    key = _derive(passphrase, salt, log2_n, r, p)
    try:
        return aesgcm(key).decrypt(nonce, blob[_HEADER_LEN:], header)
    except Exception as exc:  # InvalidTag, and anything else the backend raises
        raise EncryptionError("wrong passphrase, or the file has been modified") from exc


def write_private(path: str | Path, blob: bytes) -> None:
    """Replace `path` with `blob` atomically, owner-readable only."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
            with os.fdopen(fd, "wb") as handle:
                handle.write(blob)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            with suppress(OSError):
                os.unlink(tmp)
            raise
        os.replace(tmp, path)
    except OSError as exc:
        raise EncryptionError(f"cannot write {path}: {exc}") from exc


def prompt_passphrase(prompt: str = "Passphrase: ", *, confirm: bool = False) -> str:
    """Read a passphrase from $YOUPDATED_PASSPHRASE, else from the terminal.

    The environment variable allows cron jobs and scripts
    """
    from_env = os.environ.get(PASSPHRASE_ENV)
    if from_env:
        return from_env

    if not sys.stdin.isatty():
        raise EncryptionError(
            "a passphrase is required but there is no terminal to ask on. "
            f"Set ${PASSPHRASE_ENV} instead."
        )

    try:
        passphrase = getpass.getpass(prompt)
        if not passphrase:
            raise EncryptionError("passphrase must not be empty")
        if confirm and getpass.getpass("Confirm passphrase: ") != passphrase:
            raise EncryptionError("passphrases did not match")
    except (EOFError, KeyboardInterrupt) as exc:
        raise EncryptionError("cancelled") from exc
    return passphrase
