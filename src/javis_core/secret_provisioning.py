from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Protocol

from pydantic import SecretStr

from .windows_acl import WindowsUserOnlyAcl


class SecretProvisioningError(RuntimeError):
    pass


class SecretProtector(Protocol):
    def harden(self, path: Path) -> None: ...

    def verify(self, path: Path) -> bool: ...


def provision_nim_secret(
    project_root: Path,
    key: SecretStr,
    *,
    protector: SecretProtector | None = None,
) -> Path:
    """Create a new protected NIM.txt without accepting a path or key on argv."""
    root = _trusted_directory(project_root)
    destination = root / "NIM.txt"
    if destination.exists() or destination.is_symlink():
        raise SecretProvisioningError("NIM_SECRET_ALREADY_EXISTS")
    value = key.get_secret_value()
    if re.fullmatch(r"nvapi-[A-Za-z0-9_-]{10,480}", value) is None:
        raise SecretProvisioningError("NIM_SECRET_FORMAT_INVALID")
    payload = f"{value}\n".encode()
    if len(payload) > 512:
        raise SecretProvisioningError("NIM_SECRET_FORMAT_INVALID")
    try:
        descriptor = os.open(
            destination,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        try:
            written = os.write(descriptor, payload)
            if written != len(payload):
                raise OSError("partial secret write")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except FileExistsError as error:
        raise SecretProvisioningError("NIM_SECRET_ALREADY_EXISTS") from error
    except OSError as error:
        raise SecretProvisioningError("NIM_SECRET_WRITE_FAILED") from error
    acl = protector or WindowsUserOnlyAcl()
    try:
        acl.harden(destination)
        if not acl.verify(destination):
            raise SecretProvisioningError("NIM_SECRET_PROTECTION_FAILED")
    except Exception as error:
        destination.unlink(missing_ok=True)
        if isinstance(error, SecretProvisioningError):
            raise
        raise SecretProvisioningError("NIM_SECRET_PROTECTION_FAILED") from error
    return destination


def _trusted_directory(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise SecretProvisioningError("PROJECT_ROOT_INVALID")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise SecretProvisioningError("PROJECT_ROOT_INVALID") from error
    if not resolved.is_dir():
        raise SecretProvisioningError("PROJECT_ROOT_INVALID")
    return resolved
