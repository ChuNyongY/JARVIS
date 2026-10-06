from __future__ import annotations

import json
import re
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path, PurePosixPath
from threading import Lock, Thread
from typing import Literal, Protocol

from pydantic import Field, SecretStr, StrictFloat, StrictInt, field_validator, model_validator

from .doctor import DoctorReport, run_doctor
from .models import FrozenModel
from .windows_acl import WindowsUserOnlyAcl


class ProductHostError(ValueError):
    pass


class SecretAclVerifier(Protocol):
    def verify(self, path: Path) -> bool: ...


class ProductHostConfig(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    app_registry: str
    hello_manifest: str
    wakeword_manifest: str
    stt_manifest: str
    browser_allowed_hosts: tuple[str, ...] = Field(min_length=1, max_length=32)
    nim_model: str = Field(
        min_length=3,
        max_length=128,
        pattern=r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]+$",
    )
    wake_threshold: StrictFloat = Field(ge=0.5, le=0.99)
    vad_rms_threshold: StrictInt = Field(ge=1, le=32_767)
    end_silence_frames: StrictInt = Field(ge=1, le=50)
    max_capture_frames: StrictInt = Field(ge=10, le=1_500)
    microphone_frame_ms: StrictInt = Field(ge=10, le=200)
    microphone_queue_capacity: StrictInt = Field(ge=4, le=256)
    hud_poll_interval_ms: StrictInt = Field(ge=100, le=5_000)

    @field_validator(
        "app_registry",
        "hello_manifest",
        "wakeword_manifest",
        "stt_manifest",
    )
    @classmethod
    def artifact_paths_are_relative_json(cls, value: str) -> str:
        if "\\" in value or ":" in value:
            raise ValueError("artifact path must use relative POSIX form")
        parsed = PurePosixPath(value)
        if (
            parsed.is_absolute()
            or any(part in {"", ".", ".."} for part in parsed.parts)
            or parsed.suffix.lower() != ".json"
        ):
            raise ValueError("artifact path must be a non-traversing relative JSON path")
        return value

    @field_validator("browser_allowed_hosts")
    @classmethod
    def browser_hosts_are_canonical_ascii_dns_names(
        cls, value: tuple[str, ...]
    ) -> tuple[str, ...]:
        from .browser_navigation import _validate_allowed_host

        if len(value) != len(set(value)) or any(
            _validate_allowed_host(host) is None for host in value
        ):
            raise ValueError("browser hosts must be unique canonical ASCII DNS names")
        return value


class LoadedProductHostConfig(FrozenModel):
    settings: ProductHostConfig
    app_registry: Path
    hello_manifest: Path
    wakeword_manifest: Path
    stt_manifest: Path


class ProductPreflightStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class ProductPreflightItem(FrozenModel):
    component: str = Field(pattern=r"^[a-z0-9_.-]{1,64}$")
    status: ProductPreflightStatus
    code: str = Field(pattern=r"^[A-Z0-9_]{2,64}$")


