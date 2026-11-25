"""Extra files from youpdated apps for `youpdated uninstall` to clean up

- macOS: `YOUPDATED_BUNDLE_ID` in apps/macos/Config/Youpdated.xcconfig (and `.dev` from
  scripts/make-dev-app.sh), `Keychain` in YoupdatedUI/Services.swift, `AppLog` in YoupdatedKit.
- Windows: `Program.AppUserModelId`, `CredentialStore`, `StartupRegistration`,
  `UrlSchemeRegistration` and `StartMenuShortcut` in Youpdated.App, `AppLog` and `LocalState`.

The apps themselves (`.app`, unzipped Windows folder) aren't deleted
"""

from __future__ import annotations

import os
import plistlib
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from typing import Protocol

from . import config
from .cleanup import Trace

MAC_BUNDLE_IDS = ("io.github.void1-1.Youpdated", "io.github.void1-1.Youpdated.dev")
MAC_APP_NAMES = ("Youpdated.app", "Youpdated Dev.app")
MAC_KEYCHAIN_SERVICES = ("Youpdated", "Youpdated Dev")
MAC_KEYCHAIN_ACCOUNT = "passphrase"

WINDOWS_AUMID = "Void1-1.Youpdated"
WINDOWS_CREDENTIAL = "Youpdated/passphrase"
WINDOWS_EXE = "Youpdated.exe"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APPROVED_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
RUN_VALUE = "Youpdated"
URL_SCHEME_KEY = r"Software\Classes\youpdated"
# Where the Windows App SDK registers an unpackaged app's notifications
AUMID_KEY = rf"Software\Classes\AppUserModelId\{WINDOWS_AUMID}"
CLSID_KEY = r"Software\Classes\CLSID"
NOTIFICATION_ARGUMENT = "----AppNotificationActivated:"


def app_traces(platform: str = sys.platform) -> list[Trace]:
    """What the app for this OS leaves behind"""
    if platform == "darwin":
        return _mac_traces(Path.home())
    if platform == "win32":
        return _windows_traces(_environ_path("APPDATA"), _environ_path("LOCALAPPDATA"))
    return []


def app_locations(platform: str = sys.platform) -> list[Path]:
    """Where the app itself is installed. Not removed by uninstall"""
    if platform == "darwin":
        return _mac_locations((Path("/Applications"), Path.home() / "Applications"))
    if platform == "win32":
        return _windows_locations()
    return []


def _environ_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def _trace(path: Path | None, label: str | None, note: str, remove: Callable[[], None]) -> Trace:
    return Trace(path, "app", note, label=label, remover=remove)


# macOS


def _mac_traces(home: Path) -> list[Trace]:
    library = home / "Library"
    traces: list[Trace] = []
    for bundle_id in MAC_BUNDLE_IDS:
        app = "Mac app" if bundle_id == MAC_BUNDLE_IDS[0] else "Mac dev app"
        preferences = library / "Preferences" / f"{bundle_id}.plist"
        if preferences.exists():
            traces.append(_trace(
                preferences, None, f"{app}: its settings",
                lambda bundle_id=bundle_id, path=preferences: _remove_defaults(bundle_id, path),
            ))
        for path, note in (
            (library / "Saved Application State" / f"{bundle_id}.savedState", "window positions"),
            (library / "Caches" / bundle_id, "cache"),
            (library / "HTTPStorages" / bundle_id, "download cache"),
            (library / "HTTPStorages" / f"{bundle_id}.binarycookies", "download cookies"),
            (library / "WebKit" / bundle_id, "web cache"),
        ):
            if path.exists() or path.is_symlink():
                traces.append(_trace(path, None, f"{app}: {note}", lambda path=path: _remove_path(path)))

    for service in MAC_KEYCHAIN_SERVICES:
        if _keychain_has_passphrase(service):
            app = "Mac app" if service == MAC_KEYCHAIN_SERVICES[0] else "Mac dev app"
            traces.append(_trace(
                None, f"keychain: {service} ({MAC_KEYCHAIN_ACCOUNT})", f"{app}: the stored passphrase",
                lambda service=service: _keychain_delete_passphrase(service),
            ))

    logs = library / "Logs" / "Youpdated"
    log = logs / "app.log"
    if log.is_file():
        traces.append(_trace(log, None, "Mac app: diagnostic log", lambda: _remove_path(log)))
    if logs.is_dir():
        # A generic name, so only once the log is gone and nothing else is in it
        traces.append(_trace(logs, None, "Mac app: log folder (only if empty)", lambda: _remove_empty_dir(logs)))
    return traces


