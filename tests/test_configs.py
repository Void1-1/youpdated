"""Named configs: `-c NAME`, $YOUPDATED_CONFIG, `init NAME`, `configs`, `check --all-configs`"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from youpdated import config, crypto
from youpdated.cli import main
from youpdated.config import (
    INIT_CONFIG,
    ConfigError,
    PrivacyConfig,
    default_config_path,
    default_state_path,
    load_config,
    parse_config_text,
)
from youpdated.configs import named, select

from .fake_source import write_items

encryption = pytest.mark.skipif(not crypto.available(), reason="cryptography is not installed")

WATCHING = "settings:\n  privacy:\n    jitter: [0, 0]\nsources:\n  fake:{entries}"


@pytest.fixture
def app(tmp_path: Path, monkeypatch) -> Path:
    """The app folder, in a temp dir, with nothing from the real machine or the checkout"""
    directory = tmp_path / "youpdated"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "tool_dir", lambda: tmp_path)
    monkeypatch.setattr(config, "config_dir", lambda: directory)
    monkeypatch.setattr(config, "data_dir", lambda: directory)
    for variable in ("YOUPDATED_CONFIG", "YOUPDATED_PASSPHRASE", "YOUPDATED_PASSPHRASE_WORK"):
        monkeypatch.delenv(variable, raising=False)
    return directory


def write_config(path: Path, *entries: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = "\n" + "".join(f"    - {entry}\n" for entry in entries) if entries else " []\n"
    path.write_text(WATCHING.format(entries=lines), encoding="utf-8")
    return path


def named_config(app: Path, name: str, *entries: str) -> Path:
    return write_config(app / "configs" / f"{name}.yaml", *entries)


def run(*argv: str) -> tuple[int, str]:
    out = io.StringIO()
    with redirect_stdout(out):
        code = main(list(argv))
    return code, out.getvalue()


# layout


def test_a_fresh_setup_uses_configs_and_history(app):
    assert default_config_path() == app / "configs" / "default.yaml"
    assert default_state_path() == app / "history" / "default-state.sqlite3"


def test_init_writes_the_default_into_configs(app):
    assert main(["init"]) == 0
    assert (app / "configs" / "default.yaml").is_file()
    assert not (app / "config.yaml").exists()


def test_a_setup_from_before_named_configs_keeps_its_files(app):
    write_config(app / "config.yaml", "alpha")
    (app / "state.sqlite3").write_bytes(b"")

    assert default_config_path() == app / "config.yaml"
    assert default_state_path() == app / "state.sqlite3"


def test_an_old_config_keeps_its_history_beside_it_before_the_first_check(app):
    """The apps' older engines write state.sqlite3 for it, so the CLI must too"""
    write_config(app / "config.yaml", "alpha")
    assert default_state_path() == app / "state.sqlite3"


def test_a_new_default_wins_over_the_old_one_with_a_warning(app):
    write_config(app / "config.yaml", "alpha")
    new = named_config(app, "default", "beta")

    assert default_config_path() == new
    loaded = load_config()
    assert loaded.path == new
    assert any("config.yaml is ignored" in warning for warning in loaded.warnings)


# selecting


def test_a_name_picks_that_config_and_its_own_history(app):
    path = named_config(app, "work")
    selected = select("work")

    assert selected.config == path
    assert selected.state == app / "history" / "work-state.sqlite3"
    assert selected.name == "work"


def test_names_are_not_case_sensitive(app):
    named_config(app, "work")
    assert select("Work").name == "work"


def test_a_path_is_used_as_before_with_the_default_history(app, tmp_path):
    path = write_config(tmp_path / "other.yaml")
    selected = select("./other.yaml")

    assert selected.config == Path("other.yaml")
    assert selected.state == default_state_path()
    assert selected.name is None


def test_an_extensionless_file_still_works_as_a_path(app, tmp_path):
    write_config(tmp_path / "myconf")
    selected = select("myconf")

    assert selected.config == Path("myconf")
    assert selected.name is None


def test_a_missing_name_says_how_to_create_it(app):
    with pytest.raises(ConfigError, match="youpdated init work"):
        select("work")


def test_a_bad_name_names_the_rule(app):
    with pytest.raises(ConfigError, match="not a config name"):
        select("x" * 40)


def test_state_given_wins_over_the_named_history(app, tmp_path):
    named_config(app, "work")
    assert select("work", tmp_path / "s.db").state == tmp_path / "s.db"


