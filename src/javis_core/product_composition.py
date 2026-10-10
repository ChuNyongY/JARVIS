from __future__ import annotations

import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pydantic import SecretStr

from .agent_session import FrameSource, PlannerContextProvider, PolicyContextProvider
from .always_on_capture import AlwaysOnCapturePump
from .always_on_runtime import AlwaysOnAgentRuntime
from .approval import ApprovalTokenService
from .approval_broker import ApprovalCoordinator, ApprovalPresenter, StrongAuthenticator
from .audit import AuditSink
from .cancellation import CancellationToken
from .executor import Executor
from .executor_router import CapabilityExecutorRouter
from .intent_classifier import TypedIntentClassifier
from .orchestrator import Orchestrator
from .planner import NimChatTransport, TypedPlanner
from .policy import PolicyEngine
from .product_host import LoadedProductHostConfig
from .registry import CapabilityRegistry, default_registry
from .skill_runtime import LocalSkillCatalog, SkillAwarePlanner
from .stt_bundle import LoadedSttBundle, load_stt_bundle
from .verifier import Verifier
from .verifier_router import CapabilityVerifierRouter
from .voice import SpeechToText, VoiceActivityDetector, VoicePipeline, WakeWordDetector
from .voice_adapters import (
    FasterWhisperSpeechToText,
    OpenWakeWordDetector,
    PcmEnergyVad,
    SoundDeviceFrameSource,
)
from .voice_command import CommandPlanner, VoiceCommandCoordinator
from .voice_feedback import VoiceFeedbackPresenter
from .wakeword_bundle import LoadedWakeWordBundle, load_wakeword_bundle


class WakeDetectorFactory(Protocol):
    def __call__(self, bundle: LoadedWakeWordBundle) -> WakeWordDetector: ...


class SttFactory(Protocol):
    def __call__(self, bundle: LoadedSttBundle) -> SpeechToText: ...


class VadFactory(Protocol):
    def __call__(self, *, rms_threshold: int) -> VoiceActivityDetector: ...


class FrameSourceFactory(Protocol):
    def __call__(
        self,
        *,
        frame_ms: int,
        queue_capacity: int,
    ) -> FrameSource: ...


class LanguageTransport(Protocol):
    def complete(self, system_prompt: str, user_prompt: str) -> str: ...


class LanguageTransportFactory(Protocol):
    def __call__(
        self,
        api_key: SecretStr,
        model: str,
    ) -> LanguageTransport: ...


@dataclass(frozen=True)
class ProductVoiceStack:
    frame_source: FrameSource
    pipeline: VoicePipeline


@dataclass(frozen=True)
class ProductLanguageStack:
    classifier: TypedIntentClassifier
    planner: CommandPlanner
    registry: CapabilityRegistry


@dataclass(frozen=True)
class ProductRuntimeStack:
    runtime: AlwaysOnAgentRuntime
    capture_pump: AlwaysOnCapturePump


@dataclass(frozen=True)
class ProductCapabilityStack:
    registry: CapabilityRegistry
    executor: CapabilityExecutorRouter
    verifier: CapabilityVerifierRouter


WakeBundleLoader = Callable[[Path], LoadedWakeWordBundle]
SttBundleLoader = Callable[[Path], LoadedSttBundle]


def build_product_capability_stack(
    executor_routes: Mapping[str, Executor],
    verifier_routes: Mapping[str, Verifier],
    *,
    registry: CapabilityRegistry | None = None,
) -> ProductCapabilityStack:
    """Expose only capabilities with both an executor and an independent verifier."""
    try:
        executors = dict(executor_routes)
        verifiers = dict(verifier_routes)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid product capability routes") from error
    if not executors or not verifiers:
        raise ValueError("product capability routes must not be empty")
    if set(executors) != set(verifiers):
        raise ValueError("product executor and verifier routes must match")
    if any(
        not isinstance(capability_id, str)
        or not callable(getattr(executors[capability_id], "execute", None))
        or not callable(getattr(verifiers[capability_id], "verify", None))
        for capability_id in executors
    ):
        raise ValueError("invalid product capability route")
    base_registry = registry or default_registry()
    specifications = []
    for capability_id in sorted(executors):
        specification = base_registry.get(capability_id)
        if specification is None:
            raise ValueError("product capability is not registered")
        specifications.append(specification)
    narrowed_registry = CapabilityRegistry(specifications)
    return ProductCapabilityStack(
        registry=narrowed_registry,
        executor=CapabilityExecutorRouter("native", executors),
        verifier=CapabilityVerifierRouter(verifiers),
    )


def _nim_transport_factory(api_key: SecretStr, model: str) -> NimChatTransport:
    return NimChatTransport(api_key, model=model)


def build_product_language_stack(
    configuration: LoadedProductHostConfig,
    api_key: SecretStr,
    *,
    registry: CapabilityRegistry | None = None,
    transport_factory: LanguageTransportFactory = _nim_transport_factory,
    skill_catalog: LocalSkillCatalog | None = None,
) -> ProductLanguageStack:
    """Build typed NIM proposal components without making a network request."""
    selected_registry = registry or default_registry()
    classifier_transport = transport_factory(api_key, configuration.settings.nim_model)
    planner_transport = transport_factory(api_key, configuration.settings.nim_model)
    typed_planner = TypedPlanner(
        planner_transport,
        selected_registry,
    )
    return ProductLanguageStack(
        classifier=TypedIntentClassifier(classifier_transport),
        planner=SkillAwarePlanner(typed_planner, skill_catalog)
        if skill_catalog is not None
        else typed_planner,
        registry=selected_registry,
    )


