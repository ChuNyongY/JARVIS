from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from javis_core.credential_store import (
    NIM_CREDENTIAL_TARGET,
    CredentialStatus,
    CredentialStoreError,
    WindowsCredentialBackend,
    load_nim_credential,
    nim_credential_status,
    provision_nim_credential,
    remove_nim_credential,
)


@dataclass
class MemoryCredentialBackend:
    values: dict[str, bytes] = field(default_factory=dict)

    def read(self, target: str) -> bytes | None:
        return self.values.get(target)

    def write(self, target: str, value: bytes) -> None:
        self.values[target] = value

    def delete(self, target: str) -> bool:
        return self.values.pop(target, None) is not None


def test_credential_lifecycle_uses_fixed_target_and_never_returns_plain_text() -> None:
    backend = MemoryCredentialBackend()
    key = SecretStr("nvapi-abcdefghijklmnopqrstuvwxyz")

    assert nim_credential_status(backend=backend) is CredentialStatus.ABSENT
    provision_nim_credential(key, backend=backend)
    loaded = load_nim_credential(backend=backend)

    assert set(backend.values) == {NIM_CREDENTIAL_TARGET}
    assert isinstance(loaded, SecretStr)
    assert str(loaded) == "**********"
    assert nim_credential_status(backend=backend) is CredentialStatus.PRESENT
    assert remove_nim_credential(backend=backend)
    assert not remove_nim_credential(backend=backend)


def test_provision_is_create_only_unless_replace_is_explicit() -> None:
    backend = MemoryCredentialBackend()
    original = SecretStr("nvapi-abcdefghijklmnopqrstuvwxyz")
    replacement = SecretStr("nvapi-ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    provision_nim_credential(original, backend=backend)

    with pytest.raises(CredentialStoreError, match="ALREADY_EXISTS"):
        provision_nim_credential(replacement, backend=backend)
    assert load_nim_credential(backend=backend).get_secret_value() == (
        original.get_secret_value()
    )

    provision_nim_credential(replacement, backend=backend, replace=True)
    assert load_nim_credential(backend=backend).get_secret_value() == (
        replacement.get_secret_value()
    )


@pytest.mark.parametrize(
    "payload",
    (
        b"",
        b"wrong-prefix",
        b"nvapi-too short",
        b"\xff\xfe",
        b"x" * 513,
    ),
)
def test_load_rejects_invalid_credential_data(payload: bytes) -> None:
    backend = MemoryCredentialBackend({NIM_CREDENTIAL_TARGET: payload})

    with pytest.raises(CredentialStoreError, match="DATA_INVALID"):
        load_nim_credential(backend=backend)


@pytest.mark.parametrize("value", ("", "wrong", "nvapi-short", "nvapi-bad value"))
def test_provision_rejects_invalid_key_without_writing(value: str) -> None:
    backend = MemoryCredentialBackend()

    with pytest.raises(CredentialStoreError, match="FORMAT_INVALID"):
        provision_nim_credential(SecretStr(value), backend=backend)

    assert backend.values == {}


def test_backend_exception_is_mapped_without_leaking_secret() -> None:
    class FailingBackend(MemoryCredentialBackend):
        def read(self, target: str) -> bytes | None:
            raise RuntimeError(f"private value for {target}")

    with pytest.raises(CredentialStoreError) as captured:
        load_nim_credential(backend=FailingBackend())

    assert str(captured.value) == "CREDENTIAL_READ_FAILED"
    assert "private" not in str(captured.value)
    assert captured.value.__suppress_context__


def test_provision_verifies_the_written_bytes() -> None:
    class CorruptingBackend(MemoryCredentialBackend):
        def write(self, target: str, value: bytes) -> None:
            self.values[target] = b"nvapi-corrupted-value"

    with pytest.raises(CredentialStoreError, match="VERIFICATION_FAILED"):
        provision_nim_credential(
            SecretStr("nvapi-abcdefghijklmnopqrstuvwxyz"),
            backend=CorruptingBackend(),
        )


def test_windows_backend_converts_validated_utf8_for_pywin32() -> None:
    written: list[dict[str, object]] = []

    class Win32CredentialFixture:
        CRED_TYPE_GENERIC = 1
        CRED_PERSIST_LOCAL_MACHINE = 2

        @staticmethod
        def CredWrite(credential: dict[str, object], flags: int) -> None:
            assert flags == 0
            written.append(credential)

    backend = WindowsCredentialBackend()
    backend._modules = lambda: (  # type: ignore[method-assign]
        Win32CredentialFixture(),
        SimpleNamespace(error=RuntimeError),
    )

    backend.write(NIM_CREDENTIAL_TARGET, b"nvapi-abcdefghijklmnopqrstuvwxyz")

    assert written[0]["CredentialBlob"] == "nvapi-abcdefghijklmnopqrstuvwxyz"
    assert isinstance(written[0]["CredentialBlob"], str)


def test_windows_backend_decodes_pywin32_utf16_blob() -> None:
    value = "nvapi-abcdefghijklmnopqrstuvwxyz"

    class Win32CredentialFixture:
        CRED_TYPE_GENERIC = 1

        @staticmethod
        def CredRead(target: str, credential_type: int, flags: int) -> dict[str, object]:
            assert (target, credential_type, flags) == (NIM_CREDENTIAL_TARGET, 1, 0)
            return {"CredentialBlob": value.encode("utf-16-le")}

    backend = WindowsCredentialBackend()
    backend._modules = lambda: (  # type: ignore[method-assign]
        Win32CredentialFixture(),
        SimpleNamespace(error=RuntimeError),
    )

    assert backend.read(NIM_CREDENTIAL_TARGET) == value.encode("utf-8")


def test_windows_backend_falls_back_to_native_read_on_unexpected_pywin32_error() -> None:
    value = b"nvapi-abcdefghijklmnopqrstuvwxyz"

    class BrokenWin32CredentialFixture:
        CRED_TYPE_GENERIC = 1

        @staticmethod
        def CredRead(target: str, credential_type: int, flags: int) -> object:
            raise TypeError("frozen adapter mismatch")

    backend = WindowsCredentialBackend()
    backend._modules = lambda: (  # type: ignore[method-assign]
        BrokenWin32CredentialFixture(),
        SimpleNamespace(error=RuntimeError),
    )
    backend._read_native = lambda target: value  # type: ignore[method-assign]

    assert backend.read(NIM_CREDENTIAL_TARGET) == value
