from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from contextlib import suppress
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field

from .models import FrozenModel


class AutostartError(RuntimeError):
    pass


class AutostartStatus(StrEnum):
    ABSENT = "ABSENT"
    REGISTERED = "REGISTERED"
    ALREADY_REGISTERED = "ALREADY_REGISTERED"
    REMOVED = "REMOVED"
    CONFLICT = "CONFLICT"


class ShortcutSpec(FrozenModel):
    target: str = Field(min_length=3, max_length=32_768)
    arguments: str = Field(max_length=32_768)
    working_directory: str = Field(min_length=3, max_length=32_768)
    description: str = Field(min_length=1, max_length=256)


class ShortcutBackend(Protocol):
    def read(self, path: Path) -> ShortcutSpec: ...

    def write(self, path: Path, specification: ShortcutSpec) -> None: ...

    def delete(self, path: Path) -> None: ...


class WindowsComShortcutBackend:
    """Read and write .lnk files through the current-user WScript COM server."""

    @staticmethod
    def _shortcut(path: Path) -> Any:
        try:
            import win32com.client  # type: ignore[import-untyped]

            shell = win32com.client.Dispatch("WScript.Shell")
            return shell.CreateShortcut(str(path))
        except Exception as error:
            raise AutostartError("AUTOSTART_COM_UNAVAILABLE") from error

    def read(self, path: Path) -> ShortcutSpec:
        if not path.is_file() or path.is_symlink():
            raise AutostartError("AUTOSTART_SHORTCUT_INVALID")
        shortcut = self._shortcut(path)
        try:
            return ShortcutSpec(
                target=str(shortcut.TargetPath),
                arguments=str(shortcut.Arguments),
                working_directory=str(shortcut.WorkingDirectory),
                description=str(shortcut.Description),
            )
        except Exception as error:
            raise AutostartError("AUTOSTART_SHORTCUT_UNREADABLE") from error

    def write(self, path: Path, specification: ShortcutSpec) -> None:
        shortcut = self._shortcut(path)
        try:
            shortcut.TargetPath = specification.target
            shortcut.Arguments = specification.arguments
            shortcut.WorkingDirectory = specification.working_directory
            shortcut.Description = specification.description
            shortcut.Save()
        except Exception as error:
            raise AutostartError("AUTOSTART_SHORTCUT_WRITE_FAILED") from error

    def delete(self, path: Path) -> None:
        try:
            path.unlink()
        except OSError as error:
            raise AutostartError("AUTOSTART_SHORTCUT_DELETE_FAILED") from error


StartupDirectoryProvider = Callable[[], Path]


def current_user_startup_directory() -> Path:
    try:
        from win32com.shell import shell, shellcon  # type: ignore[import-untyped]

        value = shell.SHGetFolderPath(0, shellcon.CSIDL_STARTUP, None, 0)
    except Exception as error:
        raise AutostartError("AUTOSTART_FOLDER_UNAVAILABLE") from error
    path = Path(value)
    if not path.is_absolute() or path.is_symlink():
        raise AutostartError("AUTOSTART_FOLDER_INVALID")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise AutostartError("AUTOSTART_FOLDER_UNAVAILABLE") from error
    if not resolved.is_dir():
        raise AutostartError("AUTOSTART_FOLDER_INVALID")
    return resolved


class StartupShortcutRegistrar:
    """Own exactly one current-user Startup shortcut and refuse foreign files."""

    _NAME = "JAVIS.lnk"

    def __init__(
        self,
        target: Path,
        arguments: tuple[str, ...],
        working_directory: Path,
        *,
        startup_directory_provider: StartupDirectoryProvider = current_user_startup_directory,
        backend: ShortcutBackend | None = None,
    ) -> None:
        self._target = _trusted_file(target, "AUTOSTART_TARGET_INVALID")
        self._working_directory = _trusted_directory(
            working_directory,
            "AUTOSTART_WORKING_DIRECTORY_INVALID",
        )
        if any(
            not isinstance(argument, str)
            or not argument
            or "\0" in argument
            or len(argument) > 8_192
            for argument in arguments
        ):
            raise AutostartError("AUTOSTART_ARGUMENTS_INVALID")
        startup = _trusted_directory(
            startup_directory_provider(),
            "AUTOSTART_FOLDER_INVALID",
        )
        self._path = startup / self._NAME
        self._backend = backend or WindowsComShortcutBackend()
        self._specification = ShortcutSpec(
            target=str(self._target),
            arguments=subprocess.list2cmdline(list(arguments)),
            working_directory=str(self._working_directory),
            description="JAVIS PC Agent",
        )

    @property
    def shortcut_path(self) -> Path:
        return self._path

    def status(self) -> AutostartStatus:
        if not self._path.exists():
            return AutostartStatus.ABSENT
        if self._path.is_symlink() or not self._path.is_file():
            return AutostartStatus.CONFLICT
        try:
            observed = self._backend.read(self._path)
        except Exception:
            return AutostartStatus.CONFLICT
        return (
            AutostartStatus.ALREADY_REGISTERED
            if _specifications_match(observed, self._specification)
            else AutostartStatus.CONFLICT
        )

    def register(self) -> AutostartStatus:
        status = self.status()
        if status is AutostartStatus.ALREADY_REGISTERED:
            return status
        if status is not AutostartStatus.ABSENT:
            raise AutostartError("AUTOSTART_SHORTCUT_CONFLICT")
        try:
            descriptor = os.open(
                self._path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
            os.close(descriptor)
        except FileExistsError as error:
            raise AutostartError("AUTOSTART_SHORTCUT_CONFLICT") from error
        except OSError as error:
            raise AutostartError("AUTOSTART_REGISTRATION_FAILED") from error
        try:
            self._backend.write(self._path, self._specification)
            observed = self._backend.read(self._path)
        except Exception as error:
            self._remove_failed_new_shortcut()
            raise AutostartError("AUTOSTART_REGISTRATION_FAILED") from error
        if not _specifications_match(observed, self._specification):
            self._remove_failed_new_shortcut()
            raise AutostartError("AUTOSTART_REGISTRATION_VERIFY_FAILED")
        return AutostartStatus.REGISTERED

    def unregister(self) -> AutostartStatus:
        status = self.status()
        if status is AutostartStatus.ABSENT:
            return status
        if status is not AutostartStatus.ALREADY_REGISTERED:
            raise AutostartError("AUTOSTART_SHORTCUT_NOT_OWNED")
        self._backend.delete(self._path)
        if self._path.exists():
            raise AutostartError("AUTOSTART_REMOVAL_VERIFY_FAILED")
        return AutostartStatus.REMOVED

    def _remove_failed_new_shortcut(self) -> None:
        if self._path.exists() and not self._path.is_symlink():
            with suppress(Exception):
                self._backend.delete(self._path)


def _trusted_file(path: Path, code: str) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise AutostartError(code)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise AutostartError(code) from error
    if not resolved.is_file():
        raise AutostartError(code)
    return resolved


def _trusted_directory(path: Path, code: str) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise AutostartError(code)
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise AutostartError(code) from error
    if not resolved.is_dir():
        raise AutostartError(code)
    return resolved


def _specifications_match(first: ShortcutSpec, second: ShortcutSpec) -> bool:
    return (
        os.path.normcase(first.target) == os.path.normcase(second.target)
        and first.arguments == second.arguments
        and os.path.normcase(first.working_directory)
        == os.path.normcase(second.working_directory)
        and first.description == second.description
    )