def build_product_runtime_stack(
    voice: ProductVoiceStack,
    language: ProductLanguageStack,
    *,
    executor: Executor,
    verifier: Verifier,
    audit: AuditSink,
    approval_presenter: ApprovalPresenter,
    policy_context_provider: PolicyContextProvider,
    planner_context_provider: PlannerContextProvider | None = None,
    strong_authenticator: StrongAuthenticator | None = None,
    feedback: VoiceFeedbackPresenter | None = None,
    approval_secret: bytes | None = None,
) -> ProductRuntimeStack:
    """Compose the closed loop; injected adapters retain all external authority."""
    secret = secrets.token_bytes(32) if approval_secret is None else approval_secret
    if len(secret) < 32:
        raise ValueError("approval secret must contain at least 32 bytes")
    tokens = ApprovalTokenService(secret)
    approvals = ApprovalCoordinator(
        approval_presenter,
        tokens,
        strong_authenticator,
    )

    def coordinator_factory(cancellation: CancellationToken) -> VoiceCommandCoordinator:
        return VoiceCommandCoordinator(
            language.classifier,
            language.planner,
            cancellation,
        )

    def orchestrator_factory(cancellation: CancellationToken) -> Orchestrator:
        return Orchestrator(
            policy=PolicyEngine(language.registry),
            executor=executor,
            verifier=verifier,
            audit=audit,
            approvals=tokens,
            approval_broker=approvals,
            cancellation=cancellation,
        )

    runtime = AlwaysOnAgentRuntime(
        coordinator_factory=coordinator_factory,
        orchestrator_factory=orchestrator_factory,
        policy_context_provider=policy_context_provider,
        feedback=feedback,
        planner_context_provider=planner_context_provider,
    )
    return ProductRuntimeStack(
        runtime=runtime,
        capture_pump=AlwaysOnCapturePump(
            frame_source=voice.frame_source,
            voice_pipeline=voice.pipeline,
            runtime=runtime,
        ),
    )


def build_product_runtime_stack_from_capabilities(
    voice: ProductVoiceStack,
    language: ProductLanguageStack,
    capabilities: ProductCapabilityStack,
    *,
    audit: AuditSink,
    approval_presenter: ApprovalPresenter,
    policy_context_provider: PolicyContextProvider,
    planner_context_provider: PlannerContextProvider | None = None,
    strong_authenticator: StrongAuthenticator | None = None,
    feedback: VoiceFeedbackPresenter | None = None,
    approval_secret: bytes | None = None,
) -> ProductRuntimeStack:
    """Compose only when NIM, policy, executor, and verifier surfaces are identical."""
    language_capabilities = tuple(item.capability_id for item in language.registry.list())
    executable_capabilities = tuple(
        item.capability_id for item in capabilities.registry.list()
    )
    if language_capabilities != executable_capabilities:
        raise ValueError("product language and executable capability surfaces must match")
    return build_product_runtime_stack(
        voice,
        language,
        executor=capabilities.executor,
        verifier=capabilities.verifier,
        audit=audit,
        approval_presenter=approval_presenter,
        policy_context_provider=policy_context_provider,
        planner_context_provider=planner_context_provider,
        strong_authenticator=strong_authenticator,
        feedback=feedback,
        approval_secret=approval_secret,
    )


def build_product_voice_stack(
    configuration: LoadedProductHostConfig,
    *,
    wake_bundle_loader: WakeBundleLoader = load_wakeword_bundle,
    stt_bundle_loader: SttBundleLoader = load_stt_bundle,
    wake_factory: WakeDetectorFactory = OpenWakeWordDetector.from_local_bundle,
    stt_factory: SttFactory = FasterWhisperSpeechToText.from_local_bundle,
    vad_factory: VadFactory = PcmEnergyVad,
    frame_source_factory: FrameSourceFactory = SoundDeviceFrameSource,
) -> ProductVoiceStack:
    """Build verified local voice objects without opening the microphone."""
    wake_bundle = wake_bundle_loader(configuration.wakeword_manifest)
    stt_bundle = stt_bundle_loader(configuration.stt_manifest)
    wake_detector = wake_factory(wake_bundle)
    stt = stt_factory(stt_bundle)
    settings = configuration.settings
    vad = vad_factory(rms_threshold=settings.vad_rms_threshold)
    frame_source = frame_source_factory(
        frame_ms=settings.microphone_frame_ms,
        queue_capacity=settings.microphone_queue_capacity,
    )
    pipeline = VoicePipeline(
        wake_detector,
        vad,
        stt,
        wake_threshold=settings.wake_threshold,
        end_silence_frames=settings.end_silence_frames,
        max_capture_frames=settings.max_capture_frames,
    )
    return ProductVoiceStack(frame_source=frame_source, pipeline=pipeline)
