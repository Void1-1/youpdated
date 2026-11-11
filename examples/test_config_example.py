"""ConfigExample.yaml stays a working config that shows every source"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

from youpdated.config import parse_config_text
from youpdated.registry import all_sources

EXAMPLE = Path(__file__).parent / "ConfigExample.yaml"


def test_it_parses() -> None:
    config = parse_config_text(EXAMPLE.read_text(encoding="utf-8"), EXAMPLE)
    assert config.notify.webhooks and config.notify.commands


def test_every_built_in_source_is_shown() -> None:
    shown = set(yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))["sources"])
    built_in = {
        name for name, source in all_sources().items() if type(source).__module__.startswith("youpdated.sources")
    }
    assert built_in - shown == set(), "add these sources to ConfigExample.yaml"


def test_check_finds_no_problems(tmp_path: Path) -> None:
    # --test sends nothing; --fail-on-error exits 2 for an entry a source can't read
    result = subprocess.run(
        [sys.executable, "-m", "youpdated", "check", "--test", "--fail-on-error",
         "--config", str(EXAMPLE), "--state", str(tmp_path / "state.sqlite3")],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
