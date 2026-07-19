"""Runtime paths for source checkouts and installed Windows builds."""

from __future__ import annotations

import os
from pathlib import Path
import sys


APP_DIRECTORY_NAME = "ReviewWriter"
PROJECTS_DIRECTORY_NAME = "Review Writer"


def is_frozen() -> bool:
    """Return whether a freezer such as PyInstaller is running the app."""

    return bool(getattr(sys, "frozen", False))


def resource_root() -> Path:
    """Return the read-only source or frozen bundle root."""

    return Path(__file__).resolve().parent.parent


def resource_path(*parts: str) -> Path:
    """Resolve a bundled resource without using the process working directory."""

    return resource_root().joinpath(*parts)


def _expanded_environment_path(name: str) -> Path | None:
    value = os.environ.get(name, "").strip()
    if not value:
        return None
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def user_data_root() -> Path:
    """Return the directory for settings, encrypted secrets, logs, and indexes."""

    override = _expanded_environment_path("REVIEW_WRITER_DATA_DIR")
    if override is not None:
        return override
    if not is_frozen():
        return resource_root() / ".local"
    local_app_data = os.environ.get("LOCALAPPDATA", "").strip()
    base = Path(local_app_data) if local_app_data else Path.home() / "AppData" / "Local"
    return base / APP_DIRECTORY_NAME


def _windows_documents_directory() -> Path:
    """Read the current user's redirected Documents folder when available."""

    if os.name == "nt":
        try:
            import winreg

            key_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                value, _kind = winreg.QueryValueEx(key, "Personal")
            if str(value).strip():
                return Path(os.path.expandvars(str(value))).expanduser()
        except (ImportError, OSError):
            pass
    return Path.home() / "Documents"


def projects_root() -> Path:
    """Return the default directory for user-created research projects."""

    override = _expanded_environment_path("REVIEW_WRITER_PROJECTS_DIR")
    if override is not None:
        return override
    if not is_frozen():
        return resource_root() / "outputs"
    return _windows_documents_directory() / PROJECTS_DIRECTORY_NAME / "Projects"