def test_the_environment_picks_a_config(app, monkeypatch):
    named_config(app, "work")
    monkeypatch.setenv("YOUPDATED_CONFIG", "work")
    assert select(None).name == "work"


def test_c_wins_over_the_environment(app, monkeypatch):
    named_config(app, "work")
    named_config(app, "home")
    monkeypatch.setenv("YOUPDATED_CONFIG", "work")
    assert select("home").name == "home"


def test_c_default_overrides_the_environment(app, monkeypatch):
    named_config(app, "work")
    monkeypatch.setenv("YOUPDATED_CONFIG", "work")
    selected = select("default")

    assert selected.config is None
    assert selected.name == "default"


# init


def test_init_name_writes_a_named_config(app):
    code, out = run("init", "work")

    assert code == 0
    assert (app / "configs" / "work.yaml").is_file()
    assert "-c work" in out
    assert named() == ["work"]


def test_init_c_name_is_the_same(app):
    assert main(["init", "-c", "work"]) == 0
    assert (app / "configs" / "work.yaml").is_file()


def test_init_default_is_plain_init(app):
    assert main(["init", "default"]) == 0
    assert (app / "configs" / "default.yaml").is_file()
    assert named() == []


def test_init_refuses_an_existing_named_config(app):
    named_config(app, "work", "alpha")
    assert main(["init", "work"]) == 1
    assert "alpha" in (app / "configs" / "work.yaml").read_text(encoding="utf-8")


def test_init_refuses_a_bad_name(app):
    assert main(["init", "no/slashes"]) == 1
    assert main(["init", "_bad"]) == 1
    assert named() == []


def test_init_refuses_a_name_and_c(app):
    assert main(["init", "work", "-c", "home"]) == 1


def test_init_writes_the_basic_settings_at_their_defaults_and_watches_nothing():
    loaded = parse_config_text(INIT_CONFIG, allow_empty=True)
    assert loaded.privacy == PrivacyConfig()
    assert loaded.sources == {}
    assert not loaded.warnings


def test_the_init_example_works_once_uncommented():
    text = INIT_CONFIG.replace("  # git:\n  #   - ", "  git:\n    - ")
    assert parse_config_text(text).sources == {"git": ["Void1-1/youpdated"]}


def test_add_and_check_work_on_a_fresh_init(app, fake):
    write_items(fake, {"alpha": [{"uid": "1.0"}]})
    assert main(["init"]) == 0

    # Nothing watched yet: says how to start
    code, out = run("check", "--no-notify")
    assert code == 1 and "youpdated add" in out

    assert main(["add", "fake", "alpha"]) == 0
    text = (app / "configs" / "default.yaml").read_text(encoding="utf-8")
    assert "#   - Void1-1/youpdated" in text, "the commented example stays"
    assert main(["check", "--no-notify"]) == 0


def test_edit_saves_a_fresh_init_without_asking(app, monkeypatch):
    """Only a setting changed, nothing watched yet: no "does not load" prompt"""
    assert main(["init"]) == 0
    path = app / "configs" / "default.yaml"
    script = app.parent / "editor.py"
    script.write_text(
        "import sys, pathlib\n"
        "p = pathlib.Path(sys.argv[1])\n"
        "p.write_text(p.read_text().replace('timeout: 20 ', 'timeout: 30 '))\n",
        encoding="utf-8",
    )
    import sys
    monkeypatch.setenv("VISUAL", f"{sys.executable} {script}")
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO())

    assert main(["edit"]) == 0
    assert load_config(path, allow_empty=True).privacy.timeout == 30


# using a named config


def test_check_c_name_records_into_its_own_history(app, fake):
    write_items(fake, {"alpha": [{"uid": "1.0"}]})
    named_config(app, "work", "alpha")

    assert main(["check", "-c", "work", "--no-notify"]) == 0
    assert (app / "history" / "work-state.sqlite3").is_file()
    assert not (app / "history" / "default-state.sqlite3").exists()


def test_the_environment_applies_to_list(app, fake, monkeypatch):
    named_config(app, "work", "alpha")
    named_config(app, "default", "beta")
    monkeypatch.setenv("YOUPDATED_CONFIG", "work")

    code, out = run("list", "--json")
    assert code == 0
    assert [row["key"] for row in json.loads(out)["targets"]] == ["alpha"]


def test_add_and_rm_change_only_the_named_config(app, fake):
    work = named_config(app, "work")
    default = named_config(app, "default", "beta")
    before = default.read_text(encoding="utf-8")

    assert main(["add", "-c", "work", "fake", "alpha"]) == 0
    assert "alpha" in work.read_text(encoding="utf-8")
    assert main(["rm", "-c", "work", "fake", "alpha"]) == 0
    assert "alpha" not in work.read_text(encoding="utf-8")
    assert default.read_text(encoding="utf-8") == before


