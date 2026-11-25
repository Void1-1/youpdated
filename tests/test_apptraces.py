"""Uninstall: what the desktop apps leave behind, against a fake home, registry and keychain"""

from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from youpdated import apptraces, cleanup, cli, config
from youpdated.apptraces import AUMID_KEY, APPROVED_KEY, CLSID_KEY, RUN_KEY, RUN_VALUE, URL_SCHEME_KEY
from youpdated.cleanup import Trace, find_traces, remove_traces

MAC_ID = apptraces.MAC_BUNDLE_IDS[0]
_app_traces, _app_locations = apptraces.app_traces, apptraces.app_locations


class FakeKeychain:
    """``security`` and ``defaults``, with passphrase items by service"""

    def __init__(self, stored: bool | set[str] = True, refuse_delete: bool = False) -> None:
        if isinstance(stored, bool):
            stored = {"Youpdated"} if stored else set()
        self.stored = stored
        self.refuse_delete = refuse_delete
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, *command: str) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        tool, action = Path(command[0]).name, command[1]
        service = command[command.index("-s") + 1] if "-s" in command else None
        code, error = 0, ""
        if tool == "security" and action == "find-generic-password":
            code = 0 if service in self.stored else 44
        elif tool == "security" and action == "delete-generic-password":
            if self.refuse_delete:
                code, error = 1, "SecKeychainItemDelete: User interaction is not allowed."
            elif service in self.stored:
                self.stored.discard(service)
            else:
                code = 44
        return subprocess.CompletedProcess(command, code, "", error)


@pytest.fixture
def keychain(monkeypatch):
    fake = FakeKeychain()
    monkeypatch.setattr(apptraces, "_run", fake)
    return fake


def _mac_app(home: Path) -> None:
    library = home / "Library"
    (library / "Preferences").mkdir(parents=True)
    (library / "Preferences" / f"{MAC_ID}.plist").write_bytes(b"plist")
    state = library / "Saved Application State" / f"{MAC_ID}.savedState"
    state.mkdir(parents=True)
    (state / "windows.plist").write_bytes(b"x")
    (library / "Caches" / MAC_ID).mkdir(parents=True)
    (library / "Caches" / MAC_ID / "Cache.db").write_bytes(b"x")
    (library / "HTTPStorages").mkdir(parents=True)
    (library / "HTTPStorages" / f"{MAC_ID}.binarycookies").write_bytes(b"x")
    (library / "Logs" / "Youpdated").mkdir(parents=True)
    (library / "Logs" / "Youpdated" / "app.log").write_text("error\n", encoding="utf-8")


def test_finds_what_the_mac_app_left(tmp_path, keychain):
    _mac_app(tmp_path)
    traces = apptraces._mac_traces(tmp_path)

    assert {t.kind for t in traces} == {"app"}
    shown = [t.shown for t in traces]
    library = tmp_path / "Library"
    for path in (
        library / "Preferences" / f"{MAC_ID}.plist",
        library / "Saved Application State" / f"{MAC_ID}.savedState",
        library / "Caches" / MAC_ID,
        library / "HTTPStorages" / f"{MAC_ID}.binarycookies",
        library / "Logs" / "Youpdated" / "app.log",
        library / "Logs" / "Youpdated",
    ):
        assert str(path) in shown
    assert "keychain: Youpdated (passphrase)" in shown
    # the log goes before the folder holding it
    assert shown.index(str(library / "Logs" / "Youpdated" / "app.log")) < shown.index(str(library / "Logs" / "Youpdated"))


def test_removes_what_the_mac_app_left(tmp_path, keychain):
    _mac_app(tmp_path)
    removed, failed = remove_traces(apptraces._mac_traces(tmp_path))

    assert not failed
    assert apptraces._mac_traces(tmp_path) == []
    # only the app's own folders inside Library, never Library's folders themselves
    assert sorted(p.name for p in (tmp_path / "Library").iterdir()) == [
        "Caches", "HTTPStorages", "Logs", "Preferences", "Saved Application State",
    ]
    assert ("/usr/bin/defaults", "delete", MAC_ID) in keychain.commands
    assert not keychain.stored


def test_nothing_from_the_mac_app(tmp_path, monkeypatch):
    monkeypatch.setattr(apptraces, "_run", FakeKeychain(stored=False))

    assert apptraces._mac_traces(tmp_path) == []


