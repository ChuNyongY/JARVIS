from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr

from javis_core.approval_broker import ApprovalRequest, ApprovalResponse
from javis_core.audit import InMemoryAuditSink
from javis_core.cancellation import CancellationToken
from javis_core.executor import MockExecutor
from javis_core.models import Action, Plan, PolicyContext, Postcondition
from javis_core.product_composition import (
    ProductCapabilityStack,
    ProductLanguageStack,
    ProductRuntimeStack,
    ProductVoiceStack,
    build_product_capability_stack,
    build_product_language_stack,
    build_product_runtime_stack,
    build_product_runtime_stack_from_capabilities,
    build_product_voice_stack,
)
from javis_core.product_host import load_product_host_config
from javis_core.stt_bundle import LoadedSttBundle, SttBundleFiles, SttModelFile
from javis_core.verifier import PredicateVerifier
from javis_core.voice import AudioFrame, SpeechToText, VoicePipeline, WakeWordDetector
from javis_core.wakeword_bundle import LoadedWakeWordBundle

ROOT = Path.cwd().resolve()
CONFIGURATION = load_product_host_config(
    (ROOT / "config" / "product_host.json").resolve(),
    ROOT,
)


class Wake:
    def score(self, _frame: AudioFrame) -> float:
        return 0.0


class Vad:
    def is_speech(self, _frame: AudioFrame) -> bool:
        return False


class Stt:
    def transcribe(self, _frames: tuple[AudioFrame, ...]) -> str:
        return ""


class Source:
    def __init__(self) -> None:
        self.opened = False

    def frames(
        self,
        _cancellation: CancellationToken,
        *,
        max_frames: int,
    ) -> Iterable[AudioFrame]:
        self.opened = True
        return ()


class Transport:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, _system_prompt: str, _user_prompt: str) -> str:
        self.calls += 1
        raise AssertionError("language stack construction must not call NIM")


class StaticTransport:
    def __init__(self, response: str) -> None:
        self.response = response
        self.calls = 0

    def complete(self, _system_prompt: str, _user_prompt: str) -> str:
        self.calls += 1
        return self.response


class AlwaysWake:
    def score(self, _frame: AudioFrame) -> float:
        return 1.0


class SequencedVad:
    def __init__(self) -> None:
        self.calls = 0

    def is_speech(self, _frame: AudioFrame) -> bool:
        self.calls += 1
        return self.calls <= 2


class FixedStt:
    def transcribe(self, _frames: tuple[AudioFrame, ...]) -> str:
        return "자비스 시스템 정보 확인"


class FiniteSource:
    def frames(
        self,
        _cancellation: CancellationToken,
        *,
        max_frames: int,
    ) -> Iterable[AudioFrame]:
        for _index in range(min(max_frames, 7)):
            yield AudioFrame(pcm_s16le=b"\0\0" * 16)


class NoApproval:
    def request(self, _approval: ApprovalRequest) -> ApprovalResponse:
        raise AssertionError("GREEN action must not request approval")


def wake_bundle(tmp_path: Path) -> LoadedWakeWordBundle:
    model = tmp_path / "model.onnx"
    model.write_bytes(b"fixture")
    return LoadedWakeWordBundle(
        wakeword_key="hey_jarvis",
        wakeword_path=model,
        wakeword_sha256="a" * 64,
        melspectrogram_path=model,
        melspectrogram_sha256="b" * 64,
        embedding_path=model,
        embedding_sha256="c" * 64,
    )


def stt_bundle(tmp_path: Path) -> LoadedSttBundle:
    def specification(name: str) -> SttModelFile:
        return SttModelFile(path=name, sha256="d" * 64, size=1)

    return LoadedSttBundle(
        root=tmp_path,
        source_repository="fixture/model",
        source_revision="e" * 40,
        files=SttBundleFiles.model_validate(
            {
                "config.json": specification("config.json"),
                "model.bin": specification("model.bin"),
                "tokenizer.json": specification("tokenizer.json"),
                "vocabulary.txt": specification("vocabulary.txt"),
            }
        ),
    )


def test_voice_stack_builds_verified_components_without_opening_source(
    tmp_path: Path,
) -> None:
    source = Source()
    observed: dict[str, object] = {}

    def load_wake(path: Path) -> LoadedWakeWordBundle:
        observed["wake_path"] = path
        return wake_bundle(tmp_path)

    def load_stt(path: Path) -> LoadedSttBundle:
        observed["stt_path"] = path
        return stt_bundle(tmp_path)

    def make_vad(*, rms_threshold: int) -> Vad:
        observed["rms"] = rms_threshold
        return Vad()

    def make_source(*, frame_ms: int, queue_capacity: int) -> Source:
        observed["frame"] = (frame_ms, queue_capacity)
        return source

    stack = build_product_voice_stack(
        CONFIGURATION,
        wake_bundle_loader=load_wake,
        stt_bundle_loader=load_stt,
        wake_factory=lambda _bundle: Wake(),
        stt_factory=lambda _bundle: Stt(),
        vad_factory=make_vad,
        frame_source_factory=make_source,
    )

    assert isinstance(stack, ProductVoiceStack)
    assert stack.frame_source is source
    assert not source.opened
    assert observed == {
        "wake_path": CONFIGURATION.wakeword_manifest,
        "stt_path": CONFIGURATION.stt_manifest,
        "rms": 500,
        "frame": (80, 32),
    }


