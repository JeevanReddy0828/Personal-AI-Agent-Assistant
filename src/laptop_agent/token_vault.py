from __future__ import annotations

import base64
import ctypes
import json
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class TokenVaultError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredTokenInfo:
    provider: str
    token_type: str | None
    scope: str | None
    expires_in: int | None
    has_refresh_token: bool


class TokenVault:
    def __init__(self, path: Path) -> None:
        self.path = path

    def is_available(self) -> bool:
        return os.name == "nt"

    def store(self, provider: str, token_payload: dict[str, Any]) -> StoredTokenInfo:
        if not self.is_available():
            raise TokenVaultError("Encrypted token storage is currently implemented with Windows DPAPI only.")
        normalized = self._normalize_provider(provider)
        data = self._load()
        encrypted = self._encrypt(json.dumps(token_payload, sort_keys=True).encode("utf-8"))
        data[normalized] = base64.b64encode(encrypted).decode("ascii")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        return self._info(normalized, token_payload)

    def load(self, provider: str) -> dict[str, Any] | None:
        if not self.is_available():
            raise TokenVaultError("Encrypted token storage is currently implemented with Windows DPAPI only.")
        normalized = self._normalize_provider(provider)
        data = self._load()
        raw = data.get(normalized)
        if not raw:
            return None
        decrypted = self._decrypt(base64.b64decode(raw.encode("ascii")))
        loaded = json.loads(decrypted.decode("utf-8"))
        if not isinstance(loaded, dict):
            raise TokenVaultError("Stored token payload was not a JSON object.")
        return loaded

    def forget(self, provider: str) -> bool:
        normalized = self._normalize_provider(provider)
        data = self._load()
        existed = normalized in data
        if existed:
            data.pop(normalized)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        return existed

    def status(self) -> dict[str, Any]:
        data = self._load()
        return {
            "available": self.is_available(),
            "path": str(self.path),
            "providers": sorted(data.keys()),
        }

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise TokenVaultError("Token vault file was not a JSON object.")
        return {str(key): str(value) for key, value in loaded.items()}

    @staticmethod
    def _info(provider: str, token_payload: dict[str, Any]) -> StoredTokenInfo:
        expires_raw = token_payload.get("expires_in")
        try:
            expires_in = int(expires_raw) if expires_raw is not None else None
        except (TypeError, ValueError):
            expires_in = None
        return StoredTokenInfo(
            provider=provider,
            token_type=token_payload.get("token_type"),
            scope=token_payload.get("scope"),
            expires_in=expires_in,
            has_refresh_token=bool(token_payload.get("refresh_token")),
        )

    @staticmethod
    def _normalize_provider(provider: str) -> str:
        normalized = provider.lower().strip()
        if normalized in {"google", "gmail"}:
            return "gmail"
        if normalized in {"microsoft", "outlook", "office", "office365"}:
            return "outlook"
        return normalized

    @staticmethod
    def _encrypt(data: bytes) -> bytes:
        return _windows_dpapi(data, protect=True)

    @staticmethod
    def _decrypt(data: bytes) -> bytes:
        return _windows_dpapi(data, protect=False)


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _windows_dpapi(data: bytes, protect: bool) -> bytes:
    if os.name != "nt":
        raise TokenVaultError("Windows DPAPI is unavailable on this platform.")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    input_buffer = ctypes.create_string_buffer(data)
    input_blob = _DataBlob(len(data), ctypes.cast(input_buffer, ctypes.POINTER(ctypes.c_char)))
    output_blob = _DataBlob()
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(input_blob), None, None, None, None, 0, ctypes.byref(output_blob))
    if not ok:
        raise TokenVaultError("Windows DPAPI operation failed.")
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
