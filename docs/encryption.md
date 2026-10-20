# Encryption at rest

By default the config and the history database are plain files. Anyone with your login, or unencrypted backups, can read them. To lock them behind a passphrase:

```sh
pipx install 'youpdated[encryption]'  # (or pip). pulls in `cryptography`, not needed otherwise
youpdated encrypt                     # `set-encrypted` also works
```

Starting fresh, you can skip the plaintext step. `init --encrypt` writes the starter config encrypted, so its never left unprotected:

```sh
youpdated init --encrypt
```

You can't edit in place while encrypted: run `youpdated decrypt`, edit, then `youpdated encrypt` again. That does put the config on disk in plaintext while you work on it, so `init --encrypt` isn't worth much, or you edit before encrypting the first time.
> (This will be changed once proper `youpdated add ...` commands are implemented)

Encryption asks for a passphrase twice and rewrites both files. Afterward, every command asks for it once, decrypts **into memory**, and writes back encrypted after the run.

```sh
youpdated check                       # Passphrase: ...
youpdated decrypt                     # back to plain files
```

**There is no recovery.** Don't forget your passphrase, or you'll have to recreate configs.

## Unattended runs

For cron and other unattended runs there is no terminal, so pass the passphrase in the environment:

```cron
0 9 * * * YOUPDATED_PASSPHRASE='...' /path/to/.venv/bin/youpdated check >> ~/youpdated.log 2>&1
```

That exchanges the passphrase's secrecy for the crontab's. Ensure it is more protected than your config was. Reading it from a file your shell sources or from a keychain helper is stronger.

## What this does and does not protect

Files are encrypted whole with AES-256-GCM under a key derived from the passphrase with scrypt (n=2¹⁶, r=8), and the tag is checked on every read, so a modified file is refused rather than trusted. It does **not** cover anything while Youpdated is running: the passphrase and the decrypted config are in the process's memory, and `YOUPDATED_PASSPHRASE` is visible to other processes of the same user. Nor does it erase the plaintext that was already on the disk. `encrypt` overwrites the file, but the old blocks may survive in free space until they are reused.

| | Encrypted | Plain |
| --- | --- | --- |
| `youpdated check` | asks for the passphrase | runs |
| `cat ~/.config/youpdated/config.yaml` | binary | readable |
| `sqlite3 state.sqlite3 'select * from seen'` | `file is not a database` | readable |
| File permissions | `0600` on macOS/Linux, see below on Windows | as created |

**On Windows the file mode does nothing.** There are no POSIX permission bits, so the `0600` the writer asks for is ignored apart from the read-only flag, and access is decided by the ACL the file inherits from its directory. In the default location under `%LOCALAPPDATA%` that already excludes other standard users. The encryption itself is unaffected and works identically on all three platforms.

## Using it as a module

[youpdated/crypto.py](../youpdated/crypto.py)

```python
from youpdated import crypto

blob = crypto.encrypt(b"anything", "passphrase")
crypto.write_private("secret.bin", blob)          # atomic, 0600
assert crypto.decrypt(blob, "passphrase") == b"anything"
```

Everything in `crypto.__all__` is supported, the container format included: `encrypt`, `decrypt`, `is_encrypted`, `is_encrypted_file`, `write_private`, `prompt_passphrase`, `available`, `require_backend`, `EncryptionError`, `MAGIC`, `VERSION`, `PASSPHRASE_ENV`. A `decrypt` on a file this version can't read raises an error, and `VERSION` is bumped if the layout changes.

The same passphrase goes through the two core entry points, so you can drive an encrypted setup without the CLI:

```python
from youpdated.config import load_config
from youpdated.state import State

config = load_config("youpdated.yaml", passphrase="...")
with State("state.sqlite3", passphrase="...") as state:
    print(state.seen_count())
```

`cryptography` is only imported when encryption is used, so commands that don't need it never load it:

```sh
$ python -c "from youpdated.cli import main; main(['sources'])" && python -c "
import sys, youpdated.cli
print('cryptography loaded:', any(m.startswith('cryptography') for m in sys.modules))"
cryptography loaded: False
```

Don't use encryption with untrusted sources or non-official code without verifying security yourself!
