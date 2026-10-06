from __future__ import annotations

import ctypes
import hmac
import re
from ctypes import wintypes
from enum import StrEnum
from typing import Protocol

from pydantic import SecretStr

NIM_CREDENTIAL_TARGET = "JAVIS-PC/NVIDIA-NIM"
_NIM_CREDENTIAL_USER = "JAVIS-PC"
_NIM_KEY_PATTERN = re.compile(r"nvapi-[A-Za-z0-9_-]{10,480}")
_MAX_CREDENTIAL_BYTES = 512
_ERROR_NOT_FOUND = 1168


class _CredentialW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(wintypes.BYTE)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


_CredentialPointer = ctypes.POINTER(_CredentialW)


class CredentialStoreError(RuntimeError):
    """A privacy-minimized Credential Manager failure with a stable code."""


class CredentialStatus(StrEnum):
    PRESENT = "PRESENT"
    ABSENT = "ABSENT"


class CredentialBackend(Protocol):
    def read(self, target: str) -> bytes | None: ...

    def write(self, target: str, value: bytes) -> None: ...

    def delete(self, target: str) -> bool: ...


class WindowsCredentialBackend:
    """Small pywin32 adapter for the current user's generic credentials."""

    @staticmethod
    def _modules() -> tuple[object, object]:
        try:
            import pywintypes  # type: ignore[import-untyped]
            import win32cred  # type: ignore[import-untyped]
        except ImportError:
            raise CredentialStoreError("CREDENTIAL_BACKEND_UNAVAILABLE") from None
        return win32cred, pywintypes

    def read(self, target: str) -> bytes | None:
        try:
            win32cred, pywintypes = self._modules()
        except CredentialStoreError:
            raise
        except Exception:
            return self._read_native(target)
        try:
            credential = win32cred.CredRead(target, win32cred.CRED_TYPE_GENERIC, 0)  # type: ignore[attr-defined]
        except pywintypes.error as error:  # type: ignore[attr-defined]
            if getattr(error, "winerror", None) == _ERROR_NOT_FOUND:
                return None
            raise CredentialStoreError("CREDENTIAL_READ_FAILED") from None
        except Exception:
            return self._read_native(target)
        try:
            return self._normalize_blob(credential.get("CredentialBlob"))
        except CredentialStoreError:
            raise
        except Exception:
            return self._read_native(target)

    @staticmethod
    def _normalize_blob(blob: object) -> bytes:
        if isinstance(blob, str):
            return blob.encode("utf-8")
        if isinstance(blob, bytes):
            try:
                return blob.decode("utf-16-le").encode("utf-8")
            except (UnicodeDecodeError, UnicodeEncodeError):
                raise CredentialStoreError("CREDENTIAL_DATA_INVALID") from None
        raise CredentialStoreError("CREDENTIAL_DATA_INVALID")

    @classmethod
    def _read_native(cls, target: str) -> bytes | None:
        try:
            library = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        except (AttributeError, OSError):
            raise CredentialStoreError("CREDENTIAL_BACKEND_UNAVAILABLE") from None
        library.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(_CredentialPointer),
        ]
        library.CredReadW.restype = wintypes.BOOL
        library.CredFree.argtypes = [ctypes.c_void_p]
        library.CredFree.restype = None
        pointer = _CredentialPointer()
        if not library.CredReadW(target, 1, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == _ERROR_NOT_FOUND:
                return None
            raise CredentialStoreError("CREDENTIAL_READ_FAILED")
        try:
            credential = pointer.contents
            size = int(credential.CredentialBlobSize)
            if not 1 <= size <= _MAX_CREDENTIAL_BYTES * 2:
                raise CredentialStoreError("CREDENTIAL_DATA_INVALID")
            if not credential.CredentialBlob:
                raise CredentialStoreError("CREDENTIAL_DATA_INVALID")
            raw = ctypes.string_at(credential.CredentialBlob, size)
            return cls._normalize_blob(raw)
        finally:
            if pointer:
                library.CredFree(pointer)

    def write(self, target: str, value: bytes) -> None:
        win32cred, pywintypes = self._modules()
        try:
            unicode_value = value.decode("utf-8")
        except UnicodeDecodeError:
            raise CredentialStoreError("CREDENTIAL_DATA_INVALID") from None
        credential = {
            "Type": win32cred.CRED_TYPE_GENERIC,  # type: ignore[attr-defined]
            "TargetName": target,
            "CredentialBlob": unicode_value,
            "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,  # type: ignore[attr-defined]
            "UserName": _NIM_CREDENTIAL_USER,
        }
        try:
            win32cred.CredWrite(credential, 0)  # type: ignore[attr-defined]
        except pywintypes.error:  # type: ignore[attr-defined]
            raise CredentialStoreError("CREDENTIAL_WRITE_FAILED") from None

    def delete(self, target: str) -> bool:
        win32cred, pywintypes = self._modules()
        try:
            win32cred.CredDelete(target, win32cred.CRED_TYPE_GENERIC, 0)  # type: ignore[attr-defined]
        except pywintypes.error as error:  # type: ignore[attr-defined]
            if getattr(error, "winerror", None) == _ERROR_NOT_FOUND:
                return False
            raise CredentialStoreError("CREDENTIAL_DELETE_FAILED") from None
        return True


def load_nim_credential(
    *,
    backend: CredentialBackend | None = None,
) -> SecretStr:
    store = backend or WindowsCredentialBackend()
    try:
        payload = store.read(NIM_CREDENTIAL_TARGET)
    except CredentialStoreError:
        raise
    except Exception:
        raise CredentialStoreError("CREDENTIAL_READ_FAILED") from None
    if payload is None:
        raise CredentialStoreError("NIM_CREDENTIAL_NOT_FOUND")
    return SecretStr(_decode_nim_key(payload))


def provision_nim_credential(
    key: SecretStr,
    *,
    backend: CredentialBackend | None = None,
    replace: bool = False,
) -> None:
    """Store the NIM key under a fixed target without putting it on argv."""
    store = backend or WindowsCredentialBackend()
    payload = _encode_nim_key(key)
    try:
        existing = store.read(NIM_CREDENTIAL_TARGET)
    except CredentialStoreError:
        raise
    except Exception:
        raise CredentialStoreError("CREDENTIAL_READ_FAILED") from None
    if existing is not None and not replace:
        raise CredentialStoreError("NIM_CREDENTIAL_ALREADY_EXISTS")
    try:
        store.write(NIM_CREDENTIAL_TARGET, payload)
        written = store.read(NIM_CREDENTIAL_TARGET)
    except CredentialStoreError:
        raise
    except Exception:
        raise CredentialStoreError("CREDENTIAL_WRITE_FAILED") from None
    if written is None or not hmac.compare_digest(written, payload):
        raise CredentialStoreError("CREDENTIAL_WRITE_VERIFICATION_FAILED")


def nim_credential_status(
    *,
    backend: CredentialBackend | None = None,
) -> CredentialStatus:
    store = backend or WindowsCredentialBackend()
    try:
        payload = store.read(NIM_CREDENTIAL_TARGET)
    except CredentialStoreError:
        raise
    except Exception:
        raise CredentialStoreError("CREDENTIAL_READ_FAILED") from None
    if payload is None:
        return CredentialStatus.ABSENT
    _decode_nim_key(payload)
    return CredentialStatus.PRESENT


def remove_nim_credential(
    *,
    backend: CredentialBackend | None = None,
) -> bool:
    store = backend or WindowsCredentialBackend()
    try:
        return store.delete(NIM_CREDENTIAL_TARGET)
    except CredentialStoreError:
        raise
    except Exception:
        raise CredentialStoreError("CREDENTIAL_DELETE_FAILED") from None


def _encode_nim_key(key: SecretStr) -> bytes:
    value = key.get_secret_value()
    if _NIM_KEY_PATTERN.fullmatch(value) is None:
        raise CredentialStoreError("NIM_CREDENTIAL_FORMAT_INVALID")
    payload = value.encode("utf-8")
    if len(payload) > _MAX_CREDENTIAL_BYTES:
        raise CredentialStoreError("NIM_CREDENTIAL_FORMAT_INVALID")
    return payload


def _decode_nim_key(payload: bytes) -> str:
    if not 1 <= len(payload) <= _MAX_CREDENTIAL_BYTES:
        raise CredentialStoreError("NIM_CREDENTIAL_DATA_INVALID")
    try:
        value = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise CredentialStoreError("NIM_CREDENTIAL_DATA_INVALID") from None
    if _NIM_KEY_PATTERN.fullmatch(value) is None:
        raise CredentialStoreError("NIM_CREDENTIAL_DATA_INVALID")
    return value
