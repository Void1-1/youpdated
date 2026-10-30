from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

import pytest

from youpdated import cli, crypto
from youpdated.cli import main
from youpdated.config import EXAMPLE_CONFIG, ConfigError, load_config, parse_config_text
from youpdated.edit import tidy_config

PASSPHRASE = "correct horse battery staple"
WITH_PROXY = EXAMPLE_CONFIG.replace(
    "  # proxy: socks5://127.0.0.1:9050", "  proxy: socks5h://127.0.0.1:9050"
)
ADD_LEFT_PAD = "path.write_text(path.read_text().replace('- express', '- express\\n    - left-pad'))"

needs_crypto = pytest.mark.skipif(not crypto.available(), reason="cryptography is not installed")


@pytest.fixture(autouse=True)
def wide(monkeypatch):
    # long temp paths would wrap mid-message
    monkeypatch.setenv("COLUMNS", "500")


@pytest.fixture(autouse=True)
def waits(monkeypatch):
    monkeypatch.setattr(cli, "_QUICK_EDITOR", 0)


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "youpdated.yaml"
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    return path


@pytest.fixture
def locked(config, monkeypatch):
    monkeypatch.setenv(crypto.PASSPHRASE_ENV, PASSPHRASE)
    config.write_bytes(crypto.encrypt(EXAMPLE_CONFIG.encode("utf-8"), PASSPHRASE))
    return config


def use_editor(tmp_path, monkeypatch, *runs: str, real: Path | None = None) -> Path:
    script = tmp_path / "editor.py"
    log = tmp_path / "editor.json"
    script.write_text(
        "import json, os, stat, sys\n"
        "from pathlib import Path\n"
        f"log = Path({str(log)!r})\n"
        f"real = Path({str(real)!r})\n"
        "seen = json.loads(log.read_text()) if log.exists() else []\n"
        "path = Path(sys.argv[-1])\n"
        "seen.append({'path': str(path), 'text': path.read_text(encoding='utf-8'),"
        " 'env': sorted(os.environ), 'mode': stat.S_IMODE(path.stat().st_mode),"
        " 'dir_mode': stat.S_IMODE(path.parent.stat().st_mode)})\n"
        "log.write_text(json.dumps(seen))\n"
        f"exec({list(runs)!r}[len(seen) - 1])\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", f'"{sys.executable}" "{script}"')
    return log


def runs(log: Path) -> list[dict]:
    return json.loads(log.read_text()) if log.exists() else []


def answers(monkeypatch, *replies: str) -> list[str]:
    """Answer prompts with ``replies`` in turn, then as if stdin closed; returns the prompts"""
    asked: list[str] = []
    left = list(replies)

    def fake_input(prompt: str = "") -> str:
        asked.append(prompt)
        if not left:
            raise EOFError
        return left.pop(0)

    monkeypatch.setattr("builtins.input", fake_input)
    return asked


def test_an_edit_is_saved(config, tmp_path, monkeypatch, capsys):
    log = use_editor(tmp_path, monkeypatch, ADD_LEFT_PAD)
    handler = signal.getsignal(signal.SIGINT)

    assert main(["edit", "-c", str(config)]) == 0
    assert runs(log)[0]["text"] == EXAMPLE_CONFIG
    assert "left-pad" in load_config(config).sources["npm"]
    assert f"Saved changes to {config}" in capsys.readouterr().out
    assert signal.getsignal(signal.SIGINT) is handler


def test_no_change_writes_nothing_and_says_so(config, tmp_path, monkeypatch, capsys):
    use_editor(tmp_path, monkeypatch, "pass")
    before = config.stat().st_mtime_ns

    assert main(["edit", "-c", str(config)]) == 0
    out = capsys.readouterr().out
    assert "No changes." in out
    assert config.stat().st_mtime_ns == before