def test_bundle_failure_prevents_all_runtime_component_construction() -> None:
    calls: list[str] = []

    def fail_wake(_path: Path) -> LoadedWakeWordBundle:
        raise ValueError("bundle invalid")

    def unexpected_stt(_path: Path) -> LoadedSttBundle:
        calls.append("stt")
        raise AssertionError("STT loader must not run")

    def unexpected_wake(bundle: LoadedWakeWordBundle) -> WakeWordDetector:
        del bundle
        calls.append("wake")
        raise AssertionError("wake factory must not run")

    def unexpected_stt_model(bundle: LoadedSttBundle) -> SpeechToText:
        del bundle
        calls.append("stt-model")
        raise AssertionError("STT factory must not run")

    def unexpected_vad(*, rms_threshold: int) -> Vad:
        calls.append(f"vad-{rms_threshold}")
        raise AssertionError("VAD factory must not run")

    def unexpected_source(*, frame_ms: int, queue_capacity: int) -> Source:
        calls.append(f"source-{frame_ms}-{queue_capacity}")
        raise AssertionError("source factory must not run")

    with pytest.raises(ValueError, match="bundle invalid"):
        build_product_voice_stack(
            CONFIGURATION,
            wake_bundle_loader=fail_wake,
            stt_bundle_loader=unexpected_stt,
            wake_factory=unexpected_wake,
            stt_factory=unexpected_stt_model,
            vad_factory=unexpected_vad,
            frame_source_factory=unexpected_source,
        )
    assert calls == []


def test_language_stack_binds_two_typed_clients_without_network_request() -> None:
    secret = SecretStr("nvapi-abcdefghijklmnopqrstuvwxyz")
    transports: list[Transport] = []
    bindings: list[tuple[SecretStr, str]] = []

    def make_transport(api_key: SecretStr, model: str) -> Transport:
        bindings.append((api_key, model))
        transport = Transport()
        transports.append(transport)
        return transport

    stack = build_product_language_stack(
        CONFIGURATION,
        secret,
        transport_factory=make_transport,
    )

    assert isinstance(stack, ProductLanguageStack)
    assert len(transports) == 2
    assert all(transport.calls == 0 for transport in transports)
    assert bindings == [
        (secret, "z-ai/glm-5.3-flash"),
        (secret, "z-ai/glm-5.3-flash"),
    ]
    assert "nvapi" not in repr(stack)


def test_capability_stack_narrows_registry_to_exact_verified_routes() -> None:
    executor = MockExecutor({"system.info.read": lambda _action: {"safe": True}})
    verifier = PredicateVerifier()

    stack = build_product_capability_stack(
        {"system.info.read": executor},
        {"system.info.read": verifier},
    )

    assert isinstance(stack, ProductCapabilityStack)
    assert [item.capability_id for item in stack.registry.list()] == ["system.info.read"]
    assert stack.registry.get("file.list") is None
    selected = Action(
        capability_id="system.info.read",
        purpose="verify narrowed route",
        expected_postconditions=(Postcondition(predicate="execution.success"),),
    )
    execution = stack.executor.execute(selected)
    assert execution.success
    assert stack.verifier.verify(selected, execution).success


@pytest.mark.parametrize(
    ("executors", "verifiers", "reason"),
    [
        ({}, {}, "must not be empty"),
        ({"system.info.read": MockExecutor()}, {}, "must not be empty"),
        (
            {"system.info.read": MockExecutor()},
            {"file.list": PredicateVerifier()},
            "must match",
        ),
        (
            {"unknown.capability": MockExecutor()},
            {"unknown.capability": PredicateVerifier()},
            "not registered",
        ),
    ],
)
def test_capability_stack_rejects_incomplete_or_unknown_routes(
    executors: dict[str, object],
    verifiers: dict[str, object],
    reason: str,
) -> None:
    with pytest.raises(ValueError, match=reason):
        build_product_capability_stack(
            executors,  # type: ignore[arg-type]
            verifiers,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("executors", "verifiers"),
    [
        ({"system.info.read": object()}, {"system.info.read": PredicateVerifier()}),
        ({"system.info.read": MockExecutor()}, {"system.info.read": object()}),
    ],
)
def test_capability_stack_rejects_invalid_delegate_shapes(
    executors: dict[str, object],
    verifiers: dict[str, object],
) -> None:
    with pytest.raises(ValueError, match="invalid product capability route"):
        build_product_capability_stack(
            executors,  # type: ignore[arg-type]
            verifiers,  # type: ignore[arg-type]
        )


