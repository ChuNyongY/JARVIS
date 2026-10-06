from __future__ import annotations

import platform
import sqlite3
import sys
from collections.abc import Callable, Iterable
from contextlib import closing, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from enum import StrEnum
from importlib import import_module
from io import StringIO
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .app_registry import AppRegistryLoader
from .models import FrozenModel
from .stt_bundle import load_stt_bundle
from .tts_ipc import TtsWorkerRunner
from .wakeword_bundle import load_wakeword_bundle
from .windows_hello import load_windows_hello_verifier


class DoctorStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


class DoctorItem(FrozenModel):
    component: str = Field(pattern=r"^[a-z0-9_.-]{1,64}$")
    status: DoctorStatus
    code: str = Field(pattern=r"^[A-Z0-9_]{2,64}$")


class DoctorReport(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    passed: bool
    items: tuple[DoctorItem, ...] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def component_names_are_unique(self) -> DoctorReport:
        names = [item.component for item in self.items]
        if len(names) != len(set(names)):
            raise ValueError("doctor component names must be unique")
        if self.passed != all(item.status is not DoctorStatus.FAIL for item in self.items):
            raise ValueError("doctor passed flag does not match items")
        return self


DoctorProbe = Callable[[Path], bool]


@dataclass(frozen=True)
class DoctorCheck:
    component: str
    required: bool
    failure_code: str
    probe: DoctorProbe


def run_doctor(
    project_root: Path,
    *,
    checks: Iterable[DoctorCheck] | None = None,
) -> DoctorReport:
    root = _trusted_root(project_root)
    if root is None:
        item = DoctorItem(
            component="project_root",
            status=DoctorStatus.FAIL,
            code="PROJECT_ROOT_INVALID",
        )
        return DoctorReport(passed=False, items=(item,))
    selected = tuple(checks) if checks is not None else default_doctor_checks()
    if not selected or len(selected) > 32:
        raise ValueError("doctor must contain 1..32 checks")
    if len({check.component for check in selected}) != len(selected):
        raise ValueError("doctor check components must be unique")
    items = tuple(_run_check(check, root) for check in selected)
    return DoctorReport(
        passed=all(item.status is not DoctorStatus.FAIL for item in items),
        items=items,
    )


def default_doctor_checks() -> tuple[DoctorCheck, ...]:
    checks = [
        DoctorCheck("platform.windows", True, "WINDOWS_UNSUPPORTED", _windows_supported),
        DoctorCheck("python", True, "PYTHON_UNSUPPORTED", _python_supported),
        DoctorCheck("sqlite.fts5", True, "SQLITE_FTS5_UNAVAILABLE", _sqlite_fts5),
        DoctorCheck("registry.apps", True, "APP_REGISTRY_INVALID", _app_registry),
        DoctorCheck("approval.hello", True, "HELLO_HELPER_INVALID", _hello_manifest),
        DoctorCheck("model.wakeword", True, "WAKEWORD_BUNDLE_INVALID", _wakeword),
        DoctorCheck("model.stt", True, "STT_BUNDLE_INVALID", _stt),
        DoctorCheck("tts.sapi", True, "SAPI_UNAVAILABLE", _sapi),
    ]
    for component, module in (
        ("dependency.pywinauto", "pywinauto"),
        ("dependency.send2trash", "send2trash"),
        ("dependency.sounddevice", "sounddevice"),
        ("dependency.openwakeword", "openwakeword"),
        ("dependency.faster_whisper", "faster_whisper"),
        ("dependency.pycaw", "pycaw"),
    ):
        checks.append(
            DoctorCheck(
                component,
                False,
                "OPTIONAL_DEPENDENCY_UNAVAILABLE",
                _import_probe(module),
            )
        )
    checks.append(DoctorCheck("build.dotnet_sdk", False, "DOTNET_SDK_MISSING", _dotnet_sdk))
    return tuple(checks)


def _run_check(check: DoctorCheck, root: Path) -> DoctorItem:
    try:
        passed = check.probe(root) is True
    except Exception:
        passed = False
    if passed:
        return DoctorItem(component=check.component, status=DoctorStatus.PASS, code="OK")
    status = DoctorStatus.FAIL if check.required else DoctorStatus.WARN
    return DoctorItem(component=check.component, status=status, code=check.failure_code)


def _trusted_root(path: Path) -> Path | None:
    if not path.is_absolute() or path.is_symlink():
        return None
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.is_dir() else None


def _windows_supported(_root: Path) -> bool:
    return platform.system() == "Windows" and sys.getwindowsversion().build >= 22_000


def _python_supported(_root: Path) -> bool:
    return sys.version_info >= (3, 12)


def _sqlite_fts5(_root: Path) -> bool:
    with closing(sqlite3.connect(":memory:")) as database, database:
        database.execute("CREATE VIRTUAL TABLE probe USING fts5(content)")
        database.execute("DROP TABLE probe")
    return True


def _app_registry(root: Path) -> bool:
    apps = AppRegistryLoader(require_publisher=True).load(root / "config" / "apps.json")
    return bool(apps)


def _hello_manifest(root: Path) -> bool:
    load_windows_hello_verifier(
        root / "config" / "windows_hello_helper.json",
        root,
    )
    return True


def _wakeword(root: Path) -> bool:
    bundle = load_wakeword_bundle(root / "models" / "openwakeword" / "manifest.json")
    return bundle.verify()


def _stt(root: Path) -> bool:
    bundle = load_stt_bundle(root / "models" / "faster-whisper-tiny" / "manifest.json")
    return bundle.verify()


def _sapi(root: Path) -> bool:
    TtsWorkerRunner(root, timeout_seconds=5.0).probe()
    return True


def _module_imports(name: str) -> bool:
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        import_module(name)
    return True


def _import_probe(name: str) -> DoctorProbe:
    def probe(_root: Path) -> bool:
        return _module_imports(name)

    return probe


def _dotnet_sdk(root: Path) -> bool:
    executable = root / ".dotnet" / "dotnet.exe"
    sdk_root = root / ".dotnet" / "sdk"
    return executable.is_file() and sdk_root.is_dir() and any(sdk_root.iterdir())
