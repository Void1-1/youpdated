"""Encrypted config and state: round-trips, tamper detection, and the CLI"""

from __future__ import annotations

import os
import sqlite3
import stat
import sys
from pathlib import Path

import pytest

from youpdated import crypto
from youpdated.cli import main
from youpdated.config import EXAMPLE_CONFIG, ConfigError, load_config
from youpdated.models import Update
from youpdated.state import State

PASSPHRASE = "correct horse battery staple" # lol
REPO_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    not crypto.available(), reason="cryptography is not installed"
)

# Windows has no POSIX permission bits: os.open()'s mode only toggles the read-only
# flag there, and access is governed by the ACL the file inherits from its directory.
# `write_private` cannot deliver 0600 on Windows, so assert it only where it is real
# rather than weakening the check everywhere.
posix_only = pytest.mark.skipif(
    os.name != "posix", reason="POSIX permission bits; Windows uses inherited ACLs"
)


def update(uid: str = "v1") -> Update:
    return Update(source="github", target="python/cpython", uid=uid, title=uid, url="")


def _no_backend():
    raise crypto.EncryptionError(crypto._INSTALL_HINT)


# container


def test_public_surface_is_importable_and_real():
    """`crypto.__all__` is documented in the README as supported"""
    missing = [name for name in crypto.__all__ if not hasattr(crypto, name)]
    assert not missing, f"__all__ names that do not exist: {missing}"