def test_the_mac_log_folder_stays_with_other_files_in_it(tmp_path, keychain):
    _mac_app(tmp_path)
    logs = tmp_path / "Library" / "Logs" / "Youpdated"
    (logs / "notes.txt").write_text("not ours", encoding="utf-8")

    _, failed = remove_traces(apptraces._mac_traces(tmp_path))

    assert [t.path for t, _ in failed] == [logs]
    assert (logs / "notes.txt").exists() and not (logs / "app.log").exists()


def test_the_dev_apps_own_passphrase_is_removed_too(tmp_path, monkeypatch):
    keychain = FakeKeychain(stored={"Youpdated", "Youpdated Dev"})
    monkeypatch.setattr(apptraces, "_run", keychain)

    traces = apptraces._mac_traces(tmp_path)
    assert [(t.shown, t.note) for t in traces] == [
        ("keychain: Youpdated (passphrase)", "Mac app: the stored passphrase"),
        ("keychain: Youpdated Dev (passphrase)", "Mac dev app: the stored passphrase"),
    ]
    removed, failed = remove_traces(traces)
    assert len(removed) == 2 and not failed
    assert not keychain.stored


def test_only_the_dev_apps_passphrase(tmp_path, monkeypatch):
    keychain = FakeKeychain(stored={"Youpdated Dev"})
    monkeypatch.setattr(apptraces, "_run", keychain)

    remove_traces(apptraces._mac_traces(tmp_path))

    deletes = [c for c in keychain.commands if c[1] == "delete-generic-password"]
    assert deletes == [("/usr/bin/security", "delete-generic-password", "-s", "Youpdated Dev", "-a", "passphrase")]


def test_a_refused_keychain_delete_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(apptraces, "_run", FakeKeychain(refuse_delete=True))

    _, failed = remove_traces(apptraces._mac_traces(tmp_path))

    assert [(t.shown, why) for t, why in failed] == [
        ("keychain: Youpdated (passphrase)", "SecKeychainItemDelete: User interaction is not allowed.")
    ]