@needs_crypto
def test_an_encrypted_config_is_edited_as_text_and_stays_encrypted(
    locked, tmp_path, monkeypatch, capsys
):
    log = use_editor(tmp_path, monkeypatch, ADD_LEFT_PAD)

    assert main(["edit", "-c", str(locked)]) == 0
    run = runs(log)[0]
    assert run["text"] == EXAMPLE_CONFIG
    assert crypto.is_encrypted_file(locked)
    assert b"left-pad" not in locked.read_bytes()
    assert "left-pad" in load_config(locked, passphrase=PASSPHRASE).sources["npm"]
    assert "Saved encrypted changes" in capsys.readouterr().out

    # the plaintext copy is gone, and the editor never had the passphrase
    assert not Path(run["path"]).parent.exists()
    assert crypto.PASSPHRASE_ENV not in run["env"]


@needs_crypto
@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_the_plaintext_copy_is_owner_only(locked, tmp_path, monkeypatch):
    log = use_editor(tmp_path, monkeypatch, "pass")

    assert main(["edit", "-c", str(locked)]) == 0
    assert runs(log)[0]["mode"] == 0o600
    assert runs(log)[0]["dir_mode"] == 0o700


def test_a_broken_edit_can_be_edited_again_without_losing_it(config, tmp_path, monkeypatch, capsys):
    log = use_editor(
        tmp_path,
        monkeypatch,
        "path.write_text(path.read_text().replace('sources:', 'sources: [', 1))",
        "path.write_text(path.read_text().replace('sources: [', 'sources:', 1)"
        ".replace('- express', '- express\\n    - left-pad'))",
    )
    asked = answers(monkeypatch, "")  # the default is to edit again

    assert main(["edit", "-c", str(config)]) == 0
    assert "invalid YAML" in capsys.readouterr().out
    assert asked == ["edit again, save anyway, or discard? [E/s/d] "]
    # the second run opened the broken text, not the original
    assert "sources: [" in runs(log)[1]["text"]
    assert "left-pad" in load_config(config).sources["npm"]


def test_an_entry_its_source_rejects_can_be_saved_anyway(config, tmp_path, monkeypatch, capsys):
    use_editor(tmp_path, monkeypatch, "path.write_text(path.read_text() + '  git2: [x]\\n')")
    answers(monkeypatch, "s")

    assert main(["edit", "-c", str(config)]) == 0
    assert "unknown source `git2`" in capsys.readouterr().out
    assert "git2:" in config.read_text(encoding="utf-8")


@pytest.mark.parametrize("replies", [("d",), ("x", "discard"), ()])
def test_a_broken_edit_can_be_discarded(config, tmp_path, monkeypatch, capsys, replies):
    use_editor(tmp_path, monkeypatch, "path.write_text('sources: [')")
    asked = answers(monkeypatch, *replies)

    assert main(["edit", "-c", str(config)]) == 1
    assert "Nothing was written" in capsys.readouterr().out
    assert config.read_text(encoding="utf-8") == EXAMPLE_CONFIG
    assert len(asked) == max(len(replies), 1)


def test_an_editor_that_fails_writes_nothing(config, tmp_path, monkeypatch, capsys):
    use_editor(tmp_path, monkeypatch, "path.write_text('changed: true'); sys.exit(1)")

    assert main(["edit", "-c", str(config)]) == 1
    assert "exited with status 1" in capsys.readouterr().out
    assert config.read_text(encoding="utf-8") == EXAMPLE_CONFIG


def test_a_missing_editor_is_named(config, monkeypatch, capsys):
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setenv("EDITOR", "no-such-editor --wait")

    assert main(["edit", "-c", str(config)]) == 1
    assert "Editor not found: no-such-editor" in capsys.readouterr().out


@pytest.mark.parametrize("reply, saved", [("y", True), ("n", False), (None, False)])
def test_a_config_changed_while_editing_is_only_overwritten_when_asked(
    config, tmp_path, monkeypatch, reply, saved
):
    use_editor(
        tmp_path,
        monkeypatch,
        "real.write_text(real.read_text() + '# from elsewhere\\n'); " + ADD_LEFT_PAD,
        real=config,
    )
    asked = answers(monkeypatch, *([reply] if reply else []))

    assert main(["edit", "-c", str(config)]) == (0 if saved else 1)
    assert asked == ["Overwrite it with your edits? [y/N] "]
    text = config.read_text(encoding="utf-8")
    assert ("left-pad" in text) is saved
    assert ("# from elsewhere" in text) is not saved