def test_the_module_does_not_pull_in_cryptography_until_used():
    """Encryption is an optional dependency and not an optional package"""
    import subprocess

    probe = (
        "import sys; from youpdated import crypto; "
        "print(any(m.startswith('cryptography') for m in sys.modules))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    assert out.stdout.strip() == "False", out.stderr


def test_round_trip():
    blob = crypto.encrypt(b"hello", PASSPHRASE)
    assert crypto.is_encrypted(blob)
    assert crypto.decrypt(blob, PASSPHRASE) == b"hello"

def test_ciphertext_does_not_contain_plaintext():
    blob = crypto.encrypt(b"python/cpython", PASSPHRASE)
    assert b"cpython" not in blob


def test_same_input_encrypts_differently():
    # Fresh salt and nonce every write, so identical configs are not identifiable.
    assert crypto.encrypt(b"x", PASSPHRASE) != crypto.encrypt(b"x", PASSPHRASE)


def test_wrong_passphrase_rejected():
    blob = crypto.encrypt(b"hello", PASSPHRASE)
    with pytest.raises(crypto.EncryptionError, match="wrong passphrase"):
        crypto.decrypt(blob, "not it")


def test_tampered_ciphertext_rejected():
    blob = bytearray(crypto.encrypt(b"hello", PASSPHRASE))
    blob[-1] ^= 0x01
    with pytest.raises(crypto.EncryptionError, match="wrong passphrase"):
        crypto.decrypt(bytes(blob), PASSPHRASE)


def test_tampered_kdf_params_rejected():
    # The header is authenticated, so the cost cannot be dialled down to 2**1.
    blob = bytearray(crypto.encrypt(b"hello", PASSPHRASE))
    blob[len(crypto.MAGIC) + 2] = 1
    with pytest.raises(crypto.EncryptionError, match="wrong passphrase"):
        crypto.decrypt(bytes(blob), PASSPHRASE)


def test_empty_passphrase_rejected():
    with pytest.raises(crypto.EncryptionError, match="must not be empty"):
        crypto.encrypt(b"hello", "")


def test_truncated_file_rejected():
    blob = crypto.encrypt(b"hello", PASSPHRASE)[:20]
    with pytest.raises(crypto.EncryptionError, match="truncated"):
        crypto.decrypt(blob, PASSPHRASE)


def test_plain_file_is_not_encrypted(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    assert not crypto.is_encrypted_file(path)
    assert not crypto.is_encrypted_file(tmp_path / "missing.yaml")


def test_write_private_creates_parents_and_leaves_no_temp_file(tmp_path):
    path = tmp_path / "nested" / "state.sqlite3"
    crypto.write_private(path, b"data")
    assert path.read_bytes() == b"data"
    assert not (tmp_path / "nested" / "state.sqlite3.tmp").exists()


@posix_only
def test_write_private_is_owner_only(tmp_path):
    path = tmp_path / "state.sqlite3"
    crypto.write_private(path, b"data")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# config


def test_encrypted_config_loads_with_passphrase(tmp_path):
    path = tmp_path / "youpdated.yaml"
    crypto.write_private(path, crypto.encrypt(EXAMPLE_CONFIG.encode(), PASSPHRASE))

    config = load_config(path, passphrase=PASSPHRASE)
    assert "github" in config.sources

    with pytest.raises(ConfigError, match="encrypted"):
        load_config(path)
    with pytest.raises(ConfigError, match="wrong passphrase"):
        load_config(path, passphrase="nope")


def test_plain_config_ignores_passphrase(tmp_path):
    path = tmp_path / "youpdated.yaml"
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    assert "github" in load_config(path, passphrase=PASSPHRASE).sources


# state


def test_encrypted_state_round_trips(tmp_path):
    path = tmp_path / "state.sqlite3"

    with State(path, passphrase=PASSPHRASE) as state:
        state.mark_seen([update("v1")])
        state.set_last_run()
    assert crypto.is_encrypted_file(path)

    with State(path, passphrase=PASSPHRASE) as state:
        assert state.seen_count() == 1
        assert not state.is_new(update("v1"))
        assert state.is_new(update("v2"))
        assert state.last_run() is not None


def test_encrypted_state_is_not_readable_as_sqlite(tmp_path):
    path = tmp_path / "state.sqlite3"
    with State(path, passphrase=PASSPHRASE) as state:
        state.mark_seen([update("v1")])

    assert b"python/cpython" not in path.read_bytes()
    with sqlite3.connect(path) as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("SELECT * FROM seen").fetchall()


def test_encrypted_state_rejects_wrong_passphrase(tmp_path):
    path = tmp_path / "state.sqlite3"
    with State(path, passphrase=PASSPHRASE) as state:
        state.mark_seen([update("v1")])

    with pytest.raises(crypto.EncryptionError, match="wrong passphrase"):
        State(path, passphrase="nope")


def test_encrypted_state_refuses_plaintext_database(tmp_path):
    path = tmp_path / "state.sqlite3"
    State(path).close()  # plain SQLite file

    with pytest.raises(crypto.EncryptionError, match="not encrypted"):
        State(path, passphrase=PASSPHRASE)


def test_read_only_run_leaves_the_file_alone(tmp_path):
    path = tmp_path / "state.sqlite3"
    with State(path, passphrase=PASSPHRASE) as state:
        state.mark_seen([update("v1")])
    before = path.read_bytes()

    with State(path, passphrase=PASSPHRASE) as state:
        state.seen_count()
    assert path.read_bytes() == before


# CLI


def test_encrypt_then_check_then_decrypt(tmp_path, monkeypatch, capsys):
    config = tmp_path / "youpdated.yaml"
    config.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    state = tmp_path / "state.sqlite3"
    State(state).close()
    monkeypatch.setenv(crypto.PASSPHRASE_ENV, PASSPHRASE)

    args = ["-c", str(config), "--state", str(state)]
    assert main(["encrypt", *args]) == 0
    assert crypto.is_encrypted_file(config)
    assert crypto.is_encrypted_file(state)

    # `check --test` unlocks the config without sending anything or rewriting state.
    frozen = state.read_bytes()
    assert main(["check", "--test", *args]) == 0
    assert state.read_bytes() == frozen

    assert main(["decrypt", *args]) == 0
    assert not crypto.is_encrypted_file(config)
    assert not crypto.is_encrypted_file(state)
    assert "sources" in config.read_text(encoding="utf-8")


def test_set_encrypted_alias(tmp_path, monkeypatch):
    config = tmp_path / "youpdated.yaml"
    config.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    monkeypatch.setenv(crypto.PASSPHRASE_ENV, PASSPHRASE)

    assert main(["set-encrypted", "-c", str(config), "--state", str(tmp_path / "s")]) == 0
    assert crypto.is_encrypted_file(config)


def test_encrypt_is_idempotent(tmp_path, monkeypatch, capsys):
    config = tmp_path / "youpdated.yaml"
    config.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    monkeypatch.setenv(crypto.PASSPHRASE_ENV, PASSPHRASE)
    args = ["-c", str(config), "--state", str(tmp_path / "s")]

    assert main(["encrypt", *args]) == 0
    blob = config.read_bytes()
    assert main(["encrypt", *args]) == 0
    assert config.read_bytes() == blob  # already locked, left untouched
    assert "Already encrypted" in capsys.readouterr().out


def test_encrypt_refuses_a_mismatched_passphrase(tmp_path, monkeypatch, capsys):
    """A half-converted setup must end up under a single passphrase."""
    config = tmp_path / "youpdated.yaml"
    config.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    state = tmp_path / "state.sqlite3"
    State(state).close()
    args = ["-c", str(config), "--state", str(state)]

    monkeypatch.setenv(crypto.PASSPHRASE_ENV, PASSPHRASE)
    assert main(["encrypt", "-c", str(config), "--state", str(tmp_path / "other")]) == 0

    monkeypatch.setenv(crypto.PASSPHRASE_ENV, "a different one")
    assert main(["encrypt", *args]) == 1
    assert "Encryption error" in capsys.readouterr().out
    assert not crypto.is_encrypted_file(state)  # nothing written under the wrong key


def test_decrypt_with_wrong_passphrase_changes_nothing(tmp_path, monkeypatch):
    config = tmp_path / "youpdated.yaml"
    config.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    state = tmp_path / "state.sqlite3"
    State(state).close()
    args = ["-c", str(config), "--state", str(state)]

    monkeypatch.setenv(crypto.PASSPHRASE_ENV, PASSPHRASE)
    assert main(["encrypt", *args]) == 0
    before = (config.read_bytes(), state.read_bytes())

    monkeypatch.setenv(crypto.PASSPHRASE_ENV, "nope")
    assert main(["decrypt", *args]) == 1
    assert (config.read_bytes(), state.read_bytes()) == before


def test_init_encrypt_never_writes_plaintext(tmp_path, monkeypatch):
    """The starter config is encrypted from the first byte, not written then converted."""
    config = tmp_path / "youpdated.yaml"
    monkeypatch.setenv(crypto.PASSPHRASE_ENV, PASSPHRASE)

    assert main(["init", "--encrypt", "-c", str(config)]) == 0
    assert crypto.is_encrypted_file(config)
    if os.name == "posix":
        assert stat.S_IMODE(config.stat().st_mode) == 0o600
    assert b"sources" not in config.read_bytes()

    # And it is a real config once unlocked.
    assert "github" in load_config(config, passphrase=PASSPHRASE).sources


def test_init_encrypt_respects_force(tmp_path, monkeypatch, capsys):
    config = tmp_path / "youpdated.yaml"
    config.write_text("sources:\n  npm: [express]\n", encoding="utf-8")
    monkeypatch.setenv(crypto.PASSPHRASE_ENV, PASSPHRASE)

    assert main(["init", "--encrypt", "-c", str(config)]) == 1
    assert "already exists" in capsys.readouterr().out
    assert not crypto.is_encrypted_file(config)

    assert main(["init", "--encrypt", "--force", "-c", str(config)]) == 0
    assert crypto.is_encrypted_file(config)


def test_init_encrypt_without_the_backend_writes_nothing(tmp_path, monkeypatch, capsys):
    config = tmp_path / "youpdated.yaml"
    monkeypatch.setattr(crypto, "_aesgcm", _no_backend)

    assert main(["init", "--encrypt", "-c", str(config)]) == 1
    assert not config.exists(), "a half-made setup is worse than none"


def test_plain_init_is_unchanged(tmp_path):
    config = tmp_path / "youpdated.yaml"
    assert main(["init", "-c", str(config)]) == 0
    assert config.read_text(encoding="utf-8") == EXAMPLE_CONFIG


def test_missing_backend_reports_the_install_hint(tmp_path, monkeypatch, capsys):
    """No passphrase prompt, and Rich must not eat the `[encryption]` extra as markup."""
    config = tmp_path / "youpdated.yaml"
    config.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    monkeypatch.setattr(crypto, "_aesgcm", _no_backend)

    assert main(["encrypt", "-c", str(config), "--state", str(tmp_path / "s")]) == 1
    assert "pip install youpdated[encryption]" in capsys.readouterr().out
    assert not crypto.is_encrypted_file(config)


def test_decrypt_when_nothing_is_encrypted(tmp_path, monkeypatch, capsys):
    config = tmp_path / "youpdated.yaml"
    config.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    monkeypatch.setenv(crypto.PASSPHRASE_ENV, PASSPHRASE)

    assert main(["decrypt", "-c", str(config), "--state", str(tmp_path / "s")]) == 0
    assert config.read_text(encoding="utf-8") == EXAMPLE_CONFIG