def test_finds_the_mac_app_itself_by_its_bundle_id(tmp_path):
    def app(folder: Path, name: str, bundle_id: str) -> Path:
        contents = folder / name / "Contents"
        contents.mkdir(parents=True)
        (contents / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": bundle_id}))
        return folder / name

    system, user = tmp_path / "Applications", tmp_path / "home" / "Applications"
    ours = app(system, "Youpdated.app", MAC_ID)
    dev = app(user, "Youpdated Dev.app", apptraces.MAC_BUNDLE_IDS[1])
    app(user, "Youpdated.app", "com.example.SomethingElse")

    assert apptraces._mac_locations((system, user)) == [ours, dev]


class FakeRegistry:
    """HKEY_CURRENT_USER as ``{key: {name: value}}``"""

    def __init__(self, keys: dict[str, dict[str, object]]) -> None:
        self.keys = keys

    def value(self, key, name):
        return self.keys.get(key, {}).get(name)

    def has_key(self, key):
        return any(k == key or k.startswith(key + "\\") for k in self.keys)

    def delete_value(self, key, name):
        self.keys.get(key, {}).pop(name, None)

    def delete_tree(self, key):
        for k in [k for k in self.keys if k == key or k.startswith(key + "\\")]:
            del self.keys[k]


class FakeCredentials:
    def __init__(self, *targets: str) -> None:
        self.targets = set(targets)

    def exists(self, target):
        return target in self.targets

    def delete(self, target):
        self.targets.discard(target)


ACTIVATOR = "{6F0A1C5E-0000-4000-8000-000000000001}"
EXE = r"C:\Users\dev\Downloads\Youpdated\Youpdated.exe"


def _windows_registry() -> FakeRegistry:
    return FakeRegistry({
        RUN_KEY: {RUN_VALUE: f'"{EXE}" --background', "OneDrive": "onedrive.exe"},
        APPROVED_KEY: {RUN_VALUE: b"\x03" + bytes(11)},
        URL_SCHEME_KEY: {"": "URL:Youpdated link", "URL Protocol": ""},
        rf"{URL_SCHEME_KEY}\shell\open\command": {"": f'"{EXE}" "%1"'},
        AUMID_KEY: {"DisplayName": "Youpdated", "CustomActivator": ACTIVATOR},
        rf"{CLSID_KEY}\{ACTIVATOR}\LocalServer32": {"": f'"{EXE}" ----AppNotificationActivated:'},
        rf"{CLSID_KEY}\{{OTHER}}\LocalServer32": {"": "other.exe"},
    })


@pytest.fixture
def windows(tmp_path, monkeypatch):
    """The data folder, registry and Credential Manager of a PC that ran the Windows app"""
    data = tmp_path / "Local" / "youpdated"
    monkeypatch.setattr(config, "data_dir", lambda: data)
    (data / "app").mkdir(parents=True)
    (data / "app" / "state.json").write_text("{}", encoding="utf-8")
    (data / "app" / "app.log").write_text("error\n", encoding="utf-8")
    roaming = tmp_path / "Roaming"
    programs = roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    programs.mkdir(parents=True)
    (programs / "Youpdated.lnk").write_bytes(b"lnk")
    sdk = tmp_path / "Local" / "Microsoft" / "WindowsAppSDK"
    sdk.mkdir(parents=True)
    (sdk / "Void1-1.Youpdated.png").write_bytes(b"png")
    (sdk / "Someone.Else.png").write_bytes(b"png")
    registry = _windows_registry()
    monkeypatch.setattr(apptraces, "registry", registry)
    monkeypatch.setattr(apptraces, "credentials", FakeCredentials(apptraces.WINDOWS_CREDENTIAL, "git:https://github.com"))
    return SimpleNamespace(data=data, roaming=roaming, local=tmp_path / "Local", sdk=sdk, registry=registry)


def test_finds_what_the_windows_app_left(windows):
    shown = [t.shown for t in apptraces._windows_traces(windows.roaming, windows.local)]

    assert shown == [
        str(windows.data / "app" / "state.json"),
        str(windows.data / "app" / "app.log"),
        str(windows.data / "app"),
        str(windows.roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Youpdated.lnk"),
        rf"HKCU\{RUN_KEY}\{RUN_VALUE}",
        rf"HKCU\{URL_SCHEME_KEY}",
        rf"HKCU\{AUMID_KEY}",
        str(windows.sdk / "Void1-1.Youpdated.png"),
        "Credential Manager: Youpdated/passphrase",
    ]


def test_removes_what_the_windows_app_left_and_nothing_else(windows):
    removed, failed = remove_traces(apptraces._windows_traces(windows.roaming, windows.local))

    assert not failed
    assert apptraces._windows_traces(windows.roaming, windows.local) == []
    assert [p.name for p in windows.sdk.iterdir()] == ["Someone.Else.png"]
    assert not (windows.data / "app").exists()
    assert windows.registry.keys == {
        RUN_KEY: {"OneDrive": "onedrive.exe"},
        APPROVED_KEY: {},
        rf"{CLSID_KEY}\{{OTHER}}\LocalServer32": {"": "other.exe"},
    }
    assert apptraces.credentials.targets == {"git:https://github.com"}


def test_a_notification_class_that_isnt_the_apps_is_kept(windows):
    windows.registry.keys[rf"{CLSID_KEY}\{ACTIVATOR}\LocalServer32"] = {"": "someone-else.exe"}

    remove_traces(apptraces._windows_traces(windows.roaming))

    assert rf"{CLSID_KEY}\{ACTIVATOR}\LocalServer32" in windows.registry.keys
    assert AUMID_KEY not in windows.registry.keys


def test_uninstall_empties_the_data_folder_holding_the_windows_app_folder(tmp_path, windows, monkeypatch):
    data = windows.data
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "tool_dir", lambda: tmp_path)
    for module in (config, cleanup):
        monkeypatch.setattr(module, "config_dir", lambda: data)
        monkeypatch.setattr(module, "data_dir", lambda: data)
    monkeypatch.setattr(apptraces, "app_traces", lambda: apptraces._windows_traces(windows.roaming))
    (data / "configs").mkdir()
    (data / "configs" / "default.yaml").write_text("sources: {}\n", encoding="utf-8")

    removed, failed = remove_traces(find_traces())

    assert not failed
    assert not data.exists()


@pytest.mark.parametrize(
    ("command", "exe"),
    [
        (f'"{EXE}" --background', EXE),
        (r'"C:\Program Files\Youpdated\Youpdated.exe" "%1"', r"C:\Program Files\Youpdated\Youpdated.exe"),
        (r"C:\Apps\Youpdated\Youpdated.exe", r"C:\Apps\Youpdated\Youpdated.exe"),
        ("", None),
        (None, None),
        (b"\x03", None),
    ],
)
def test_reads_the_program_of_a_registered_command(command, exe):
    found = apptraces._exe_of(command)
    assert (str(found) if found else None) == exe


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths")
def test_finds_the_windows_app_folder_from_its_registrations(tmp_path, monkeypatch):
    folder = tmp_path / "Youpdated"
    folder.mkdir()
    exe = folder / "Youpdated.exe"
    monkeypatch.setattr(apptraces, "registry", FakeRegistry({
        RUN_KEY: {RUN_VALUE: f'"{exe}" --background'},
        rf"{URL_SCHEME_KEY}\shell\open\command": {"": f'"{exe}" "%1"'},
    }))

    assert apptraces._windows_locations() == [folder]


def test_no_app_on_linux():
    assert _app_traces("linux") == []
    assert _app_locations("linux") == []


# The CLI


@pytest.fixture
def app_dir(tmp_path, monkeypatch):
    directory = tmp_path / "youpdated"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(config, "tool_dir", lambda: tmp_path)
    for module in (config, cleanup):
        monkeypatch.setattr(module, "config_dir", lambda: directory)
        monkeypatch.setattr(module, "data_dir", lambda: directory)
    monkeypatch.setattr(cli, "find_package", lambda: None)
    return directory


APP = Path("/Applications/Youpdated.app")


@pytest.fixture
def an_app_trace(tmp_path, monkeypatch):
    """One keychain-like trace, and the app itself in Applications"""
    removed: list[str] = []
    trace = Trace(None, "app", "Mac app: the remembered passphrase", label="keychain: Youpdated (passphrase)",
                  remover=lambda: removed.append("passphrase"))
    monkeypatch.setattr(apptraces, "app_traces", lambda: [trace])
    monkeypatch.setattr(apptraces, "app_locations", lambda: [APP])
    return removed


def test_uninstall_removes_app_traces_and_points_at_the_app(app_dir, an_app_trace, capsys):
    assert cli.main(["uninstall", "--yes"]) == 0
    out = capsys.readouterr().out

    assert an_app_trace == ["passphrase"]
    assert "removed keychain: Youpdated (passphrase)" in out
    assert "Quit the desktop app first" in out
    assert "The app itself is kept" in out and str(APP) in out and "Drag it to the Trash" in out


def test_uninstall_test_lists_app_traces_without_removing_them(app_dir, an_app_trace, capsys):
    assert cli.main(["uninstall", "--test"]) == 0

    assert "keychain: Youpdated (passphrase)" in capsys.readouterr().out
    assert an_app_trace == []


def test_uninstall_keep_app_leaves_the_app_alone(app_dir, an_app_trace, capsys):
    assert cli.main(["uninstall", "--yes", "--keep-app"]) == 0
    out = capsys.readouterr().out

    assert an_app_trace == []
    assert "keychain" not in out and "The app itself" not in out


def test_uninstall_with_only_the_app_installed_points_at_it(app_dir, monkeypatch, capsys):
    monkeypatch.setattr(apptraces, "app_locations", lambda: [APP])

    assert cli.main(["uninstall", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "No Youpdated files found." in out and str(APP) in out


@pytest.mark.skipif(sys.platform != "win32", reason="the real registry")
def test_the_real_registry_reads_and_deletes_a_tree():
    import winreg

    key = r"Software\YoupdatedTests\apptraces"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
        winreg.SetValueEx(handle, "Youpdated", 0, winreg.REG_SZ, '"C:\\x\\Youpdated.exe"')
    winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{key}\shell\open\command").Close()
    registry = apptraces._WinRegistry()
    try:
        assert registry.value(key, "Youpdated") == '"C:\\x\\Youpdated.exe"'
        assert registry.value(key, "missing") is None
        assert registry.has_key(rf"{key}\shell")
        registry.delete_value(key, "Youpdated")
        registry.delete_value(key, "Youpdated")
        assert registry.value(key, "Youpdated") is None
        registry.delete_tree(key)
        assert not registry.has_key(key)
        registry.delete_tree(key)
    finally:
        registry.delete_tree(r"Software\YoupdatedTests")


@pytest.mark.skipif(sys.platform != "win32", reason="the real Credential Manager")
def test_the_real_credential_manager_takes_a_missing_credential():
    credentials = apptraces._WinCredentials()
    target = "YoupdatedTests/not-a-credential"

    assert credentials.exists(target) is False
    credentials.delete(target)
