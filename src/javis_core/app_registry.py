from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field, StrictBool, ValidationError, model_validator

from .models import FrozenModel
from .process_launch import RegisteredApp


class AppRegistryError(ValueError):
    pass


class AppRegistration(FrozenModel):
    app_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    executable: Path
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    publisher: str | None = Field(default=None, min_length=1)
    runtime_executable: Path | None = None
    runtime_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    runtime_publisher: str | None = Field(default=None, min_length=1)
    direct_launch: StrictBool = True
    single_instance_handoff: StrictBool = False
    window_class: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[^\x00-\x1f\x7f]+$",
    )
    document_launch_mode: Literal["path-only", "browser-new-window"] = "path-only"
    launch_argument: Literal["--new-window"] | None = None

    @model_validator(mode="after")
    def runtime_identity_must_be_complete(self) -> AppRegistration:
        if (self.runtime_executable is None) != (self.runtime_sha256 is None):
            raise ValueError("runtime executable and hash must be provided together")
        if self.runtime_publisher is not None and self.runtime_executable is None:
            raise ValueError("runtime publisher requires a runtime executable")
        if self.single_instance_handoff and self.runtime_executable is not None:
            raise ValueError("single-instance handoff requires the launcher runtime identity")
        if self.single_instance_handoff and not self.direct_launch:
            raise ValueError("single-instance handoff requires direct launch")
        if self.single_instance_handoff and self.window_class is None:
            raise ValueError("single-instance handoff requires an exact window class")
        if self.document_launch_mode == "browser-new-window" and not self.single_instance_handoff:
            raise ValueError("browser document launch requires single-instance handoff")
        if self.launch_argument is not None and not self.single_instance_handoff:
            raise ValueError("fixed launch argument requires single-instance handoff")
        return self


class AppRegistryDocument(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    apps: tuple[AppRegistration, ...]

    @model_validator(mode="after")
    def unique_app_ids(self) -> AppRegistryDocument:
        app_ids = [app.app_id for app in self.apps]
        if len(app_ids) != len(set(app_ids)):
            raise ValueError("app_id must be unique")
        return self


class PublisherVerifier(Protocol):
    def verify(self, executable: Path, expected_subject: str) -> bool: ...


class PowerShellAuthenticodeVerifier:
    """Verify Authenticode status and exact certificate subject via a fixed script."""

    _SCRIPT = """
$ErrorActionPreference = 'Stop'
$signature = Get-AuthenticodeSignature -LiteralPath $env:JAVIS_APP_PATH
if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) { exit 2 }
if ($null -eq $signature.SignerCertificate) { exit 3 }
$matches = [String]::Equals(
  $signature.SignerCertificate.Subject,
  $env:JAVIS_EXPECTED_PUBLISHER,
  [StringComparison]::Ordinal
)
if (-not $matches) { exit 4 }
exit 0
""".strip()

    def verify(self, executable: Path, expected_subject: str) -> bool:
        if os.name != "nt":
            return False
        system_root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR")
        if not system_root:
            return False
        powershell = (
            Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        )
        try:
            powershell = powershell.resolve(strict=True)
        except OSError:
            return False
        encoded = base64.b64encode(self._SCRIPT.encode("utf-16-le")).decode("ascii")
        environment = {
            key: os.environ[key]
            for key in ("SystemRoot", "WINDIR", "TEMP", "TMP")
            if key in os.environ
        }
        environment["JAVIS_APP_PATH"] = str(executable)
        environment["JAVIS_EXPECTED_PUBLISHER"] = expected_subject
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        try:
            result = subprocess.run(  # noqa: S603 - fixed executable and encoded script
                [
                    str(powershell),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-EncodedCommand",
                    encoded,
                ],
                capture_output=True,
                check=False,
                env=environment,
                startupinfo=startupinfo,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0


class AppRegistryLoader:
    def __init__(
        self,
        *,
        publisher_verifier: PublisherVerifier | None = None,
        require_publisher: bool = False,
        max_bytes: int = 1_000_000,
        max_apps: int = 100,
    ) -> None:
        if max_bytes < 2 or max_apps < 1:
            raise ValueError("registry limits must be positive")
        self._publisher_verifier = publisher_verifier or PowerShellAuthenticodeVerifier()
        self._require_publisher = require_publisher
        self._max_bytes = max_bytes
        self._max_apps = max_apps

    def load(self, path: Path) -> dict[str, RegisteredApp]:
        if path.is_symlink():
            raise AppRegistryError("registry symlinks are not allowed")
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise AppRegistryError("could not read app registry") from error
        if len(raw) > self._max_bytes:
            raise AppRegistryError("app registry exceeds size limit")
        try:
            document = AppRegistryDocument.model_validate_json(raw)
        except (ValidationError, ValueError) as error:
            raise AppRegistryError("invalid app registry") from error
        if len(document.apps) > self._max_apps:
            raise AppRegistryError("app registry exceeds app limit")

        loaded: dict[str, RegisteredApp] = {}
        for registration in document.apps:
            loaded[registration.app_id] = self._validate_registration(registration)
        return loaded

    def _validate_registration(self, registration: AppRegistration) -> RegisteredApp:
        resolved = self._validate_file_identity(
            registration.executable,
            registration.sha256,
            registration.publisher,
            label="registered executable",
        )
        runtime_path: Path | None = None
        if registration.runtime_executable is not None:
            assert registration.runtime_sha256 is not None
            runtime_path = self._validate_file_identity(
                registration.runtime_executable,
                registration.runtime_sha256,
                registration.runtime_publisher,
                label="registered runtime executable",
            )
        return RegisteredApp(
            app_id=registration.app_id,
            executable=resolved,
            sha256=registration.sha256,
            publisher=registration.publisher,
            runtime_executable=runtime_path,
            runtime_sha256=registration.runtime_sha256,
            runtime_publisher=registration.runtime_publisher,
            direct_launch=registration.direct_launch,
            single_instance_handoff=registration.single_instance_handoff,
            window_class=registration.window_class,
            document_launch_mode=registration.document_launch_mode,
            launch_argument=registration.launch_argument,
        )

    def _validate_file_identity(
        self,
        executable: Path,
        expected_sha256: str,
        publisher: str | None,
        *,
        label: str,
    ) -> Path:
        if not executable.is_absolute() or executable.is_symlink():
            raise AppRegistryError(f"{label} must be an absolute non-symlink path")
        try:
            resolved = executable.resolve(strict=True)
        except OSError as error:
            raise AppRegistryError(f"{label} is unavailable") from error
        if not resolved.is_file():
            raise AppRegistryError(f"{label} must be a file")
        actual = RegisteredApp.from_path("identity-check", resolved)
        if actual.sha256 != expected_sha256:
            raise AppRegistryError(f"{label} hash mismatch")
        if self._require_publisher and publisher is None:
            raise AppRegistryError(f"{label} publisher is required")
        if publisher is not None and not self._publisher_verifier.verify(resolved, publisher):
            raise AppRegistryError(f"{label} publisher verification failed")
        return resolved


def serialize_registry(document: AppRegistryDocument) -> str:
    """Return canonical JSON for reviewable configuration generation."""

    return json.dumps(
        document.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
