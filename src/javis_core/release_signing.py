from __future__ import annotations

import base64
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from .app_registry import PowerShellAuthenticodeVerifier, PublisherVerifier


class ReleaseSigningError(RuntimeError):
    pass


class ReleaseSigner(Protocol):
    def sign(self, path: Path) -> None: ...


ProcessRunner = Callable[..., subprocess.CompletedProcess[bytes]]


class WindowsAuthenticodeSigner:
    """Sign one release file with an existing CurrentUser code-signing certificate."""

    _SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$certificatePath = 'Cert:\CurrentUser\My\' + $env:JAVIS_CERT_THUMBPRINT
$certificates = @(Get-ChildItem -LiteralPath $certificatePath -CodeSigningCert)
if ($certificates.Count -ne 1) { exit 2 }
$certificate = $certificates[0]
if (-not $certificate.HasPrivateKey) { exit 3 }
$codeSigningOid = '1.3.6.1.5.5.7.3.3'
$hasCodeSigningEku = $false
$ekuType = [System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension]
foreach ($extension in $certificate.Extensions) {
  if ($extension -is $ekuType) {
    foreach ($oid in $extension.EnhancedKeyUsages) {
      if ($oid.Value -eq $codeSigningOid) { $hasCodeSigningEku = $true }
    }
  }
}
if (-not $hasCodeSigningEku) { exit 4 }
$signature = Set-AuthenticodeSignature `
  -LiteralPath $env:JAVIS_SIGN_TARGET `
  -Certificate $certificate `
  -HashAlgorithm SHA256 `
  -TimestampServer $env:JAVIS_TIMESTAMP_URL
if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) { exit 5 }
exit 0
""".strip()

    def __init__(
        self,
        certificate_thumbprint: str,
        expected_publisher: str,
        timestamp_url: str,
        *,
        process_runner: ProcessRunner = subprocess.run,
        publisher_verifier: PublisherVerifier | None = None,
        powershell_path: Path | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        normalized = certificate_thumbprint.replace(" ", "").upper()
        if re.fullmatch(r"(?:[0-9A-F]{40}|[0-9A-F]{64})", normalized) is None:
            raise ReleaseSigningError("SIGNING_THUMBPRINT_INVALID")
        if (
            not expected_publisher
            or len(expected_publisher) > 512
            or any(character in expected_publisher for character in "\0\r\n")
        ):
            raise ReleaseSigningError("SIGNING_PUBLISHER_INVALID")
        parsed_timestamp = urlsplit(timestamp_url)
        if (
            parsed_timestamp.scheme != "https"
            or not parsed_timestamp.netloc
            or parsed_timestamp.username is not None
            or parsed_timestamp.password is not None
            or parsed_timestamp.fragment
            or len(timestamp_url) > 2_048
        ):
            raise ReleaseSigningError("SIGNING_TIMESTAMP_URL_INVALID")
        if not 1 <= timeout_seconds <= 300:
            raise ReleaseSigningError("SIGNING_TIMEOUT_INVALID")
        self._thumbprint = normalized
        self._expected_publisher = expected_publisher
        self._timestamp_url = timestamp_url
        self._runner = process_runner
        self._verifier = publisher_verifier or PowerShellAuthenticodeVerifier()
        self._powershell = powershell_path
        self._timeout = timeout_seconds

    def sign(self, path: Path) -> None:
        target = _trusted_file(path)
        powershell = self._resolve_powershell()
        environment = {
            key: os.environ[key]
            for key in ("SystemRoot", "WINDIR", "TEMP", "TMP")
            if key in os.environ
        }
        environment.update(
            JAVIS_CERT_THUMBPRINT=self._thumbprint,
            JAVIS_SIGN_TARGET=str(target),
            JAVIS_TIMESTAMP_URL=self._timestamp_url,
        )
        encoded = base64.b64encode(self._SCRIPT.encode("utf-16-le")).decode("ascii")
        startupinfo = None
        creationflags = 0
        if os.name == "nt":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            creationflags = subprocess.CREATE_NO_WINDOW
        try:
            result = self._runner(
                [
                    str(powershell),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-EncodedCommand",
                    encoded,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                shell=False,
                env=environment,
                startupinfo=startupinfo,
                creationflags=creationflags,
                timeout=self._timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ReleaseSigningError("SIGNING_PROCESS_FAILED") from error
        if result.returncode != 0:
            raise ReleaseSigningError("SIGNING_PROCESS_FAILED")
        if not self._verifier.verify(target, self._expected_publisher):
            raise ReleaseSigningError("SIGNING_VERIFICATION_FAILED")

    def _resolve_powershell(self) -> Path:
        if self._powershell is not None:
            return _trusted_file(self._powershell)
        system_root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR")
        if not system_root:
            raise ReleaseSigningError("SIGNING_POWERSHELL_UNAVAILABLE")
        candidate = (
            Path(system_root)
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        try:
            return _trusted_file(candidate)
        except ReleaseSigningError as error:
            raise ReleaseSigningError("SIGNING_POWERSHELL_UNAVAILABLE") from error


def _trusted_file(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise ReleaseSigningError("SIGNING_TARGET_INVALID")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ReleaseSigningError("SIGNING_TARGET_INVALID") from error
    if not resolved.is_file():
        raise ReleaseSigningError("SIGNING_TARGET_INVALID")
    return resolved