def test_runtime_stack_executes_one_closed_loop_without_external_effects() -> None:
    action = Action(
        action_id=uuid4(),
        capability_id="system.info.read",
        purpose="read fixture system info",
        target_identity="fixture-machine",
        expected_postconditions=(Postcondition(predicate="execution.success"),),
    )
    plan = Plan(goal="fixture", steps=(action,))
    intent_response = (
        '{"intent_class":"COMMAND","addressed_confidence":0.99,'
        '"intent_confidence":0.99,"target_confidence":0.99,'
        '"quoted_command":false,"negated_command":false,"hypothetical":false}'
    )
    responses = [StaticTransport(intent_response), StaticTransport(plan.model_dump_json())]

    def make_transport(api_key: SecretStr, model: str) -> StaticTransport:
        del api_key, model
        return responses.pop(0)

    executor = MockExecutor({"system.info.read": lambda _action: {"fixture": True}})
    capabilities = build_product_capability_stack(
        {"system.info.read": executor},
        {"system.info.read": PredicateVerifier()},
    )
    language = build_product_language_stack(
        CONFIGURATION,
        SecretStr("nvapi-abcdefghijklmnopqrstuvwxyz"),
        registry=capabilities.registry,
        transport_factory=make_transport,
    )
    voice = ProductVoiceStack(
        frame_source=FiniteSource(),
        pipeline=VoicePipeline(
            AlwaysWake(),
            SequencedVad(),
            FixedStt(),
            end_silence_frames=5,
        ),
    )
    audit = InMemoryAuditSink()
    stack = build_product_runtime_stack_from_capabilities(
        voice,
        language,
        capabilities,
        audit=audit,
        approval_presenter=NoApproval(),
        policy_context_provider=lambda _action: PolicyContext(
            addressed_confidence=0.99,
            intent_confidence=0.99,
            target_confidence=0.99,
            target_matches_plan=True,
        ),
        approval_secret=b"a" * 32,
    )

    assert isinstance(stack, ProductRuntimeStack)
    assert not stack.capture_pump.is_running
    pump_result = stack.capture_pump.run(max_frames=20)
    completed = stack.runtime.wait_for_idle(timeout_seconds=1)

    assert pump_result.tasks_started == 1
    assert completed is not None and completed.reason == "ALL_ACTIONS_VERIFIED"
    assert len(executor.calls) == 1
    assert audit.of_type("TASK_FINISHED")


def test_runtime_stack_rejects_language_executor_capability_drift() -> None:
    capabilities = build_product_capability_stack(
        {"system.info.read": MockExecutor()},
        {"system.info.read": PredicateVerifier()},
    )
    language = build_product_language_stack(
        CONFIGURATION,
        SecretStr("nvapi-abcdefghijklmnopqrstuvwxyz"),
        transport_factory=lambda _key, _model: Transport(),
    )
    voice = ProductVoiceStack(
        frame_source=FiniteSource(),
        pipeline=VoicePipeline(AlwaysWake(), SequencedVad(), FixedStt()),
    )

    with pytest.raises(ValueError, match="capability surfaces must match"):
        build_product_runtime_stack_from_capabilities(
            voice,
            language,
            capabilities,
            audit=InMemoryAuditSink(),
            approval_presenter=NoApproval(),
            policy_context_provider=lambda _action: PolicyContext(
                addressed_confidence=0.99,
                intent_confidence=0.99,
                target_confidence=0.99,
            ),
        )


def test_runtime_stack_rejects_short_approval_secret() -> None:
    language = build_product_language_stack(
        CONFIGURATION,
        SecretStr("nvapi-abcdefghijklmnopqrstuvwxyz"),
        transport_factory=lambda _key, _model: Transport(),
    )
    voice = ProductVoiceStack(
        frame_source=FiniteSource(),
        pipeline=VoicePipeline(AlwaysWake(), SequencedVad(), FixedStt()),
    )
    with pytest.raises(ValueError, match="at least 32 bytes"):
        build_product_runtime_stack(
            voice,
            language,
            executor=MockExecutor(),
            verifier=PredicateVerifier(),
            audit=InMemoryAuditSink(),
            approval_presenter=NoApproval(),
            policy_context_provider=lambda _action: PolicyContext(
                addressed_confidence=0.0,
                intent_confidence=0.0,
                target_confidence=0.0,
            ),
            approval_secret=b"short",
        )
