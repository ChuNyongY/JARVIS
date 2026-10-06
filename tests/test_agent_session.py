from __future__ import annotations

from collections.abc import Iterable, Mapping

from javis_core import (
    Action,
    ApprovalTokenService,
    AudioFrame,
    CancellationToken,
    InMemoryAuditSink,
    IntentClass,
    IntentEvidence,
    MockExecutor,
    Orchestrator,
    Plan,
    PolicyContext,
    PolicyEngine,
    Postcondition,
    PredicateVerifier,
    VoiceCommandCoordinator,
    VoicePipeline,
    default_registry,
)
from javis_core.agent_session import AgentSession, AgentSessionStatus
from javis_core.intent import InputSource
from javis_core.voice_feedback import VoiceFeedbackPresenter

FRAME = AudioFrame(pcm_s16le=b"\0\0" * 80)


class FrameSource:
    def __init__(self, frames: tuple[AudioFrame, ...], *, fail: bool = False) -> None:
        self._frames = frames
        self._fail = fail
        self.closed = False

    def frames(
        self,
        cancellation: CancellationToken,
        *,
        max_frames: int,
    ) -> Iterable[AudioFrame]:
        try:
            if self._fail:
                raise RuntimeError("private audio detail")
            yield from self._frames[:max_frames]
        finally:
            self.closed = True


class CloseFailingIterator:
    def __iter__(self) -> CloseFailingIterator:
        return self

    def __next__(self) -> AudioFrame:
        raise StopIteration

    def close(self) -> None:
        raise RuntimeError("private cleanup detail")


class CloseFailingSource:
    def frames(
        self,
        cancellation: CancellationToken,
        *,
        max_frames: int,
    ) -> Iterable[AudioFrame]:
        return CloseFailingIterator()


class Wake:
    def score(self, frame: AudioFrame) -> float:
        return 1.0


class Vad:
    def __init__(self) -> None:
        self.calls = 0

    def is_speech(self, frame: AudioFrame) -> bool:
        self.calls += 1
        return self.calls == 1


class Stt:
    def transcribe(self, frames: tuple[AudioFrame, ...]) -> str:
        return "자비스 시스템 정보 확인"


class Classifier:
    def classify(
        self,
        transcript: str,
        *,
        wake_confidence: float,
        explicit_wake_word: bool,
        source: InputSource = "MIC",
    ) -> IntentEvidence:
        return IntentEvidence(
            transcript=transcript,
            wake_confidence=wake_confidence,
            addressed_confidence=0.99,
            intent_confidence=0.99,
            target_confidence=0.99,
            intent_class=IntentClass.COMMAND,
            explicit_wake_word=explicit_wake_word,
            source=source,
        )


class Planner:
    def __init__(self) -> None:
        self.contexts: list[Mapping[str, object] | None] = []

    def plan(
        self,
        goal: str,
        *,
        trusted_context: Mapping[str, object] | None = None,
        untrusted_context: str | None = None,
    ) -> Plan:
        self.contexts.append(trusted_context)
        return Plan(
            goal=goal,
            steps=(
                Action(
                    capability_id="system.info.read",
                    purpose="read system info",
                    expected_postconditions=(
                        Postcondition(predicate="execution.success"),
                    ),
                ),
            ),
        )


class SpeechSink:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.messages: list[str] = []

    def speak(
        self,
        text: str,
        *,
        rate: int = 0,
        volume: int = 100,
        cancellation: CancellationToken | None = None,
    ) -> None:
        if self.fail:
            raise RuntimeError("private speaker detail")
        self.messages.append(text)


def build_session(
    source: FrameSource,
    *,
    sink: SpeechSink | None = None,
    context_provider: object | None = None,
) -> tuple[AgentSession, Planner, MockExecutor]:
    planner = Planner()
    executor = MockExecutor({"system.info.read": lambda _action: {}})

    def coordinator_factory(token: CancellationToken) -> VoiceCommandCoordinator:
        return VoiceCommandCoordinator(Classifier(), planner, token)

    def orchestrator_factory(token: CancellationToken) -> Orchestrator:
        return Orchestrator(
            policy=PolicyEngine(default_registry()),
            executor=executor,
            verifier=PredicateVerifier(),
            audit=InMemoryAuditSink(),
            approvals=ApprovalTokenService(b"s" * 32),
            cancellation=token,
        )

    planner_context = (
        context_provider
        if callable(context_provider)
        else lambda: {"foreground_app_id": "notepad"}
    )
    return (
        AgentSession(
            frame_source=source,
            voice_pipeline=VoicePipeline(
                Wake(), Vad(), Stt(), end_silence_frames=1, max_capture_frames=10
            ),
            coordinator_factory=coordinator_factory,
            orchestrator_factory=orchestrator_factory,
            policy_context_provider=lambda _action: PolicyContext(
                addressed_confidence=0.99,
                intent_confidence=0.99,
                target_confidence=0.99,
                target_matches_plan=True,
            ),
            planner_context_provider=planner_context,
            feedback=VoiceFeedbackPresenter(sink) if sink is not None else None,
        ),
        planner,
        executor,
    )


def test_run_once_connects_frames_to_verified_task_and_fixed_feedback() -> None:
    source = FrameSource((FRAME, FRAME))
    sink = SpeechSink()
    session, planner, executor = build_session(source, sink=sink)

    result = session.run_once(max_frames=10)

    assert result.status is AgentSessionStatus.COMPLETED
    assert result.reason == "ALL_ACTIONS_VERIFIED"
    assert result.completed_actions == 1
    assert result.feedback_delivered is True
    assert source.closed
    assert planner.contexts == [{"foreground_app_id": "notepad"}]
    assert len(executor.calls) == 1
    assert sink.messages == ["요청한 작업을 확인하고 완료했습니다."]
    assert "자비스 시스템 정보 확인" not in result.model_dump_json()


def test_no_complete_utterance_and_capture_failure_close_source() -> None:
    empty = FrameSource(())
    session, _planner, _executor = build_session(empty)
    assert session.run_once().status is AgentSessionStatus.NO_UTTERANCE
    assert empty.closed

    failed = FrameSource((), fail=True)
    session, _planner, _executor = build_session(failed)
    result = session.run_once()
    assert result.status is AgentSessionStatus.FAILED
    assert result.reason == "AUDIO_CAPTURE_FAILED"
    assert "private" not in result.model_dump_json()
    assert failed.closed


def test_context_and_feedback_failures_are_redacted_and_fail_safe() -> None:
    source = FrameSource((FRAME, FRAME))

    def fail_context() -> Mapping[str, object]:
        raise RuntimeError("private context detail")

    session, _planner, executor = build_session(source, context_provider=fail_context)
    result = session.run_once()
    assert result.reason == "CONTEXT_COLLECTION_FAILED"
    assert executor.calls == ()

    source = FrameSource((FRAME, FRAME))
    session, _planner, executor = build_session(source, sink=SpeechSink(fail=True))
    result = session.run_once()
    assert result.status is AgentSessionStatus.COMPLETED
    assert result.feedback_delivered is False
    assert len(executor.calls) == 1

    session, _planner, executor = build_session(CloseFailingSource())  # type: ignore[arg-type]
    result = session.run_once()
    assert result.reason == "AUDIO_CAPTURE_CLEANUP_FAILED"
    assert "private" not in result.model_dump_json()
    assert executor.calls == ()


def test_max_frames_is_strict_and_bounded() -> None:
    session, _planner, _executor = build_session(FrameSource(()))
    for invalid in (0, 1_501, True):
        try:
            session.run_once(max_frames=invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid max_frames was accepted")