def test_rm_wipe_forgets_in_the_named_history(app, fake):
    write_items(fake, {"alpha": [{"uid": "1.0"}]})
    named_config(app, "work", "alpha")
    named_config(app, "default", "alpha")
    assert main(["check", "-c", "work", "--no-notify"]) == 0
    assert main(["check", "--no-notify"]) == 0

    code, out = run("rm", "-c", "work", "--wipe", "fake", "alpha")
    assert code == 0 and "Forgot the history of alpha" in out
    _, listed = run("list", "--json")
    assert json.loads(listed)["targets"][0]["latest"] is not None


# configs


def test_configs_lists_every_config(app, fake):
    named_config(app, "default", "alpha", "beta")
    named_config(app, "work", "gamma")

    code, out = run("configs", "--json")
    rows = {row["name"]: row for row in json.loads(out)["configs"]}

    assert code == 0
    assert list(rows) == ["default", "work"]
    assert rows["default"]["targets"] == 2 and rows["default"]["selected"]
    assert rows["work"]["targets"] == 1 and not rows["work"]["selected"]
    assert rows["work"]["history"] == str(app / "history" / "work-state.sqlite3")


def test_configs_marks_the_environments_choice(app, monkeypatch):
    named_config(app, "work")
    monkeypatch.setenv("YOUPDATED_CONFIG", "work")

    _, out = run("configs", "--json")
    rows = {row["name"]: row for row in json.loads(out)["configs"]}
    assert rows["work"]["selected"] and not rows["default"]["selected"]


def test_configs_warns_about_an_environment_naming_no_config(app, monkeypatch):
    monkeypatch.setenv("YOUPDATED_CONFIG", "nope")
    _, out = run("configs")
    assert "no config named `nope`" in out


def test_configs_takes_a_path_in_the_environment_quietly(app, tmp_path, monkeypatch):
    write_config(tmp_path / "other.yaml")
    monkeypatch.setenv("YOUPDATED_CONFIG", str(tmp_path / "other.yaml"))
    _, out = run("configs")
    assert "warning" not in out


def test_configs_shows_a_default_not_created_yet(app):
    code, out = run("configs")
    assert code == 0 and "not created" in out


def test_configs_says_when_the_default_could_be_migrated(app):
    write_config(app / "config.yaml")
    _, out = run("configs")
    assert "`youpdated configs migrate`" in " ".join(out.split())


@encryption
def test_configs_shows_an_encrypted_config_without_asking(app, monkeypatch):
    path = app / "configs" / "work.yaml"
    path.parent.mkdir(parents=True)
    crypto.write_private(path, crypto.encrypt(b"sources: {}\n", "pw"))
    # no terminal and no passphrase: a prompt would fail the command
    monkeypatch.setattr("sys.stdin", io.StringIO())

    code, out = run("configs", "--json")
    rows = {row["name"]: row for row in json.loads(out)["configs"]}
    assert code == 0
    assert rows["work"]["encrypted"] and rows["work"]["targets"] is None


def test_configs_rm_deletes_the_config_and_its_history(app, fake):
    named_config(app, "work", "alpha")
    assert main(["check", "-c", "work", "--no-notify"]) == 0
    history = app / "history" / "work-state.sqlite3"
    assert history.is_file()

    assert main(["configs", "rm", "work", "--yes"]) == 0
    assert not (app / "configs" / "work.yaml").exists()
    assert not history.exists()
    assert not Path(f"{history}.lock").exists()


def test_configs_rm_test_deletes_nothing(app):
    path = named_config(app, "work")
    code, out = run("configs", "rm", "work", "--test")
    assert code == 0 and "nothing was deleted" in out
    assert path.exists()


def test_configs_rm_without_a_terminal_needs_yes(app, monkeypatch):
    path = named_config(app, "work")
    monkeypatch.setattr("sys.stdin", io.StringIO())
    assert main(["configs", "rm", "work"]) == 1
    assert path.exists()


def test_configs_rm_refuses_the_default(app):
    path = named_config(app, "default")
    code, out = run("configs", "rm", "default", "--yes")
    assert code == 1 and "uninstall" in out
    assert path.exists()


def test_configs_rm_a_missing_config(app):
    assert main(["configs", "rm", "work", "--yes"]) == 1


# migrate


