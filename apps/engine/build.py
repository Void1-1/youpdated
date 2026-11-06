#!/usr/bin/env python3
"""Freezes the engine into build/engine/dist/youpdated-engine"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import venv
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
XCCONFIG = REPO / "apps/macos/Config/Youpdated.xcconfig"


def run(*command: object, **options: object) -> None:
    subprocess.run([str(part) for part in command], check=True, **options)


def lowest_macos() -> tuple[int, ...]:
    """The app's deployment target: the engine has to run wherever the app does"""
    found = re.search(r"^MACOSX_DEPLOYMENT_TARGET *= *([\d.]+)", XCCONFIG.read_text(), re.M)
    if not found:
        sys.exit(f"no MACOSX_DEPLOYMENT_TARGET in {XCCONFIG}")
    return tuple(int(part) for part in found.group(1).split("."))


def macos_binaries(folder: Path) -> list[tuple[tuple[int, ...], Path]]:
    """Each Mach-O file with the macOS version it needs"""
    binaries = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as file:
            magic = file.read(4)
        if magic not in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe"):
            continue
        shown = subprocess.run(["vtool", "-show-build", path], capture_output=True, text=True).stdout
        versions = re.findall(r"minos ([\d.]+)", shown)
        needed = max((tuple(int(part) for part in version.split(".")) for version in versions), default=(0,))
        binaries.append((needed, path))
    return binaries


def check_macos(folder: Path) -> None:
    limit = lowest_macos()
    binaries = macos_binaries(folder)
    too_new = [(needed, path) for needed, path in binaries if needed > limit]
    if too_new:
        for needed, path in too_new:
            print(f"needs macOS {'.'.join(map(str, needed))}: {path.relative_to(folder)}", file=sys.stderr)
        sys.exit(
            f"The app supports macOS {'.'.join(map(str, limit))}, but the Python used here doesn't. "
            "Use a build made for older macOS"
        )
    print(f"{len(binaries)} binaries run on macOS {'.'.join(map(str, limit))}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, help="the Python to freeze with; see README.md")
    parser.add_argument("--out", type=Path, default=REPO / "build/engine", help="default: build/engine")
    parser.add_argument("--offline", action="store_true", help="skip the smoke test's real requests")
    args = parser.parse_args()
    out: Path = args.out.resolve()

    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)

    # a venv of that Python, with the pinned packages but not youpdated itself
    environment = out / "venv"
    run(args.python, "-m", "venv", environment)
    scripts = environment / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / "python"
    run(python, "-m", "pip", "--quiet", "--disable-pip-version-check", "install", "-r", HERE / "requirements.txt")

    # a copy, so the generated _build.py stays out of the checkout
    source = out / "source"
    shutil.copytree(REPO / "youpdated", source / "youpdated", ignore=shutil.ignore_patterns("__pycache__"))
    commit = subprocess.run(
        ["git", "-C", REPO, "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    (source / "youpdated/_build.py").write_text(f'COMMIT = "{commit}"\n', encoding="utf-8")

    run(
        scripts / "pyinstaller", "--noconfirm", "--clean", "--log-level", "WARN",
        "--distpath", out / "dist", "--workpath", out / "work", HERE / "youpdated-engine.spec",
        env={**os.environ, "YOUPDATED_ENGINE_SOURCE": str(source)},
    )
    frozen = out / "dist/youpdated-engine"

    if sys.platform == "darwin":
        check_macos(frozen)

    smoke = [sys.executable, HERE / "smoke.py", frozen / "youpdated-engine"]
    run(*smoke, *(["--offline"] if args.offline else []))
    print(f"\nFrozen: {frozen}")


if __name__ == "__main__":
    main()
