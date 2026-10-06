from __future__ import annotations

from pathlib import Path

import pytest

from javis_core.autostart import (
    AutostartError,
    AutostartStatus,
    ShortcutSpec,
    StartupShortcutRegistrar,
)


class MemoryShortcutBackend:
    def __init__(self) -> None:
        self.items: dict[Path, ShortcutSpec] = {}

    def read(self, path: Path) -> ShortcutSpec:
        return self.items[path]

    def write(self, path: Path, specification: ShortcutSpec) -> None:
        path.write_bytes(b"shortcut")
        self.items[path] = specification

    def delete(self, path: Path) -> None:
        path.unlink()
        self.items.pop(path, None)


def registrar(tmp_path: Path, backend: MemoryShortcutBackend) -> StartupShortcutRegistrar:
    target = tmp_path / "javis.exe"
    target.write_bytes(b"fixture")
    startup = tmp_path / "Startup"
    startup.mkdir()
    return StartupShortcutRegistrar(
        target,
        ("run-product-tray", "C:\\Project Root"),
        tmp_path,
        startup_directory_provider=lambda: startup,
        backend=backend,
    )


def test_register_is_verified_idempotent_and_removable(tmp_path: Path) -> None:
    backend = MemoryShortcutBackend()
    registration = registrar(tmp_path, backend)

    assert registration.status() is AutostartStatus.ABSENT
    assert registration.register() is AutostartStatus.REGISTERED
    assert registration.status() is AutostartStatus.ALREADY_REGISTERED
    assert registration.register() is AutostartStatus.ALREADY_REGISTERED
    assert registration.unregister() is AutostartStatus.REMOVED
    assert registration.status() is AutostartStatus.ABSENT


def test_foreign_shortcut_is_never_overwritten_or_removed(tmp_path: Path) -> None:
    backend = MemoryShortcutBackend()
    registration = registrar(tmp_path, backend)
    path = registration.shortcut_path
    path.write_bytes(b"foreign")
    backend.items[path] = ShortcutSpec(
        target="C:\\Other\\app.exe",
        arguments="--other",
        working_directory="C:\\Other",
        description="Other app",
    )

    assert registration.status() is AutostartStatus.CONFLICT
    with pytest.raises(AutostartError, match="CONFLICT"):
        registration.register()
    with pytest.raises(AutostartError, match="NOT_OWNED"):
        registration.unregister()
    assert path.read_bytes() == b"foreign"


def test_invalid_target_and_arguments_are_rejected(tmp_path: Path) -> None:
    startup = tmp_path / "Startup"
    startup.mkdir()
    with pytest.raises(AutostartError, match="TARGET_INVALID"):
        StartupShortcutRegistrar(
            Path("relative.exe"),
            ("run",),
            tmp_path,
            startup_directory_provider=lambda: startup,
        )
    target = tmp_path / "javis.exe"
    target.write_bytes(b"fixture")
    with pytest.raises(AutostartError, match="ARGUMENTS_INVALID"):
        StartupShortcutRegistrar(
            target,
            ("bad\0argument",),
            tmp_path,
            startup_directory_provider=lambda: startup,
        )