@needs_crypto
@pytest.mark.parametrize("text", [WITH_PROXY, "sources: ["])
def test_encrypted_with_a_proxy_warns_before_any_plaintext_is_written(
    locked, tmp_path, monkeypatch, capsys, text
):
    """A config too broken to read the proxy from gets the warning too"""
    locked.write_bytes(crypto.encrypt(text.encode("utf-8"), PASSPHRASE))
    log = use_editor(tmp_path, monkeypatch, "pass")
    asked = answers(monkeypatch, "n")

    assert main(["edit", "-c", str(locked)]) == 1
    out = capsys.readouterr().out
    assert "plaintext copy" in out
    assert "Nothing was decrypted to disk" in out
    assert asked == ["Continue? [y/N] "]
    assert runs(log) == []


@needs_crypto
def test_encrypted_with_a_proxy_edits_once_confirmed(locked, tmp_path, monkeypatch):
    locked.write_bytes(crypto.encrypt(WITH_PROXY.encode("utf-8"), PASSPHRASE))
    use_editor(tmp_path, monkeypatch, ADD_LEFT_PAD)
    answers(monkeypatch, "y")

    assert main(["edit", "-c", str(locked)]) == 0
    assert "left-pad" in load_config(locked, passphrase=PASSPHRASE).sources["npm"]


@needs_crypto
def test_encrypted_without_a_proxy_does_not_ask(locked, tmp_path, monkeypatch, capsys):
    use_editor(tmp_path, monkeypatch, "pass")
    asked = answers(monkeypatch)

    assert main(["edit", "-c", str(locked)]) == 0
    assert asked == []
    assert "plaintext copy" not in capsys.readouterr().out


def test_a_plain_config_with_a_proxy_does_not_ask(config, tmp_path, monkeypatch):
    config.write_text(WITH_PROXY, encoding="utf-8")
    use_editor(tmp_path, monkeypatch, "pass")
    asked = answers(monkeypatch)

    assert main(["edit", "-c", str(config)]) == 0
    assert asked == []


def edit_while_asked(monkeypatch, log: Path, *replies: str | None) -> list[str]:
    """How :func:`answers`, but each reply first edits the copy, as in a window still open"""
    asked: list[str] = []
    left = list(replies)

    def fake_input(prompt: str = "") -> str:
        asked.append(prompt)
        draft = Path(runs(log)[-1]["path"])
        text = draft.read_text(encoding="utf-8")
        draft.write_text(text.replace("- express", "- express\n    - left-pad"), encoding="utf-8")
        reply = left.pop(0) if left else None
        if reply is None:
            raise KeyboardInterrupt
        return reply

    monkeypatch.setattr("builtins.input", fake_input)
    return asked