class ProductPreflightReport(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    ready: bool
    items: tuple[ProductPreflightItem, ...] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def summary_matches_items(self) -> ProductPreflightReport:
        if len({item.component for item in self.items}) != len(self.items):
            raise ValueError("preflight components must be unique")
        if self.ready != all(item.status is ProductPreflightStatus.PASS for item in self.items):
            raise ValueError("preflight ready flag does not match items")
        return self


DoctorRunner = Callable[[Path], DoctorReport]
SecretLoader = Callable[[], SecretStr]
ProductPreflightRunner = Callable[[], ProductPreflightReport]
ProductCaptureRunner = Callable[[], object]
ProductStopRequest = Callable[[], object]


class ProductHostState(StrEnum):
    CREATED = "CREATED"
    PREFLIGHTING = "PREFLIGHTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"


class ProductHostStatusSnapshot(FrozenModel):
    state: ProductHostState
    reason: str = Field(pattern=r"^[A-Z0-9_]{2,64}$")
    sequence: int = Field(ge=0)


class ProductHostLifecycle:
    """Start capture only after preflight and coordinate an explicit bounded stop."""

    def __init__(
        self,
        *,
        preflight: ProductPreflightRunner,
        run_capture: ProductCaptureRunner,
        request_capture_stop: ProductStopRequest,
        request_runtime_stop: ProductStopRequest,
        thread_factory: Callable[..., Thread] = Thread,
    ) -> None:
        self._preflight = preflight
        self._run_capture = run_capture
        self._request_capture_stop = request_capture_stop
        self._request_runtime_stop = request_runtime_stop
        self._thread_factory = thread_factory
        self._lock = Lock()
        self._worker: Thread | None = None
        self._status = ProductHostStatusSnapshot(
            state=ProductHostState.CREATED,
            reason="HOST_CREATED",
            sequence=0,
        )

    def status_snapshot(self) -> ProductHostStatusSnapshot:
        with self._lock:
            return self._status.model_copy()

    def start(self) -> ProductHostStatusSnapshot:
        self._begin_preflight()
        try:
            preflight = self._preflight()
        except Exception:
            return self._transition(ProductHostState.FAILED, "PREFLIGHT_EXCEPTION")
        if not preflight.ready:
            return self._transition(ProductHostState.FAILED, "PREFLIGHT_FAILED")
        with self._lock:
            if self._status.state is not ProductHostState.PREFLIGHTING:
                return self._status.model_copy()
            try:
                worker = self._thread_factory(
                    target=self._capture_worker,
                    name="javis-capture-host",
                    daemon=False,
                )
                self._worker = worker
                worker.start()
            except Exception:
                self._worker = None
                return self._transition_locked(
                    ProductHostState.FAILED,
                    "CAPTURE_THREAD_START_FAILED",
                )
            return self._transition_locked(ProductHostState.RUNNING, "CAPTURE_STARTED")

    def _begin_preflight(self) -> None:
        with self._lock:
            if self._status.state is not ProductHostState.CREATED:
                raise ProductHostError("product host may only start once")
            self._transition_locked(ProductHostState.PREFLIGHTING, "PREFLIGHT_STARTED")

    def stop(self, *, timeout_seconds: float = 5.0) -> ProductHostStatusSnapshot:
        if isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be within 0..30")
        with self._lock:
            state = self._status.state
            if state in {ProductHostState.STOPPED, ProductHostState.FAILED}:
                return self._status.model_copy()
            if state is ProductHostState.CREATED:
                return self._transition_locked(ProductHostState.STOPPED, "STOPPED_BEFORE_START")
            worker = self._worker
            self._transition_locked(ProductHostState.STOPPING, "STOP_REQUESTED")
        stop_failed = False
        for request in (self._request_capture_stop, self._request_runtime_stop):
            try:
                request()
            except Exception:
                stop_failed = True
        if worker is not None:
            worker.join(timeout=timeout_seconds)
            if worker.is_alive():
                return self._transition(ProductHostState.FAILED, "CAPTURE_STOP_TIMEOUT")
        with self._lock:
            if self._status.state is ProductHostState.FAILED:
                return self._status.model_copy()
        if stop_failed:
            return self._transition(ProductHostState.FAILED, "STOP_CALLBACK_FAILED")
        return self._transition(ProductHostState.STOPPED, "HOST_STOPPED")

    def wait(self, *, timeout_seconds: float = 5.0) -> ProductHostStatusSnapshot:
        if isinstance(timeout_seconds, bool) or not 0 < timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be within 0..30")
        with self._lock:
            worker = self._worker
        if worker is not None:
            worker.join(timeout=timeout_seconds)
        return self.status_snapshot()

    def _capture_worker(self) -> None:
        try:
            self._run_capture()
        except Exception:
            self._transition(ProductHostState.FAILED, "CAPTURE_FAILED")
            return
        with self._lock:
            if self._status.state is ProductHostState.RUNNING:
                self._transition_locked(ProductHostState.STOPPED, "CAPTURE_ENDED")

    def _transition(
        self,
        state: ProductHostState,
        reason: str,
    ) -> ProductHostStatusSnapshot:
        with self._lock:
            return self._transition_locked(state, reason)

    def _transition_locked(
        self,
        state: ProductHostState,
        reason: str,
    ) -> ProductHostStatusSnapshot:
        self._status = ProductHostStatusSnapshot(
            state=state,
            reason=reason,
            sequence=self._status.sequence + 1,
        )
        return self._status.model_copy()


def load_product_host_config(
    path: Path,
    project_root: Path,
    *,
    max_bytes: int = 32 * 1024,
) -> LoadedProductHostConfig:
    root = _trusted_root(project_root)
    if max_bytes < 2 or root is None:
        raise ProductHostError("invalid product host root or loader limit")
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ProductHostError("invalid product host config file")
    try:
        resolved_config = path.resolve(strict=True)
        config_root = (root / "config").resolve(strict=True)
    except OSError as error:
        raise ProductHostError("product host config is unavailable") from error
    if not resolved_config.is_relative_to(config_root):
        raise ProductHostError("product host config must remain under project config")
    _reject_symlink_components(root, resolved_config)
    try:
        if resolved_config.stat().st_size > max_bytes:
            raise ProductHostError("product host config exceeded size limit")
        raw = resolved_config.read_bytes()
        if len(raw) > max_bytes:
            raise ProductHostError("product host config exceeded size limit")
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
        settings = ProductHostConfig.model_validate(document)
    except ProductHostError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ProductHostError("invalid product host config") from error
    resolved = {
        name: _resolve_artifact(root, value)
        for name, value in (
            ("app_registry", settings.app_registry),
            ("hello_manifest", settings.hello_manifest),
            ("wakeword_manifest", settings.wakeword_manifest),
            ("stt_manifest", settings.stt_manifest),
        )
    }
    return LoadedProductHostConfig(settings=settings, **resolved)


def load_nim_api_key(
    path: Path,
    *,
    acl_verifier: SecretAclVerifier | None = None,
    max_bytes: int = 512,
) -> SecretStr:
    verifier = acl_verifier or WindowsUserOnlyAcl()
    if (
        max_bytes < 16
        or not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
        or not verifier.verify(path)
    ):
        raise ProductHostError("invalid or unprotected NIM secret file")
    try:
        size = path.stat().st_size
        if not 1 <= size <= max_bytes:
            raise ProductHostError("NIM secret file exceeded size limit")
        raw = path.read_bytes()
        if len(raw) > max_bytes:
            raise ProductHostError("NIM secret file exceeded size limit")
        decoded = raw.decode("utf-8")
    except ProductHostError:
        raise
    except (OSError, UnicodeDecodeError) as error:
        raise ProductHostError("NIM secret file is unreadable") from error
    key = decoded.strip()
    if (
        decoded not in {key, f"{key}\n", f"{key}\r\n"}
        or re.fullmatch(r"nvapi-[A-Za-z0-9_-]{10,480}", key) is None
    ):
        raise ProductHostError("invalid NIM secret format")
    return SecretStr(key)


def run_product_preflight(
    project_root: Path,
    config_path: Path,
    secret_path: Path,
    *,
    doctor_runner: DoctorRunner = run_doctor,
    acl_verifier: SecretAclVerifier | None = None,
) -> ProductPreflightReport:
    return run_product_preflight_with_secret_loader(
        project_root,
        config_path,
        lambda: load_nim_api_key(secret_path, acl_verifier=acl_verifier),
        doctor_runner=doctor_runner,
    )


def run_product_preflight_with_secret_loader(
    project_root: Path,
    config_path: Path,
    secret_loader: SecretLoader,
    *,
    doctor_runner: DoctorRunner = run_doctor,
) -> ProductPreflightReport:
    items: list[ProductPreflightItem] = []
    try:
        load_product_host_config(config_path, project_root)
    except Exception:
        items.append(_preflight_item("config", False, "CONFIG_INVALID"))
    else:
        items.append(_preflight_item("config", True, "OK"))
    try:
        secret = secret_loader()
        if not isinstance(secret, SecretStr):
            raise ProductHostError("secret loader returned an invalid value")
    except Exception:
        items.append(_preflight_item("secret.nim", False, "NIM_SECRET_INVALID"))
    else:
        items.append(_preflight_item("secret.nim", True, "OK"))
    try:
        doctor = doctor_runner(project_root)
        doctor_passed = doctor.passed
    except Exception:
        doctor_passed = False
    items.append(_preflight_item("doctor", doctor_passed, "DOCTOR_FAILED"))
    return ProductPreflightReport(
        ready=all(item.status is ProductPreflightStatus.PASS for item in items),
        items=tuple(items),
    )


def _preflight_item(component: str, passed: bool, failure_code: str) -> ProductPreflightItem:
    return ProductPreflightItem(
        component=component,
        status=ProductPreflightStatus.PASS if passed else ProductPreflightStatus.FAIL,
        code="OK" if passed else failure_code,
    )


def _trusted_root(path: Path) -> Path | None:
    if not path.is_absolute() or path.is_symlink():
        return None
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.is_dir() else None


def _resolve_artifact(root: Path, relative: str) -> Path:
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    _reject_symlink_components(root, candidate)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ProductHostError("product host artifact is unavailable") from error
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ProductHostError("product host artifact escaped its root")
    return resolved


def _reject_symlink_components(root: Path, candidate: Path) -> None:
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise ProductHostError("product host path escaped its root") from error
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            raise ProductHostError("product host path must not contain symlinks")


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProductHostError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ProductHostError(f"non-finite JSON number: {value}")