def test_migrate_moves_the_old_default_and_keeps_its_history(app, fake):
    write_items(fake, {"alpha": [{"uid": "1.0"}]})
    write_config(app / "config.yaml", "alpha")
    assert main(["check", "--no-notify"]) == 0
    assert (app / "state.sqlite3").is_file()

    assert main(["configs", "migrate", "--yes"]) == 0
    assert (app / "configs" / "default.yaml").is_file()
    assert (app / "history" / "default-state.sqlite3").is_file()
    assert not (app / "config.yaml").exists() and not (app / "state.sqlite3").exists()
    assert not (app / "state.sqlite3.lock").exists()

    # Seen before the move: not new again
    write_items(fake, {"alpha": [{"uid": "1.0"}, {"uid": "1.1"}]})
    code, out = run("check", "--no-notify", "--json")
    assert code == 0
    assert [update["uid"] for update in json.loads(out)["updates"]] == ["1.1"]


@encryption
def test_migrate_moves_encrypted_files_without_a_passphrase(app, monkeypatch):
    (app).mkdir()
    crypto.write_private(app / "config.yaml", crypto.encrypt(b"sources: {}\n", "pw"))
    monkeypatch.setattr("sys.stdin", io.StringIO())

    assert main(["configs", "migrate", "--yes"]) == 0
    assert crypto.is_encrypted_file(app / "configs" / "default.yaml")


def test_migrate_refuses_to_overwrite(app):
    write_config(app / "config.yaml", "alpha")
    new = named_config(app, "default", "beta")

    assert main(["configs", "migrate", "--yes"]) == 1
    assert "beta" in new.read_text(encoding="utf-8")
    assert (app / "config.yaml").exists()


def test_migrate_with_nothing_to_move(app):
    code, out = run("configs", "migrate", "--yes")
    assert code == 0 and "Nothing to migrate" in out


def test_migrate_test_moves_nothing(app):
    write_config(app / "config.yaml")
    code, out = run("configs", "migrate", "--test")
    assert code == 0 and "nothing was moved" in out
    assert (app / "config.yaml").exists()


# check --all-configs


def test_all_configs_checks_each_with_its_own_history(app, fake):
    write_items(fake, {"alpha": [{"uid": "1.0"}], "beta": [{"uid": "2.0"}]})
    named_config(app, "default", "alpha")
    named_config(app, "work", "beta")

    code, out = run("check", "--all-configs", "--no-notify")
    assert code == 0
    assert "default" in out and "work" in out
    assert (app / "history" / "default-state.sqlite3").is_file()
    assert (app / "history" / "work-state.sqlite3").is_file()


def test_all_configs_goes_on_past_a_broken_config(app, fake):
    write_items(fake, {"beta": [{"uid": "2.0"}]})
    broken = app / "configs" / "default.yaml"
    broken.parent.mkdir(parents=True)
    broken.write_text("sources: [not a mapping\n", encoding="utf-8")
    named_config(app, "work", "beta")

    code, out = run("check", "--all-configs", "--no-notify")
    assert code == 1
    assert "Config error" in out
    assert (app / "history" / "work-state.sqlite3").is_file()


@pytest.mark.parametrize("flag", [["-c", "work"], ["--state", "x.db"], ["--json"], ["--rss", "f.xml"]])
def test_all_configs_refuses_options_for_one_config(app, flag):
    named_config(app, "work")
    code, out = run("check", "--all-configs", *flag)
    assert code == 1 and "--all-configs" in out


def test_all_configs_with_no_config(app):
    assert main(["check", "--all-configs"]) == 1


# passphrases


@encryption
def test_each_config_can_have_its_own_passphrase(app, fake, monkeypatch):
    write_items(fake, {"alpha": [{"uid": "1.0"}], "beta": [{"uid": "2.0"}]})
    for name, entry, passphrase in (("default", "alpha", "one"), ("work", "beta", "two")):
        path = named_config(app, name, entry)
        crypto.write_private(path, crypto.encrypt(path.read_bytes(), passphrase))
    monkeypatch.setattr("sys.stdin", io.StringIO())
    monkeypatch.setenv("YOUPDATED_PASSPHRASE", "one")
    monkeypatch.setenv("YOUPDATED_PASSPHRASE_WORK", "two")

    assert main(["check", "--all-configs", "--no-notify"]) == 0
    assert (app / "history" / "work-state.sqlite3").is_file()


def test_the_passphrase_variable_for_a_name():
    assert crypto.passphrase_env("work-stuff") == "YOUPDATED_PASSPHRASE_WORK_STUFF"