def test_an_editor_that_returns_at_once_is_waited_for(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_QUICK_EDITOR", 60)
    log = use_editor(tmp_path, monkeypatch, "pass")
    asked = edit_while_asked(monkeypatch, log, "")

    assert main(["edit", "-c", str(config)]) == 0
    assert asked == [cli._WHEN_SAVED]
    assert "returned right away" in capsys.readouterr().out
    assert "left-pad" in load_config(config).sources["npm"]


def test_waiting_for_a_window_editor_can_be_cancelled(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_QUICK_EDITOR", 60)
    log = use_editor(tmp_path, monkeypatch, "pass")
    edit_while_asked(monkeypatch, log, None)

    assert main(["edit", "-c", str(config)]) == 1
    assert "Nothing was written" in capsys.readouterr().out
    assert config.read_text(encoding="utf-8") == EXAMPLE_CONFIG


def test_an_editor_that_changed_the_file_is_not_waited_for(config, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_QUICK_EDITOR", 60)
    use_editor(tmp_path, monkeypatch, ADD_LEFT_PAD)
    asked = answers(monkeypatch)

    assert main(["edit", "-c", str(config)]) == 0
    assert asked == []


def test_the_default_editor_is_always_waited_for(config, tmp_path, monkeypatch, capsys):
    log = use_editor(tmp_path, monkeypatch, "pass")
    command, _ = cli._editor_command()
    monkeypatch.setattr(cli, "_editor_command", lambda *_: (command, True))
    asked = edit_while_asked(monkeypatch, log, "")

    assert main(["edit", "-c", str(config)]) == 0
    out = capsys.readouterr().out
    assert "Opened youpdated.yaml in your editor" in out
    assert "Waiting for" not in out
    assert asked == [cli._WHEN_SAVED]
    assert "left-pad" in load_config(config).sources["npm"]


def test_visual_comes_before_editor(monkeypatch):
    monkeypatch.setenv("VISUAL", "code --wait")
    monkeypatch.setenv("EDITOR", "vim")
    assert cli._editor_command() == (["code", "--wait"], False)

    monkeypatch.setenv("VISUAL", " ")
    assert cli._editor_command() == (["vim"], False)


@pytest.mark.parametrize(
    "platform, env, found, expected",
    [
        ("darwin", {}, set(), (["open", "-t"], True)),
        ("darwin", {"SSH_CONNECTION": "10.0.0.2 51234 10.0.0.1 22"}, {"nano"}, (["nano"], False)),
        ("linux", {"DISPLAY": ":0"}, {"xdg-open", "nano"}, (["xdg-open"], True)),
        ("linux", {"WAYLAND_DISPLAY": "wayland-0"}, {"xdg-open"}, (["xdg-open"], True)),
        ("linux", {"DISPLAY": ":0", "SSH_TTY": "/dev/pts/1"}, {"xdg-open"}, (["vi"], False)),
        ("linux", {"DISPLAY": ":0"}, {"nano"}, (["nano"], False)),
        ("linux", {}, {"xdg-open", "nano"}, (["nano"], False)),
        ("linux", {}, set(), (["vi"], False)),
        ("win32", {"SSH_CONNECTION": "x"}, set(), (["notepad"], False)),
    ],
)
def test_without_either_the_system_default_is_used(monkeypatch, platform, env, found, expected):
    for name in ("VISUAL", "EDITOR", "SSH_CONNECTION", "SSH_TTY", "DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli.sys, "platform", platform)
    monkeypatch.setattr(cli.shutil, "which", lambda name: f"/bin/{name}" if name in found else None)

    assert cli._editor_command() == expected


def test_the_config_editor_comes_first(config, tmp_path, monkeypatch):
    log = use_editor(tmp_path, monkeypatch, ADD_LEFT_PAD)
    configured = os.environ["EDITOR"]
    monkeypatch.setenv("VISUAL", "no-such-editor")
    config.write_text(f"editor: {json.dumps(configured)}\n" + EXAMPLE_CONFIG, encoding="utf-8")

    assert main(["edit", "-c", str(config)]) == 0
    assert len(runs(log)) == 1
    assert "left-pad" in load_config(config).sources["npm"]


def test_a_missing_config_editor_is_named(config, monkeypatch, capsys):
    monkeypatch.setattr(cli.sys, "platform", "linux")
    config.write_text("editor: no-such-editor\n" + EXAMPLE_CONFIG, encoding="utf-8")

    assert main(["edit", "-c", str(config)]) == 1
    assert "Editor not found: no-such-editor. Set `editor:`" in capsys.readouterr().out


def test_a_config_editor_can_be_a_path_with_spaces(tmp_path, monkeypatch):
    program = tmp_path / "My Editor" / "edit.exe"
    program.parent.mkdir()
    program.write_text("")
    assert cli._editor_command(str(program)) == ([str(program)], False)
    assert cli._editor_command("vim -n") == (["vim", "-n"], False)


@pytest.mark.parametrize(
    "value, expected",
    [
        ("/Applications/Zed.app", (["open", "-a", "/Applications/Zed.app"], True)),
        ("Visual Studio Code", (["open", "-a", "Visual Studio Code"], True)),
        ("code --wait", (["code", "--wait"], False)),
    ],
)
def test_a_config_editor_can_be_a_mac_app(monkeypatch, value, expected):
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/bin/code" if name == "code" else None)
    assert cli._editor_command(value) == expected


@pytest.mark.parametrize("value", [5, "", ["vim"]])
def test_the_config_editor_must_be_a_string(value):
    with pytest.raises(ConfigError, match="`editor` must be"):
        parse_config_text(f"editor: {json.dumps(value)}\nsources: {{npm: [x]}}\n")


def test_smart_quotes_and_a_bare_at_are_fixed_on_save(config, tmp_path, monkeypatch, capsys):
    log = use_editor(
        tmp_path,
        monkeypatch,
        "path.write_text(path.read_text(encoding='utf-8')"
        ".replace('- express', '- express\\n    - \u2018@types/react\u2019\\n    - @scope/x'),"
        " encoding='utf-8')",
    )

    assert main(["edit", "-c", str(config)]) == 0
    out = capsys.readouterr().out
    line = EXAMPLE_CONFIG.splitlines().index("    - express") + 3
    assert f'Fixed line {line}: @scope/x --> "@scope/x" (it needs quotes)' in out
    assert "(smart quotes)" in out
    npm = load_config(config).sources["npm"]
    assert "@types/react" in npm and "@scope/x" in npm
    assert len(runs(log)) == 1


def test_fixes_are_kept_when_editing_again(config, tmp_path, monkeypatch):
    log = use_editor(
        tmp_path,
        monkeypatch,
        "path.write_text(path.read_text(encoding='utf-8').replace('- express', '- @x\\n    - [')"
        ", encoding='utf-8')",
        "path.write_text(path.read_text(encoding='utf-8').replace('    - [\\n', ''),"
        " encoding='utf-8')",
    )
    answers(monkeypatch, "e")

    assert main(["edit", "-c", str(config)]) == 0
    assert '- "@x"' in runs(log)[1]["text"]
    assert "@x" in load_config(config).sources["npm"]


def test_an_unchanged_config_is_not_fixed(config, tmp_path, monkeypatch, capsys):
    config.write_text(EXAMPLE_CONFIG.replace('"@NASA"', "\u201c@NASA\u201d"), encoding="utf-8")
    before = config.read_text(encoding="utf-8")
    use_editor(tmp_path, monkeypatch, "pass")

    assert main(["edit", "-c", str(config)]) == 0
    assert "No changes." in capsys.readouterr().out
    assert config.read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    "text, fixed",
    [
        ("a:\n  - \u201c@NASA\u201d\n", 'a:\n  - "@NASA"\n'),
        ("a: [\u2018440\u2019, x]\n", "a: ['440', x]\n"),
        ("a: \u201csay \"hi\"\u201d\n", 'a: "say \\"hi\\""\n'),
        ("a:\n  - @NASA # nasa\n  - b\n", 'a:\n  - "@NASA" # nasa\n  - b\n'),
        ("a: [@x, `y`]\n", 'a: ["@x", "`y`"]\n'),
        ("a:\n  - @x: 1\n", 'a:\n  - "@x": 1\n'),
        ("a:\r\n  - @x\r\n", 'a:\r\n  - "@x"\r\n'),
        ("\ufeffa:\n\u00a0 b: c\u200b\n", "a:\n  b: c\n"),
    ],
)
def test_tidy_fixes_slips(text, fixed):
    assert tidy_config(text)[0] == fixed


@pytest.mark.parametrize(
    "text",
    [
        "a: Baldur\u2019s Gate\n",                    # an apostrophe
        "a: \u2018Tis\n",                             # opened, never closed
        'a: "\u201cquoted\u201d"\n',                  # quoted on purpose
        "a: |\n  @here \u201cx\u201d\n",              # a block keeps its text
        "a: [\n",                                     # broken some other way
        "a:\n\t- b\n",                                # a tab's width is a guess
    ],
)
def test_tidy_leaves_alone(text):
    assert tidy_config(text) == (text, [])