def _mac_locations(folders: tuple[Path, ...]) -> list[Path]:
    found = []
    for folder in folders:
        for name in MAC_APP_NAMES:
            app = folder / name
            try:
                with (app / "Contents" / "Info.plist").open("rb") as file:
                    bundle_id = plistlib.load(file).get("CFBundleIdentifier")
            except (OSError, plistlib.InvalidFileException):
                continue
            if bundle_id in MAC_BUNDLE_IDS:
                found.append(app)
    return found


def _run(*command: str) -> subprocess.CompletedProcess[str]:
    """A macOS tool, quietly. Raises OSError when it can't start"""
    return subprocess.run(
        command, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False
    )


def _keychain_has_passphrase(service: str) -> bool:
    try:
        result = _run(
            "/usr/bin/security", "find-generic-password",
            "-s", service, "-a", MAC_KEYCHAIN_ACCOUNT,
        )
    except OSError:
        return False
    return result.returncode == 0


def _keychain_delete_passphrase(service: str) -> None:
    result = _run(
        "/usr/bin/security", "delete-generic-password",
        "-s", service, "-a", MAC_KEYCHAIN_ACCOUNT,
    )
    if result.returncode != 0:
        raise OSError(result.stderr.strip() or f"security exited with status {result.returncode}")


def _remove_defaults(bundle_id: str, path: Path) -> None:
    # Through cfprefsd, which caches settings
    try:
        _run("/usr/bin/defaults", "delete", bundle_id)
    except OSError:
        pass
    path.unlink(missing_ok=True)


# Windows

class Registry(Protocol):
    """HKEY_CURRENT_USER. Deleting raises OSError when Windows refuses"""

    def value(self, key: str, name: str) -> object | None: ...

    def has_key(self, key: str) -> bool: ...

    def delete_value(self, key: str, name: str) -> None: ...

    def delete_tree(self, key: str) -> None: ...


class Credentials(Protocol):
    def exists(self, target: str) -> bool: ...

    def delete(self, target: str) -> None: ...


class _WinRegistry:
    def value(self, key: str, name: str) -> object | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                return winreg.QueryValueEx(handle, name)[0]
        except OSError:
            return None

    def has_key(self, key: str) -> bool:
        import winreg

        try:
            winreg.OpenKey(winreg.HKEY_CURRENT_USER, key).Close()
        except OSError:
            return False
        return True

    def delete_value(self, key: str, name: str) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as handle:
                winreg.DeleteValue(handle, name)
        except FileNotFoundError:
            pass

    def delete_tree(self, key: str) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                children = []
                while True:
                    try:
                        children.append(winreg.EnumKey(handle, len(children)))
                    except OSError:
                        break
        except FileNotFoundError:
            return
        for child in children:
            self.delete_tree(rf"{key}\{child}")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)


class _WinCredentials:
    """Credential Manager, through advapi32"""

    _GENERIC = 1
    _NOT_FOUND = 1168

    def _advapi(self):
        import ctypes
        from ctypes import wintypes

        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        advapi.CredReadW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)
        ]
        advapi.CredReadW.restype = wintypes.BOOL
        advapi.CredFree.argtypes = [ctypes.c_void_p]
        advapi.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        advapi.CredDeleteW.restype = wintypes.BOOL
        return ctypes, advapi

    def exists(self, target: str) -> bool:
        ctypes, advapi = self._advapi()
        pointer = ctypes.c_void_p()
        if not advapi.CredReadW(target, self._GENERIC, 0, ctypes.byref(pointer)):
            return False
        advapi.CredFree(pointer)
        return True

    def delete(self, target: str) -> None:
        ctypes, advapi = self._advapi()
        if not advapi.CredDeleteW(target, self._GENERIC, 0):
            error = ctypes.get_last_error()
            if error != self._NOT_FOUND:
                raise ctypes.WinError(error)


