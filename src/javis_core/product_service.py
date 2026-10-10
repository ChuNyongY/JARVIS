from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .audit import AuditRetentionPolicy, JsonlAuditSink
from .local_approval import LocalApprovalPresenter, OsBackedStrongAuthenticator, TkApprovalDialog
from .product_composition import (
    ProductCapabilityStack,
    ProductLanguageStack,
    ProductRuntimeStack,
    ProductVoiceStack,
    build_product_language_stack,
    build_product_runtime_stack_from_capabilities,
    build_product_voice_stack,
)
from .product_context import build_windows_planner_context_provider
from .product_host import (
    LoadedProductHostConfig,
    ProductHostError,
    ProductHostLifecycle,
    ProductPreflightReport,
    SecretLoader,
    load_nim_api_key,
    load_product_host_config,
    run_product_preflight,
    run_product_preflight_with_secret_loader,
)
from .product_policy import build_windows_policy_context_provider
from .product_windows import build_windows_product_capabilities
from .skill_runtime import LocalSkillCatalog
from .windows_hello import load_windows_hello_verifier


@dataclass(frozen=True)
class WindowsProductService:
    configuration: LoadedProductHostConfig
    preflight: ProductPreflightReport
    capabilities: ProductCapabilityStack
    skills: LocalSkillCatalog
    voice: ProductVoiceStack
    language: ProductLanguageStack
    runtime: ProductRuntimeStack
    audit: JsonlAuditSink
    lifecycle: ProductHostLifecycle


def build_windows_product_service(
    project_root: Path,
    config_path: Path,
    secret_path: Path,
) -> WindowsProductService:
    """Build the complete local service after preflight, without opening the microphone."""
    preflight = run_product_preflight(project_root, config_path, secret_path)
    return _build_windows_product_service(
        project_root,
        config_path,
        lambda: load_nim_api_key(secret_path),
        preflight,
    )


def build_windows_product_service_with_secret_loader(
    project_root: Path,
    config_path: Path,
    secret_loader: SecretLoader,
) -> WindowsProductService:
    """Build the complete local service using a non-file secret provider."""
    preflight = run_product_preflight_with_secret_loader(
        project_root,
        config_path,
        secret_loader,
    )
    return _build_windows_product_service(
        project_root,
        config_path,
        secret_loader,
        preflight,
    )


def _build_windows_product_service(
    project_root: Path,
    config_path: Path,
    secret_loader: SecretLoader,
    preflight: ProductPreflightReport,
) -> WindowsProductService:
    if not preflight.ready:
        raise ProductHostError("product service preflight failed")
    root = project_root.resolve(strict=True)
    configuration = load_product_host_config(config_path, root)
    api_key = secret_loader()
    capabilities = build_windows_product_capabilities(configuration, root)
    state_directory = root / "state"
    if state_directory.is_symlink():
        raise ProductHostError("product state directory must not be a symlink")
    state_directory.mkdir(exist_ok=True)
    if not state_directory.is_dir():
        raise ProductHostError("product state directory is unavailable")
    skills = LocalSkillCatalog(state_directory / "skills.sqlite3", capabilities.registry)
    language = build_product_language_stack(
        configuration,
        api_key,
        registry=capabilities.registry,
        skill_catalog=skills,
    )
    voice = build_product_voice_stack(configuration)
    audit = JsonlAuditSink(
        state_directory / "audit.jsonl",
        AuditRetentionPolicy(),
    )
    approval_presenter = LocalApprovalPresenter(TkApprovalDialog())
    strong_authenticator = OsBackedStrongAuthenticator(
        load_windows_hello_verifier(configuration.hello_manifest, root)
    )
    policy_context_provider = build_windows_policy_context_provider(
        (root,),
        {"workspace": root},
        configuration.settings.browser_allowed_hosts,
        ("personal",),
    )
    planner_context_provider = build_windows_planner_context_provider(configuration)
    runtime = build_product_runtime_stack_from_capabilities(
        voice,
        language,
        capabilities,
        audit=audit,
        approval_presenter=approval_presenter,
        strong_authenticator=strong_authenticator,
        policy_context_provider=policy_context_provider,
        planner_context_provider=planner_context_provider,
    )
    lifecycle = ProductHostLifecycle(
        preflight=lambda: preflight,
        run_capture=runtime.capture_pump.run,
        request_capture_stop=runtime.capture_pump.request_stop,
        request_runtime_stop=runtime.runtime.close,
    )
    return WindowsProductService(
        configuration=configuration,
        preflight=preflight,
        capabilities=capabilities,
        skills=skills,
        voice=voice,
        language=language,
        runtime=runtime,
        audit=audit,
        lifecycle=lifecycle,
    )
