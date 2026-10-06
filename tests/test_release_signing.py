from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from javis_core.release_signing import (
    ReleaseSigningError,
    WindowsAuthenticodeSigner,
)


class AcceptingVerifier:
    def verify(self, executable: Path, expected_subject: str) -> bool:
        return executable.is_file() and expected_subject == "CN=Trusted Publisher"


class RecordingRunner:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        self.calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, self.returncode, b"", b"")


def test_signer_uses_fixed_powershell_and_minimal_environment(tmp_path: Path) -> None:
    powershell = tmp_path / "powershell.exe"
    powershell.write_bytes(b"fixture")
    target = tmp_path / "JAVIS.exe"
    target.write_bytes(b"fixture")
    runner = RecordingRunner()
    signer = WindowsAuthenticodeSigner(
        "A" * 40,
        "CN=Trusted Publisher",
        "https://timestamp.example.test",
        process_runner=runner,
        publisher_verifier=AcceptingVerifier(),
        powershell_path=powershell.resolve(),
    )

    signer.sign(target.resolve())

    command, options = runner.calls[0]
    assert command[:4] == [
        str(powershell.resolve()),
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
    ]
    assert options["shell"] is False
    environment = options["env"]
    assert isinstance(environment, dict)
    assert environment["JAVIS_CERT_THUMBPRINT"] == "A" * 40
    assert environment["JAVIS_SIGN_TARGET"] == str(target.resolve())
    assert "NVIDIA_API_KEY" not in environment


@pytest.mark.parametrize(
    ("thumbprint", "url", "code"),
    (
        ("short", "https://timestamp.test", "THUMBPRINT_INVALID"),
        ("A" * 40, "http://timestamp.test", "TIMESTAMP_URL_INVALID"),
        ("A" * 40, "https://user:pass@timestamp.test", "TIMESTAMP_URL_INVALID"),
    ),
)
def test_signer_rejects_invalid_identity_or_timestamp(
    thumbprint: str,
    url: str,
    code: str,
) -> None:
    with pytest.raises(ReleaseSigningError, match=code):
        WindowsAuthenticodeSigner(thumbprint, "CN=Trusted", url)


def test_signer_fails_closed_on_process_failure(tmp_path: Path) -> None:
    powershell = tmp_path / "powershell.exe"
    powershell.write_bytes(b"fixture")
    target = tmp_path / "JAVIS.exe"
    target.write_bytes(b"fixture")
    signer = WindowsAuthenticodeSigner(
        "B" * 40,
        "CN=Trusted Publisher",
        "https://timestamp.example.test",
        process_runner=RecordingRunner(returncode=5),
        publisher_verifier=AcceptingVerifier(),
        powershell_path=powershell.resolve(),
    )

    with pytest.raises(ReleaseSigningError, match="PROCESS_FAILED"):
        signer.sign(target.resolve())
