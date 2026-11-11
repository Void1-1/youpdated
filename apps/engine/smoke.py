#!/usr/bin/env python3
"""Runs a frozen engine through what a freeze can break, with a throwaway home folder"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

failures = 0


def check(name: str, passed: Any, detail: Any = "") -> None:
    global failures
    failures += not passed
    print(f"{'PASS' if passed else 'FAIL'} {name}" + (f": {detail}" if detail and not passed else ""))


class Engine:
    """`api serve`"""

    def __init__(self, exe: str, env: dict[str, str], home: str) -> None:
        self.process = subprocess.Popen(
            [exe, "api", "serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", env=env, cwd=home,
        )
        self.last = 0

    def __call__(self, op: str, **params: Any) -> dict[str, Any]:
        self.last += 1
        assert self.process.stdin and self.process.stdout
        self.process.stdin.write(json.dumps({"id": self.last, "op": op, **params}) + "\n")
        self.process.stdin.flush()
        while True:
            line = self.process.stdout.readline()
            if not line:
                assert self.process.stderr
                return {"ok": False, "error": {"code": "exited", "message": self.process.stderr.read()[-500:]}}
            message = json.loads(line)
            if message.get("id") == self.last:
                return message

    def close(self) -> int:
        self("shutdown")
        assert self.process.stdin
        self.process.stdin.close()
        return self.process.wait(30)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("engine", type=Path)
    parser.add_argument("--offline", action="store_true", help="skip the real requests")
    args = parser.parse_args()

    exe = str(args.engine.resolve())
    home = tempfile.mkdtemp(prefix="youpdated-smoke-")
    env = {
        "HOME": home, "USERPROFILE": home, "APPDATA": home, "LOCALAPPDATA": home,
        "PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "LANG": "en_US.UTF-8",
    }

    def cli(*arguments: str, **extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [exe, *arguments], capture_output=True, text=True, encoding="utf-8", input="",
            env={**env, **extra}, cwd=home, timeout=120,
        )

    def serve() -> Engine:
        return Engine(exe, env, home)

    try:
        version = cli("--version")
        check("--version", version.returncode == 0, version.stderr)

        # Rich loads its Unicode tables by name
        sources = cli("sources")
        check("sources prints its table", sources.returncode == 0 and "pypi" in sources.stdout, sources.stderr)

        engine = serve()
        hello = engine("hello")
        check("hello", hello.get("ok") and hello.get("contract"), hello)
        check("hello names the commit", hello.get("commit"), hello)
        status = engine("status")
        config = Path(status.get("config_path", ""))
        check("status", status.get("ok"), status)
        if sys.platform == "darwin":
            check("config under Application Support", "Library/Application Support/youpdated" in str(config), config)
        check("config.init", engine("config.init", self_watch=True).get("created"))
        for op in ("settings.get", "targets.list", "inbox.list", "problems.list"):
            reply = engine(op)
            check(op, reply.get("ok"), reply)
        check("shuts down cleanly", engine.close() == 0)

        if not args.offline:
            # certifi's bundle, the JSON path, then feedparser on a real feed
            config.write_text("sources:\n  pypi:\n    - packaging\n  git:\n    - repo: python/cpython\n", encoding="utf-8")
            engine = serve()
            reply = engine("check")
            check("check over TLS", reply.get("ok") and not reply.get("errors"), reply)
            check("items were stored", engine("inbox.list").get("ok"))
            engine.close()

        # socksio: nothing listens there
        config.write_text("settings:\n  privacy:\n    proxy: socks5://127.0.0.1:9\nsources:\n  pypi:\n    - packaging\n", encoding="utf-8")
        proxied = cli("check", "--no-save")
        output = proxied.stdout + proxied.stderr
        check("SOCKS support is bundled", "proxy" in output and "socksio" not in output and "ModuleNotFound" not in output, output)

        # cryptography, there and back
        config.write_text("sources:\n  pypi:\n    - packaging\n", encoding="utf-8")
        encrypted = cli("encrypt", YOUPDATED_PASSPHRASE="smoke-test")
        check("encrypt", encrypted.returncode == 0, encrypted.stdout + encrypted.stderr)
        check("config is encrypted on disk", not config.read_bytes().startswith(b"sources"))
        engine = serve()
        check("locked until unlocked", engine("status").get("locked") is True)
        wrong = engine("unlock", passphrase="wrong")
        check("wrong passphrase refused", wrong.get("error", {}).get("code") == "wrong_passphrase", wrong)
        check("unlock", engine("unlock", passphrase="smoke-test").get("ok"))
        check("reads the config once unlocked", engine("targets.list").get("ok"))
        engine.close()
    finally:
        shutil.rmtree(home, ignore_errors=True)

    print(f"\n{failures} failed" if failures else "\nsmoke: all ok")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
