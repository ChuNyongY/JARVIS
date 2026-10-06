from __future__ import annotations

import hashlib
import re
from pathlib import Path

from pydantic import Field, model_validator

from .app_registry import PowerShellAuthenticodeVerifier, PublisherVerifier
from .models import FrozenModel
from .package_manifest import verify_package_manifest


class ReleaseAuditError(ValueError):
    pass


class ReleaseAuditItem(FrozenModel):
    component: str = Field(pattern=r"^[a-z0-9_.-]{2,64}$")
    passed: bool
    code: str = Field(pattern=r"^[A-Z0-9_]{2,64}$")


class ReleaseAuditReport(FrozenModel):
    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    ready: bool
    items: tuple[ReleaseAuditItem, ...] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def ready_matches_items(self) -> ReleaseAuditReport:
        if self.ready != all(item.passed for item in self.items):
            raise ValueError("release readiness mismatch")
        return self


def audit_windows_release(
    package_root: Path,
    setup_path: Path,
    expected_publisher: str,
    *,
    publisher_verifier: PublisherVerifier | None = None,
) -> ReleaseAuditReport:
    if (
        not expected_publisher
        or len(expected_publisher) > 512
        or "\0" in expected_publisher
        or "\r" in expected_publisher
        or "\n" in expected_publisher
    ):
        raise ReleaseAuditError("EXPECTED_PUBLISHER_INVALID")
    package = _trusted_directory(package_root)
    setup = _trusted_file(setup_path, "SETUP_INVALID")
    verifier = publisher_verifier or PowerShellAuthenticodeVerifier()
    items: list[ReleaseAuditItem] = []
    try:
        verify_package_manifest(package)
    except Exception:
        items.append(_item("package.manifest", False, "MANIFEST_INVALID"))
    else:
        items.append(_item("package.manifest", True, "OK"))
    for component, name in (
        ("package.console-signature", "JAVIS.exe"),
        ("package.tray-signature", "JAVIS-Tray.exe"),
    ):
        try:
            executable = _trusted_file(package / name, "PACKAGE_EXECUTABLE_INVALID")
            passed = verifier.verify(executable, expected_publisher)
        except Exception:
            passed = False
        items.append(_item(component, passed, "SIGNATURE_INVALID"))
    items.append(
        _item(
            "setup.hash",
            _sidecar_matches(setup),
            "SETUP_HASH_INVALID",
        )
    )
    try:
        setup_signed = verifier.verify(setup, expected_publisher)
    except Exception:
        setup_signed = False
    items.append(_item("setup.signature", setup_signed, "SIGNATURE_INVALID"))
    return ReleaseAuditReport(
        ready=all(item.passed for item in items),
        items=tuple(items),
    )


def _sidecar_matches(setup: Path) -> bool:
    sidecar = setup.with_suffix(".exe.sha256")
    if not sidecar.is_file() or sidecar.is_symlink():
        return False
    try:
        raw = sidecar.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return False
    match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)\n", raw)
    if match is None or match.group(2) != setup.name:
        return False
    return match.group(1) == _sha256(setup)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _trusted_directory(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise ReleaseAuditError("PACKAGE_ROOT_INVALID")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ReleaseAuditError("PACKAGE_ROOT_INVALID") from error
    if not resolved.is_dir():
        raise ReleaseAuditError("PACKAGE_ROOT_INVALID")
    return resolved


def _trusted_file(path: Path, code: str) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise ReleaseAuditError(code)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ReleaseAuditError(code) from error
    if not resolved.is_file():
        raise ReleaseAuditError(code)
    return resolved


def _item(component: str, passed: bool, failure_code: str) -> ReleaseAuditItem:
    return ReleaseAuditItem(
        component=component,
        passed=passed,
        code="OK" if passed else failure_code,
    )
