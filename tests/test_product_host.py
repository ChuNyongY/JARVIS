from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from pydantic import SecretStr

from javis_core.doctor import DoctorItem, DoctorReport, DoctorStatus
from javis_core.product_host import (
    ProductHostError,
    ProductHostLifecycle,
    ProductHostState,
    ProductPreflightItem,
    ProductPreflightReport,
    ProductPreflightStatus,
    load_nim_api_key,
    load_product_host_config,
    run_product_preflight,
    run_product_preflight_with_secret_loader,
)


class AclVerifier:
    def __init__(self, accepted: bool) -> None:
        self.accepted = accepted

    def verify(self, _path: Path) -> bool:
        return self.accepted


def preflight_report(ready: bool) -> ProductPreflightReport:
    status = ProductPreflightStatus.PASS if ready else ProductPreflightStatus.FAIL
    code = "OK" if ready else "PREFLIGHT_FIXTURE_FAILED"
    return ProductPreflightReport(
        ready=ready,
        items=tuple(
            ProductPreflightItem(component=name, status=status, code=code)
            for name in ("config", "secret.nim", "doctor")
        ),
    )


def create_root(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path.resolve()
    config_directory = root / "config"
    wake_directory = root / "models" / "openwakeword"
    stt_directory = root / "models" / "faster-whisper-tiny"
    config_directory.mkdir(parents=True)
    wake_directory.mkdir(parents=True)
    stt_directory.mkdir(parents=True)
    for path in (
        config_directory / "apps.json",
        config_directory / "windows_hello_helper.json",
        wake_directory / "manifest.json",
        stt_directory / "manifest.json",
    ):
        path.write_text("{}", encoding="utf-8")
    config = config_directory / "product_host.json"
    config.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "app_registry": "config/apps.json",
                "hello_manifest": "config/windows_hello_helper.json",
                "wakeword_manifest": "models/openwakeword/manifest.json",
                "stt_manifest": "models/faster-whisper-tiny/manifest.json",
                "browser_allowed_hosts": ["github.com"],
                "nim_model": "z-ai/glm-5.3",
                "wake_threshold": 0.75,
                "vad_rms_threshold": 500,
                "end_silence_frames": 5,
                "max_capture_frames": 1500,
                "microphone_frame_ms": 80,
                "microphone_queue_capacity": 32,
                "hud_poll_interval_ms": 250,
            }
        ),
        encoding="utf-8",
    )
    return root, config


def test_config_loader_resolves_only_declared_project_artifacts(tmp_path: Path) -> None:
    root, config = create_root(tmp_path)

    loaded = load_product_host_config(config.resolve(), root)

    assert loaded.settings.nim_model == "z-ai/glm-5.3"
    assert loaded.settings.browser_allowed_hosts == ("github.com",)
    assert loaded.app_registry == (root / "config" / "apps.json").resolve()
    assert "secret" not in loaded.settings.model_dump_json()


def test_config_loader_rejects_duplicate_secret_and_traversal(tmp_path: Path) -> None:
    root, config = create_root(tmp_path)
    config.write_text(
        '{"schema_version":"1.0","schema_version":"1.0"}',
        encoding="utf-8",
    )
    with pytest.raises(ProductHostError, match="duplicate"):
        load_product_host_config(config.resolve(), root)

    second_root, config = create_root(tmp_path / "second")
    document = json.loads(config.read_text(encoding="utf-8"))
    document["nim_api_key"] = "nvapi-do-not-store-secrets"
    config.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ProductHostError, match="invalid"):
        load_product_host_config(config.resolve(), second_root)

    third_root, config = create_root(tmp_path / "third")
    document = json.loads(config.read_text(encoding="utf-8"))
    document["app_registry"] = "../outside.json"
    config.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ProductHostError, match="invalid"):
        load_product_host_config(config.resolve(), third_root)

    fourth_root, config = create_root(tmp_path / "fourth")
    document = json.loads(config.read_text(encoding="utf-8"))
    document["browser_allowed_hosts"] = ["GitHub.com"]
    config.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ProductHostError, match="invalid"):
        load_product_host_config(config.resolve(), fourth_root)


def test_secret_loader_requires_absolute_protected_bounded_file(tmp_path: Path) -> None:
    secret = (tmp_path / "nim.secret").resolve()
    secret.write_text("nvapi-abcdefghijklmnopqrstuvwxyz", encoding="utf-8")

    loaded = load_nim_api_key(secret, acl_verifier=AclVerifier(True))

    assert isinstance(loaded, SecretStr)
    assert "nvapi" not in str(loaded)
    with pytest.raises(ProductHostError, match="unprotected"):
        load_nim_api_key(secret, acl_verifier=AclVerifier(False))
    secret.write_text("not-a-key", encoding="utf-8")
    with pytest.raises(ProductHostError, match="format"):
        load_nim_api_key(secret, acl_verifier=AclVerifier(True))


