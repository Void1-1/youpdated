"""`import`: entries read from a package.json, requirements.txt, or Brewfile"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from youpdated import crypto
from youpdated.cli import main
from youpdated.config import EXAMPLE_CONFIG, ConfigError, load_config
from youpdated.importers import Found, file_kind, read_file

PASSPHRASE = "correct horse battery staple"


def read(path, kind=None) -> Found:
    found = Found()
    read_file(path, found, kind)
    return found


def skipped(found: Found) -> list[str]:
    return [what for what, _ in found.skipped]

# package.json

def package_json(tmp_path, **sections) -> Path:
    path = tmp_path / "package.json"
    path.write_text(json.dumps({"name": "demo", **sections}), encoding="utf-8")
    return path


def test_every_dependency_section_is_read_once(tmp_path):
    path = package_json(
        tmp_path,
        dependencies={"express": "^4.19.0", "@types/node": "^20"},
        devDependencies={"typescript": "latest", "express": "4.19.2"},
        optionalDependencies={"fsevents": "*"},
        peerDependencies={"react": ">=18 <20"},
    )
    assert read(path).entries == {
        "npm": ["express", "@types/node", "typescript", "fsevents", "react"]
    }


def test_an_alias_watches_the_package_it_installs(tmp_path):
    path = package_json(
        tmp_path,
        dependencies={"lodash-es": "npm:lodash@^4", "types": "npm:@types/node@20", "bare": "npm:left-pad"},
    )
    assert read(path).entries == {"npm": ["lodash", "@types/node", "left-pad"]}


@pytest.mark.parametrize(
    "spec", ["npm:^4.17.0", "npm:1.2.3", "npm:>=1 <2", "npm:1.x || 2.x", "npm:*", "npm:"]
)
def test_an_alias_to_a_range_watches_the_package_under_its_own_name(tmp_path, spec):
    found = read(package_json(tmp_path, dependencies={"lodash": spec}))
    assert found.entries == {"npm": ["lodash"]}


def test_an_alias_to_a_name_starting_with_a_digit_is_still_a_name(tmp_path):
    found = read(package_json(tmp_path, dependencies={"zip": "npm:7zip-bin"}))
    assert found.entries == {"npm": ["7zip-bin"]}


@pytest.mark.parametrize("spec", ["catalog:", "catalog:react18"])
def test_a_catalog_dependency_comes_from_the_registry(tmp_path, spec):
    found = read(package_json(tmp_path, dependencies={"react": spec}))
    assert found.entries == {"npm": ["react"]}


@pytest.mark.parametrize(
    "spec",
    ["file:../lib", "link:../lib", "workspace:*", "../lib", ".", "owner/repo#main",
     "github:owner/repo", "git+https://github.com/owner/repo.git", "https://example.com/pkg.tgz",
     "thing-1.0.0.tgz", "C:\\lib"],
)
def test_a_dependency_from_outside_the_registry_is_skipped(tmp_path, spec):
    found = read(package_json(tmp_path, dependencies={"thing": spec}))
    assert found.entries == {}
    assert found.skipped == [("thing", f"installed from `{spec}`, not npm registry")]


@pytest.mark.parametrize(
    "name, spec, package",
    [("bad", "npm:@scope", "@scope"), ("has space", "^1", "has space"), ("@nope", "^1", "@nope")],
)
def test_what_is_not_a_package_name_is_skipped(tmp_path, name, spec, package):
    found = read(package_json(tmp_path, dependencies={name: spec}))
    assert found.entries == {}
    assert found.skipped == [(name, f"`{package}` is not an npm package name")]


def test_credentials_in_a_dependency_are_never_shown(tmp_path):
    spec = "git+https://user:ghp_SECRET@github.com/owner/repo.git"
    found = read(package_json(tmp_path, dependencies={"thing": spec}))
    assert "ghp_SECRET" not in repr(found.skipped)
    assert found.skipped == [
        ("thing", "installed from `git+https://github.com/owner/repo.git`, not npm registry")
    ]


def test_a_package_json_that_is_not_json_is_an_error(tmp_path):
    path = tmp_path / "package.json"
    path.write_text("{ nope", encoding="utf-8")
    with pytest.raises(ConfigError, match="is not valid JSON"):
        read(path)


def test_a_package_json_without_dependencies_finds_nothing(tmp_path):
    assert read(package_json(tmp_path)).entries == {}

# requirements.txt

def requirements(tmp_path, text: str, name: str = "requirements.txt") -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_requirements_are_read_with_their_extras_markers_and_pins_dropped(tmp_path):
    path = requirements(
        tmp_path,
        "# pinned\n"
        "httpx[socks]>=0.27  # http client\n"
        "rich ; python_version >= '3.11'\n"
        "Django==5.0 \\\n"
        "    --hash=sha256:abc123\n"
        "\n"
        "packaging\t--config-settings=x=y\n",
    )
    assert read(path).entries == {"pypi": ["httpx", "rich", "Django", "packaging"]}


def test_two_spellings_of_one_project_are_one_entry(tmp_path):
    path = requirements(tmp_path, "typing_extensions\nTyping-Extensions>=4\n")
    assert read(path).entries == {"pypi": ["typing_extensions"]}


def test_included_files_are_followed_relative_to_the_file_and_only_once(tmp_path):
    requirements(tmp_path, "pytest\n-r ../requirements.txt\n", "requirements/dev.txt")
    path = requirements(tmp_path, "--requirement=requirements/dev.txt\nhttpx\n-rrequirements/dev.txt\n")
    assert read(path).entries == {"pypi": ["pytest", "httpx"]}


def test_options_and_constraints_add_nothing(tmp_path):
    path = requirements(
        tmp_path,
        "--index-url https://pypi.org/simple\n-c constraints.txt\n--pre\nhttpx\n",
    )
    found = read(path)
    assert found.entries == {"pypi": ["httpx"]}
    assert found.skipped == []


@pytest.mark.parametrize(
    "line, what",
    [
        ("-e git+https://github.com/owner/thing#egg=thing", "git+https://github.com/owner/thing#egg=thing"),
        ("--editable .", "."),
        ("thing @ https://example.com/thing-1.0.tar.gz", "thing"),
        ("./vendor/thing", "./vendor/thing"),
        ("thing-1.0-py3-none-any.whl", "thing-1.0-py3-none-any.whl"),
        ("${PACKAGE}", "${PACKAGE}"),
        ("-r https://example.com/requirements.txt", "https://example.com/requirements.txt"),
    ],
)
def test_what_does_not_come_from_pypi_is_skipped(tmp_path, line, what):
    found = read(requirements(tmp_path, line + "\n"))
    assert found.entries == {}
    assert skipped(found) == [what]


def test_a_missing_include_is_an_error(tmp_path):
    path = requirements(tmp_path, "-r nope.txt\n")
    with pytest.raises(ConfigError, match="cannot read .*nope.txt"):
        read(path)


def test_an_include_can_be_a_file_url(tmp_path):
    base = requirements(tmp_path, "httpx\n", "requirements/base.txt")
    path = requirements(tmp_path, f"-r {base.as_uri()}\nrich\n")
    assert read(path).entries == {"pypi": ["httpx", "rich"]}


def test_a_comment_ending_in_a_backslash_does_not_swallow_the_next_line(tmp_path):
    path = requirements(tmp_path, "# pinned for reasons \\\nhttpx\n  # indented \\\nrich\n")
    assert read(path).entries == {"pypi": ["httpx", "rich"]}


@pytest.mark.parametrize(
    "option, why",
    [
        ("--index-url https://private.example/simple", "installed from https://private.example/simple, not PyPI"),
        ("-i https://private.example/simple", "installed from https://private.example/simple, not PyPI"),
        ("--no-index", "pip is told not to use an index"),
    ],
)
def test_requirements_from_another_index_are_skipped_wherever_it_is_set(tmp_path, option, why):
    requirements(tmp_path, f"{option}\n", "requirements/index.txt")
    path = requirements(tmp_path, "internal-tool\n-r requirements/index.txt\n")
    found = read(path)
    assert found.entries == {}
    assert found.skipped == [("internal-tool", why)]


def test_the_pypi_index_named_outright_is_still_pypi(tmp_path):
    path = requirements(tmp_path, "-i https://private.example/simple\n--index-url=https://pypi.org/simple/\nhttpx\n")
    assert read(path).entries == {"pypi": ["httpx"]}


def test_an_extra_index_is_a_warning_and_its_packages_are_still_added(tmp_path):
    path = requirements(tmp_path, "--extra-index-url https://download.pytorch.org/whl/cu121\ntorch\n")
    found = read(path)
    assert found.entries == {"pypi": ["torch"]}
    assert found.warnings == [
        f"{path} also installs from https://download.pytorch.org/whl/cu121; a package from "
        "there is watched on PyPI, which may not be the same package"
    ]


@pytest.mark.parametrize(
    "line",
    [
        "thing @ git+https://user:ghp_SECRET@github.com/owner/thing.git",
        "-e git+https://ghp_SECRET@github.com/owner/thing#egg=thing",
        "-r https://user:ghp_SECRET@example.com/requirements.txt",
        "--extra-index-url https://user:ghp_SECRET@private.example/simple",
        "-i https://ghp_SECRET@private.example/simple\nthing",
    ],
)
def test_credentials_in_a_url_are_never_shown(tmp_path, line):
    found = read(requirements(tmp_path, line + "\n"))
    assert "ghp_SECRET" not in repr((found.skipped, found.warnings))
    assert found.skipped or found.warnings


def test_a_skip_is_reported_once_however_many_files_list_it(tmp_path):
    requirements(tmp_path, "-e .\n", "requirements/base.txt")
    path = requirements(tmp_path, "-e .\n-r requirements/base.txt\n")
    found = Found()
    read_file(path, found)
    read_file(tmp_path / "requirements/base.txt", found)
    assert found.skipped == [(".", "an editable install, not from PyPI")]

# Brewfile

def test_formulae_and_casks_are_read_as_brew_bundle_dump_writes_them(tmp_path):
    path = tmp_path / "Brewfile"
    path.write_text(
        'tap "homebrew/bundle"\n'
        'brew "wget"\n'
        "brew 'python@3.12', restart_service: true\n"
        'brew("homebrew/core/jq")\n'
        '# brew "commented-out"\n'
        'cask_args appdir: "~/Applications"\n'
        'cask "firefox"\n'
        'cask "homebrew/cask/iterm2", greedy: true\n'
        'brew "wget" if OS.mac?\n',
        encoding="utf-8",
    )
    found = read(path)
    assert found.entries == {
        "homebrew": ["wget", "python@3.12", "jq", "cask:firefox", "cask:iterm2"]
    }
    assert found.skipped == []


def test_what_no_source_watches_is_skipped_with_why(tmp_path):
    path = tmp_path / "Brewfile"
    path.write_text(
        'brew "someone/tap/tool"\nmas "Xcode", id: 497799835\nvscode "ms-python.python"\n',
        encoding="utf-8",
    )
    found = read(path)
    assert found.entries == {}
    assert found.skipped[0][0] == "someone/tap/tool"
    assert "third-party tap" in found.skipped[0][1]
    assert found.skipped[1:] == [
        ("mas Xcode", "no source watches `mas` entries"),
        ("vscode ms-python.python", "no source watches `vscode` entries"),
    ]


def test_ruby_that_is_not_a_brewfile_entry_is_ignored(tmp_path):
    path = tmp_path / "Brewfile"
    path.write_text(
        'require "json"\nputs "hello"\nsystem "true"\ncargo "ripgrep"\nbrew "wget"\n',
        encoding="utf-8",
    )
    found = read(path)
    assert found.entries == {"homebrew": ["wget"]}
    assert found.skipped == [("cargo ripgrep", "no source watches `cargo` entries")]

# what a file is

@pytest.mark.parametrize(
    "name, kind",
    [
        ("package.json", "package.json"),
        ("Brewfile", "Brewfile"),
        (".Brewfile", "Brewfile"),
        ("work.Brewfile", "Brewfile"),
        ("requirements.txt", "requirements.txt"),
        ("requirements-dev.txt", "requirements.txt"),
        ("dev-requirements.txt", "requirements.txt"),
        ("requirements.in", "requirements.txt"),
        ("requirements/base.txt", "requirements.txt"),
    ],
)
def test_a_file_is_known_by_its_name(tmp_path, name, kind):
    assert file_kind(tmp_path / name) == kind


@pytest.mark.parametrize("name", ["deps.json", "Brewfile.lock.json", "notes.txt", "constraints.txt"])
def test_a_file_named_otherwise_needs_a_format(tmp_path, name):
    with pytest.raises(ConfigError, match=r"name with --format \(package.json \| requirements.txt \| Brewfile\)"):
        file_kind(tmp_path / name)


def test_format_reads_a_file_named_otherwise(tmp_path):
    path = tmp_path / "base.pip"
    path.write_text("httpx\n", encoding="utf-8")
    assert read(path, "requirements.txt").entries == {"pypi": ["httpx"]}


def test_format_leaves_a_file_it_knows_by_name_alone(tmp_path):
    path = package_json(tmp_path, dependencies={"express": "^4"})
    assert read(path, "requirements.txt").entries == {"npm": ["express"]}


def test_a_missing_file_says_so(tmp_path):
    with pytest.raises(ConfigError, match="no file at"):
        read(tmp_path / "package.json")


@pytest.mark.skipif(not os.path.isdir("/dev/fd"), reason="needs /dev/fd")
def test_a_pipe_is_read_with_a_format():
    read_end, write_end = os.pipe()
    os.write(write_end, b"httpx==0.27.0\nrich==13.7.1\n")
    os.close(write_end)
    try:
        found = read(Path(f"/dev/fd/{read_end}"), "requirements.txt")
    finally:
        os.close(read_end)
    assert found.entries == {"pypi": ["httpx", "rich"]}

# CLI


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "youpdated.yaml"
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    return path


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "package.json").write_text(
        json.dumps({"dependencies": {"express": "^4", "react": "^18", "local": "file:../local"}}),
        encoding="utf-8",
    )
    (folder / "requirements.txt").write_text("httpx\nrich\n", encoding="utf-8")
    (folder / "Brewfile").write_text('brew "jq"\ncask "firefox"\n', encoding="utf-8")
    return folder


def files(project: Path) -> list[str]:
    return [str(project / name) for name in ("package.json", "requirements.txt", "Brewfile")]


def test_import_adds_what_is_new_under_each_source(config, project, capsys):
    assert main(["import", "-c", str(config), *files(project)]) == 0
    sources = load_config(config).sources
    assert sources["npm"] == ["express", "@types/node", "react"]
    assert sources["pypi"] == ["httpx", "youpdated", "rich"]
    assert sources["homebrew"] == ["wget", "cask:firefox", "jq"]

    out = capsys.readouterr().out
    assert "Skipped local" in out
    assert "Already watching express under npm" in out
    assert "Already watching cask:firefox under homebrew" in out
    assert "Added react under npm" in out
    assert "Added rich under pypi" in out and "Added jq under homebrew" in out


def test_importing_again_changes_nothing(config, project, capsys):
    assert main(["import", "-c", str(config), *files(project)]) == 0
    written = config.read_text(encoding="utf-8")
    capsys.readouterr()

    assert main(["import", "-c", str(config), *files(project)]) == 0
    assert config.read_text(encoding="utf-8") == written
    out = capsys.readouterr().out
    assert "Already watching express, react under npm" in out
    assert "Added" not in out


def test_a_file_with_nothing_to_watch_says_so(config, tmp_path, capsys):
    path = package_json(tmp_path, dependencies={"local": "file:../local"})
    assert main(["import", "-c", str(config), str(path)]) == 0
    assert "Nothing to watch in these files" in capsys.readouterr().out
    assert config.read_text(encoding="utf-8") == EXAMPLE_CONFIG


def test_a_warning_is_shown_with_what_was_added(config, tmp_path, capsys):
    path = requirements(tmp_path, "--extra-index-url https://download.pytorch.org/whl/cu121\ntorch\n")
    assert main(["import", "-c", str(config), str(path)]) == 0
    out = capsys.readouterr().out
    assert "Warning:" in out and "https://download.pytorch.org/whl/cu121" in out
    assert "Added torch under pypi" in out


def test_import_test_shows_the_change_and_writes_nothing(config, project, capsys):
    assert main(["import", "--test", "-c", str(config), *files(project)]) == 0
    out = capsys.readouterr().out
    assert "+    - react" in out and "+    - jq" in out
    assert "nothing was written" in out
    assert config.read_text(encoding="utf-8") == EXAMPLE_CONFIG


def test_import_into_a_config_with_no_sources_yet(tmp_path, project):
    config = tmp_path / "youpdated.yaml"
    config.write_text("privacy:\n  timeout: 10\n", encoding="utf-8")
    assert main(["import", "-c", str(config), str(project / "requirements.txt")]) == 0
    assert config.read_text(encoding="utf-8") == (
        "privacy:\n  timeout: 10\n\nsources:\n  pypi:\n    - httpx\n    - rich\n"
    )


def test_a_file_that_cannot_be_read_writes_nothing(config, project, capsys):
    assert main(["import", "-c", str(config), str(project / "package.json"), str(project / "nope.txt")]) == 1
    assert "Config error" in capsys.readouterr().out
    assert config.read_text(encoding="utf-8") == EXAMPLE_CONFIG


def test_import_without_a_file_is_a_usage_error(config, capsys):
    with pytest.raises(SystemExit):
        main(["import", "-c", str(config)])


@pytest.mark.skipif(not crypto.available(), reason="cryptography is not installed")
def test_an_encrypted_config_stays_encrypted(config, project, monkeypatch):
    monkeypatch.setenv(crypto.PASSPHRASE_ENV, PASSPHRASE)
    config.write_bytes(crypto.encrypt(EXAMPLE_CONFIG.encode("utf-8"), PASSPHRASE))

    assert main(["import", "-c", str(config), str(project / "Brewfile")]) == 0
    assert crypto.is_encrypted_file(config)
    assert b"jq" not in config.read_bytes()
    assert "jq" in load_config(config, passphrase=PASSPHRASE).sources["homebrew"]
