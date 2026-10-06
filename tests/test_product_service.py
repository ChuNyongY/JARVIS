from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from pytest import MonkeyPatch

from javis_core import product_service
from javis_core.product_host import (
    ProductHostError,
    ProductPreflightItem,
    ProductPreflightReport,
    ProductPreflightStatus,
)


def failed_preflight() -> ProductPreflightReport:
    return ProductPreflightReport(
        ready=False,
        items=(
            ProductPreflightItem(
                component="config",
                status=ProductPreflightStatus.FAIL,
                code="CONFIG_INVALID",
            ),
            ProductPreflightItem(
                component="secret.nim",
                status=ProductPreflightStatus.FAIL,
                code="NIM_SECRET_INVALID",
            ),
            ProductPreflightItem(
                component="doctor",
                status=ProductPreflightStatus.FAIL,
                code="DOCTOR_FAILED",
            ),
        ),
    )


def passed_preflight() -> ProductPreflightReport:
    return ProductPreflightReport(
        ready=True,
        items=(
            ProductPreflightItem(
                component="config",
                status=ProductPreflightStatus.PASS,
                code="OK",
            ),
            ProductPreflightItem(
                component="secret.nim",
                status=ProductPreflightStatus.PASS,
                code="OK",
            ),
            ProductPreflightItem(
                component="doctor",
                status=ProductPreflightStatus.PASS,
                code="OK",
            ),
        ),
    )


def test_service_build_stops_before_loading_any_component_when_preflight_fails(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        product_service,
        "run_product_preflight",
        lambda *_args: failed_preflight(),
    )
    monkeypatch.setattr(
        product_service,
        "load_product_host_config",
        lambda *_args: calls.append("config"),
    )

    with pytest.raises(ProductHostError, match="preflight failed"):
        product_service.build_windows_product_service(
            tmp_path.resolve(),
            (tmp_path / "config.json").resolve(),
            (tmp_path / "secret.txt").resolve(),
        )

    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_credential_service_stops_before_loading_components_when_preflight_fails(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        product_service,
        "run_product_preflight_with_secret_loader",
        lambda *_args: failed_preflight(),
    )
    monkeypatch.setattr(
        product_service,
        "load_product_host_config",
        lambda *_args: calls.append("config"),
    )

    with pytest.raises(ProductHostError, match="preflight failed"):
        product_service.build_windows_product_service_with_secret_loader(
            tmp_path.resolve(),
            (tmp_path / "config.json").resolve(),
            lambda: pytest.fail("secret must not be loaded"),
        )

    assert calls == []


def test_service_wires_privacy_context_and_personal_memory_policy(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
    configuration = SimpleNamespace(
        settings=SimpleNamespace(browser_allowed_hosts=("github.com",)),
        hello_manifest=(tmp_path / "hello.json").resolve(),
    )
    capabilities = SimpleNamespace(registry=object())
    voice = object()
    language = object()
    audit = object()
    policy_provider = object()
    planner_provider = object()
    runtime = SimpleNamespace(
        capture_pump=SimpleNamespace(run=lambda: None, request_stop=lambda: None),
        runtime=SimpleNamespace(close=lambda: None),
    )
    lifecycle = object()
    observed: dict[str, object] = {}

    monkeypatch.setattr(
        product_service,
        "run_product_preflight_with_secret_loader",
        lambda *_args: passed_preflight(),
    )
    monkeypatch.setattr(
        product_service, "load_product_host_config", lambda *_args: configuration
    )
    monkeypatch.setattr(
        product_service,
        "build_windows_product_capabilities",
        lambda received, _root: (
            observed.setdefault("capability_configuration", received),
            capabilities,
        )[1],
    )
    monkeypatch.setattr(
        product_service, "build_product_language_stack", lambda *_args, **_kw: language
    )
    monkeypatch.setattr(product_service, "build_product_voice_stack", lambda *_args: voice)
    monkeypatch.setattr(product_service, "JsonlAuditSink", lambda *_args: audit)
    monkeypatch.setattr(product_service, "TkApprovalDialog", lambda: object())
    monkeypatch.setattr(product_service, "LocalApprovalPresenter", lambda *_args: object())
    monkeypatch.setattr(
        product_service, "load_windows_hello_verifier", lambda *_args: object()
    )
    monkeypatch.setattr(
        product_service, "OsBackedStrongAuthenticator", lambda *_args: object()
    )

    def policy_builder(*args: object) -> object:
        observed["policy_args"] = args
        return policy_provider

    monkeypatch.setattr(
        product_service, "build_windows_policy_context_provider", policy_builder
    )
    monkeypatch.setattr(
        product_service,
        "build_windows_planner_context_provider",
        lambda received: (
            observed.setdefault("planner_configuration", received),
            planner_provider,
        )[1],
    )

    def runtime_builder(*args: object, **kwargs: object) -> object:
        observed["runtime_args"] = args
        observed["runtime_kwargs"] = kwargs
        return runtime

    monkeypatch.setattr(
        product_service,
        "build_product_runtime_stack_from_capabilities",
        runtime_builder,
    )
    monkeypatch.setattr(
        product_service,
        "ProductHostLifecycle",
        lambda **_kwargs: lifecycle,
    )

    service = product_service.build_windows_product_service_with_secret_loader(
        tmp_path.resolve(),
        (tmp_path / "config.json").resolve(),
        lambda: SecretStr("nvapi-abcdefghijklmnopqrstuvwxyz"),
    )

    assert observed["capability_configuration"] is configuration
    assert observed["planner_configuration"] is configuration
    assert observed["policy_args"] == (
        (tmp_path.resolve(),),
        {"workspace": tmp_path.resolve()},
        ("github.com",),
        ("personal",),
    )
    runtime_kwargs = observed["runtime_kwargs"]
    assert isinstance(runtime_kwargs, dict)
    assert runtime_kwargs["policy_context_provider"] is policy_provider
    assert runtime_kwargs["planner_context_provider"] is planner_provider
    assert service.lifecycle is lifecycle