# Swapped out in tests, which must never touch the real registry or Credential Manager
registry: Registry = _WinRegistry()
credentials: Credentials = _WinCredentials()


def _windows_traces(roaming: Path | None, local: Path | None = None) -> list[Trace]:
    traces: list[Trace] = []

    # %LOCALAPPDATA%\youpdated\app, inside the data folder
    folder = config.data_dir() / "app"
    for name, note in (
        ("state.json", "its settings"),
        ("state.json.tmp", "its settings, partially saved"),
        ("app.log", "diagnostic log"),
    ):
        path = folder / name
        if path.is_file():
            traces.append(_trace(path, None, f"Windows app: {note}", lambda path=path: _remove_path(path)))
    if folder.is_dir():
        traces.append(_trace(folder, None, "Windows app: its folder (only if empty)", lambda: _remove_empty_dir(folder)))

    if roaming is not None:
        shortcut = roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Youpdated.lnk"
        if shortcut.is_file():
            traces.append(_trace(shortcut, None, "Windows app: Start menu shortcut", lambda: _remove_path(shortcut)))

    if isinstance(registry.value(RUN_KEY, RUN_VALUE), str):
        traces.append(_trace(
            None, rf"HKCU\{RUN_KEY}\{RUN_VALUE}", "Windows app: start at sign-in", _remove_run_entry
        ))
    if registry.has_key(URL_SCHEME_KEY):
        traces.append(_trace(
            None, rf"HKCU\{URL_SCHEME_KEY}", "Windows app: youpdated:// links",
            lambda: registry.delete_tree(URL_SCHEME_KEY),
        ))
    if registry.has_key(AUMID_KEY):
        traces.append(_trace(
            None, rf"HKCU\{AUMID_KEY}", "Windows app: notification registration",
            _remove_notification_registration,
        ))
    if local is not None:
        icon = local / "Microsoft" / "WindowsAppSDK" / f"{WINDOWS_AUMID}.png"
        if icon.is_file():
            traces.append(_trace(icon, None, "Windows app: notification icon", lambda: _remove_path(icon)))

    try:
        stored = credentials.exists(WINDOWS_CREDENTIAL)
    except OSError:
        stored = False
    if stored:
        traces.append(_trace(
            None, f"Credential Manager: {WINDOWS_CREDENTIAL}", "Windows app: the stored passphrase",
            lambda: credentials.delete(WINDOWS_CREDENTIAL),
        ))
    return traces


def _remove_run_entry() -> None:
    registry.delete_value(RUN_KEY, RUN_VALUE)
    registry.delete_value(APPROVED_KEY, RUN_VALUE)


def _remove_notification_registration() -> None:
    activator = registry.value(AUMID_KEY, "CustomActivator")
    if isinstance(activator, str) and activator.strip():
        server = rf"{CLSID_KEY}\{activator.strip()}"
        command = registry.value(rf"{server}\LocalServer32", "")
        if isinstance(command, str) and NOTIFICATION_ARGUMENT in command:
            registry.delete_tree(server)
    registry.delete_tree(AUMID_KEY)


def _windows_locations() -> list[Path]:
    found: list[Path] = []
    for command in (
        registry.value(RUN_KEY, RUN_VALUE),
        registry.value(rf"{URL_SCHEME_KEY}\shell\open\command", ""),
    ):
        exe = _exe_of(command)
        if exe is None or exe.name.lower() != WINDOWS_EXE.lower():
            continue
        folder = Path(str(exe.parent))
        if folder.is_dir() and folder not in found:
            found.append(folder)
    return found


def _exe_of(command: object) -> PureWindowsPath | None:
    """The program of a command the app registered"""
    if not isinstance(command, str) or not command.strip():
        return None
    try:
        words = shlex.split(command, posix=False)
    except ValueError:
        return None
    return PureWindowsPath(words[0].strip('"')) if words else None


# Both


def _remove_path(path: Path) -> None:
    """A file or folder named after the bundle id"""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _remove_empty_dir(path: Path) -> None:
    if not path.is_dir():
        return
    leftovers = list(path.iterdir())
    if leftovers:
        raise OSError(f"not empty ({len(leftovers)} other file(s)), aborted")
    path.rmdir()
