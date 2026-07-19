"""Session and Windows-DPAPI protected credential storage."""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
from typing import Any

from .app_paths import user_data_root


class SecretStoreError(RuntimeError):
    pass


def default_secret_path() -> Path:
    return user_data_root() / "secrets.json"


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob_from_bytes(value: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(value)
    blob = _DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    return blob, buffer


def _protect(value: str) -> str:
    if os.name != "nt":
        raise SecretStoreError("当前系统不支持 Windows DPAPI；请使用仅本次运行模式。")
    data = value.encode("utf-8")
    source, source_buffer = _blob_from_bytes(data)
    destination = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptProtectData(
        ctypes.byref(source),
        "Review Writer credential",
        None,
        None,
        None,
        0,
        ctypes.byref(destination),
    )
    del source_buffer
    if not ok:
        raise SecretStoreError("Windows DPAPI 加密失败。")
    try:
        encrypted = ctypes.string_at(destination.pbData, destination.cbData)
        return base64.b64encode(encrypted).decode("ascii")
    finally:
        kernel32.LocalFree(destination.pbData)


def _unprotect(encoded: str) -> str:
    if os.name != "nt":
        raise SecretStoreError("当前系统不支持 Windows DPAPI。")
    try:
        encrypted = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (ValueError, UnicodeError) as error:
        raise SecretStoreError("密钥文件格式无效。") from error
    source, source_buffer = _blob_from_bytes(encrypted)
    destination = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    ok = crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(destination),
    )
    del source_buffer
    if not ok:
        raise SecretStoreError("无法解密密钥；它可能属于另一个 Windows 用户。")
    try:
        return ctypes.string_at(destination.pbData, destination.cbData).decode("utf-8")
    except UnicodeDecodeError as error:
        raise SecretStoreError("解密后的密钥不是有效 UTF-8。") from error
    finally:
        kernel32.LocalFree(destination.pbData)


class SecretStore:
    """Keep session secrets in memory and optionally persist encrypted copies."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_secret_path()
        self._session: dict[str, str] = {}

    @property
    def persistent_storage_available(self) -> bool:
        return os.name == "nt"

    def _load_encrypted(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            return {}
        return {str(key): str(value) for key, value in raw.items() if isinstance(value, str)}

    def _save_encrypted(self, values: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(values, ensure_ascii=True, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def get(self, name: str) -> str | None:
        if name in self._session:
            return self._session[name]
        encoded = self._load_encrypted().get(name)
        if not encoded:
            return None
        try:
            return _unprotect(encoded)
        except SecretStoreError:
            return None

    def set(self, name: str, value: str, *, persist: bool) -> None:
        value = value.strip()
        if not value:
            self.delete(name)
            return
        self._session[name] = value
        encrypted = self._load_encrypted()
        if persist:
            encrypted[name] = _protect(value)
        else:
            encrypted.pop(name, None)
        self._save_encrypted(encrypted)

    def delete(self, name: str) -> None:
        self._session.pop(name, None)
        encrypted = self._load_encrypted()
        if name in encrypted:
            encrypted.pop(name)
            self._save_encrypted(encrypted)

    def has(self, name: str) -> bool:
        return bool(self.get(name))
