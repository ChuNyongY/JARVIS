from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from javis_core.secret_provisioning import (
    SecretProvisioningError,
    provision_nim_secret,
)


class RecordingProtector:
    def __init__(self, *, verifies: bool = True) -> None:
        self.hardened: list[Path] = []
        self.verifies = verifies

    def harden(self, path: Path) -> None:
        self.hardened.append(path)

    def verify(self, path: Path) -> bool:
        return self.verifies and path in self.hardened


def test_provision_nim_secret_creates_then_protects_fixed_file(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    protector = RecordingProtector()
    secret = SecretStr("nvapi-abcdefghijklmnopqrstuvwxyz")

    destination = provision_nim_secret(root, secret, protector=protector)

    assert destination == root / "NIM.txt"
    assert destination.read_text(encoding="utf-8") == "nvapi-abcdefghijklmnopqrstuvwxyz\n"
    assert protector.hardened == [destination]


def test_provision_nim_secret_never_overwrites_existing_file(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    destination = root / "NIM.txt"
    destination.write_text("existing", encoding="utf-8")

    with pytest.raises(SecretProvisioningError, match="ALREADY_EXISTS"):
        provision_nim_secret(
            root,
            SecretStr("nvapi-abcdefghijklmnopqrstuvwxyz"),
            protector=RecordingProtector(),
        )

    assert destination.read_text(encoding="utf-8") == "existing"


def test_provision_nim_secret_removes_file_when_acl_verification_fails(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()

    with pytest.raises(SecretProvisioningError, match="PROTECTION_FAILED"):
        provision_nim_secret(
            root,
            SecretStr("nvapi-abcdefghijklmnopqrstuvwxyz"),
            protector=RecordingProtector(verifies=False),
        )

    assert not (root / "NIM.txt").exists()


@pytest.mark.parametrize("value", ("", "wrong-prefix", "nvapi-short", "nvapi-bad value"))
def test_provision_nim_secret_rejects_invalid_format(
    tmp_path: Path,
    value: str,
) -> None:
    with pytest.raises(SecretProvisioningError, match="FORMAT_INVALID"):
        provision_nim_secret(
            tmp_path.resolve(),
            SecretStr(value),
            protector=RecordingProtector(),
        )

    assert not (tmp_path / "NIM.txt").exists()