def test_preflight_is_privacy_minimized_and_fail_closed(tmp_path: Path) -> None:
    root, config = create_root(tmp_path)
    secret = (root / "nim.secret").resolve()
    secret.write_text("nvapi-abcdefghijklmnopqrstuvwxyz", encoding="utf-8")

    def passing_doctor(_root: Path) -> DoctorReport:
        return DoctorReport(
            passed=True,
            items=(DoctorItem(component="fixture", status=DoctorStatus.PASS, code="OK"),),
        )

    report = run_product_preflight(
        root,
        config.resolve(),
        secret,
        doctor_runner=passing_doctor,
        acl_verifier=AclVerifier(True),
    )
    assert report.ready
    assert all(item.status is ProductPreflightStatus.PASS for item in report.items)
    serialized = report.model_dump_json()
    assert "nvapi" not in serialized
    assert str(root) not in serialized

    failed = run_product_preflight(
        root,
        config.resolve(),
        secret,
        doctor_runner=lambda _root: DoctorReport(
            passed=False,
            items=(
                DoctorItem(
                    component="fixture",
                    status=DoctorStatus.FAIL,
                    code="FIXTURE_FAILED",
                ),
            ),
        ),
        acl_verifier=AclVerifier(False),
    )
    assert not failed.ready
    assert [item.code for item in failed.items] == [
        "OK",
        "NIM_SECRET_INVALID",
        "DOCTOR_FAILED",
    ]


def test_preflight_accepts_an_injected_secret_loader_without_serializing_it(
    tmp_path: Path,
) -> None:
    root, config = create_root(tmp_path)
    calls: list[str] = []

    def passing_doctor(_root: Path) -> DoctorReport:
        return DoctorReport(
            passed=True,
            items=(DoctorItem(component="fixture", status=DoctorStatus.PASS, code="OK"),),
        )

    report = run_product_preflight_with_secret_loader(
        root,
        config.resolve(),
        lambda: (calls.append("secret"), SecretStr("nvapi-private-value"))[1],
        doctor_runner=passing_doctor,
    )

    assert report.ready
    assert calls == ["secret"]
    assert "private" not in report.model_dump_json()

    failed = run_product_preflight_with_secret_loader(
        root,
        config.resolve(),
        lambda: "not-a-secret",  # type: ignore[return-value]
        doctor_runner=passing_doctor,
    )
    assert not failed.ready
    assert failed.items[1].code == "NIM_SECRET_INVALID"


def test_lifecycle_never_starts_capture_when_preflight_fails() -> None:
    calls: list[str] = []
    host = ProductHostLifecycle(
        preflight=lambda: preflight_report(False),
        run_capture=lambda: calls.append("capture"),
        request_capture_stop=lambda: calls.append("capture-stop"),
        request_runtime_stop=lambda: calls.append("runtime-stop"),
    )

    status = host.start()

    assert status.state is ProductHostState.FAILED
    assert status.reason == "PREFLIGHT_FAILED"
    assert calls == []


def test_lifecycle_starts_capture_and_stops_in_required_order() -> None:
    capture_started = threading.Event()
    capture_release = threading.Event()
    calls: list[str] = []

    def run_capture() -> None:
        capture_started.set()
        assert capture_release.wait(timeout=2)

    def stop_capture() -> None:
        calls.append("capture-stop")
        capture_release.set()

    host = ProductHostLifecycle(
        preflight=lambda: preflight_report(True),
        run_capture=run_capture,
        request_capture_stop=stop_capture,
        request_runtime_stop=lambda: calls.append("runtime-stop"),
    )

    started = host.start()
    assert capture_started.wait(timeout=1)
    stopped = host.stop(timeout_seconds=1)

    assert started.state is ProductHostState.RUNNING
    assert stopped.state is ProductHostState.STOPPED
    assert stopped.reason == "HOST_STOPPED"
    assert calls == ["capture-stop", "runtime-stop"]


def test_lifecycle_redacts_capture_failure_and_rejects_second_start() -> None:
    def fail_capture() -> None:
        raise RuntimeError("secret transcript C:\\private\\file.txt")

    host = ProductHostLifecycle(
        preflight=lambda: preflight_report(True),
        run_capture=fail_capture,
        request_capture_stop=lambda: None,
        request_runtime_stop=lambda: None,
    )

    host.start()
    status = host.wait(timeout_seconds=1)

    assert status.state is ProductHostState.FAILED
    assert status.reason == "CAPTURE_FAILED"
    assert "secret" not in status.model_dump_json()
    with pytest.raises(ProductHostError, match="only start once"):
        host.start()


def test_lifecycle_stop_timeout_fails_closed() -> None:
    capture_started = threading.Event()
    capture_release = threading.Event()

    def run_capture() -> None:
        capture_started.set()
        assert capture_release.wait(timeout=2)

    host = ProductHostLifecycle(
        preflight=lambda: preflight_report(True),
        run_capture=run_capture,
        request_capture_stop=lambda: None,
        request_runtime_stop=lambda: None,
    )
    host.start()
    assert capture_started.wait(timeout=1)

    status = host.stop(timeout_seconds=0.01)
    capture_release.set()
    host.wait(timeout_seconds=1)

    assert status.state is ProductHostState.FAILED
    assert status.reason == "CAPTURE_STOP_TIMEOUT"
