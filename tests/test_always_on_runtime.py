from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping

from javis_core import (
    Action,
    ApprovalTokenService,
    CancellationToken,
    CaptureStatus,
    ExecutionResult,
    InMemoryAuditSink,
    IntentClass,
    IntentEvidence,
    MockExecutor,
    Orchestrator,
    Plan,
    PolicyContext,
    PolicyDecision,
    PolicyEngine,
    Postcondition,
    PredicateVerifier,
    VoiceCaptureResult,
    VoiceCommandCoordinator,
    default_registry,
)
from javis_core.always_on_runtime import (
    AlwaysOnAgentRuntime,
    AlwaysOnState,
    RuntimeEventType,
)
from javis_core.intent import InputSource


def capture(text: str) -> VoiceCaptureResult:
    return VoiceCaptureResult(
        status=CaptureStatus.TRANSCRIBED,
        transcript=text,
        wake_confidence=0.99,
        frame_count=2,
        reason="UTTERANCE_COMPLETE",
    )


class Classifier:
    def __init__(self, confidence: float = 0.99) -> None:
        self.calls = 0
        self.confidence = confidence

    def classify(
        self,
        transcript: str,
        *,
        wake_confidence: float,
        explicit_wake_word: bool,
        source: InputSource = "MIC",
    ) -> IntentEvidence:
        self.calls += 1
        return IntentEvidence(
            transcript=transcript,
            wake_confidence=wake_confidence,
            addressed_confidence=self.confidence,
            intent_confidence=self.confidence,
            target_confidence=self.confidence,
            intent_class=IntentClass.COMMAND,
            explicit_wake_word=True,
            source=source,
        )


class Planner:
    def __init__(self) -> None:
        self.calls = 0

    def plan(
        self,
        goal: str,
        *,
        trusted_context: Mapping[str, object] | None = None,
        untrusted_context: str | None = None,
    ) -> Plan:
        self.calls += 1
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


class BlockingPlanner(Planner):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()

    def plan(
        self,
        goal: str,
        *,
        trusted_context: Mapping[str, object] | None = None,
        untrusted_context: str | None = None,
    ) -> Plan:
        self.started.set()
        assert self.release.wait(timeout=2)
        return super().plan(
            goal,
            trusted_context=trusted_context,
            untrusted_context=untrusted_context,
        )


class BlockingExecutor:
    def __init__(self) -> None:
        self.started = threading.Event()

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        self.started.set()
        started = time.monotonic()
        while cancellation is not None and not cancellation.is_cancelled:
            if time.monotonic() - started > 2:
                raise AssertionError("STOP was not delivered")
            time.sleep(0.005)
        return ExecutionResult(
            action_id=action.action_id,
            executor="mock",
            success=False,
            error_code="STOP_REQUESTED",
            retry_safe=False,
            duration_ms=int((time.monotonic() - started) * 1000),
        )


class RecordingPolicy(PolicyEngine):
    def __init__(self) -> None:
        super().__init__(default_registry())
        self.contexts: list[PolicyContext] = []

    def evaluate(self, action: Action, context: PolicyContext) -> PolicyDecision:
        self.contexts.append(context)
        return super().evaluate(action, context)


def runtime_with_executor(
    executor: object,
    *,
    planner: Planner | None = None,
    planner_context_provider: Callable[[], Mapping[str, object]] | None = None,
    classifier_confidence: float = 0.99,
    policy: PolicyEngine | None = None,
) -> tuple[AlwaysOnAgentRuntime, Classifier, Planner]:
    classifier = Classifier(classifier_confidence)
    selected_planner = planner or Planner()

    def coordinator_factory(token: CancellationToken) -> VoiceCommandCoordinator:
        return VoiceCommandCoordinator(classifier, selected_planner, token)

    def orchestrator_factory(token: CancellationToken) -> Orchestrator:
        return Orchestrator(
            policy=policy or PolicyEngine(default_registry()),
            executor=executor,  # type: ignore[arg-type]
            verifier=PredicateVerifier(),
            audit=InMemoryAuditSink(),
            approvals=ApprovalTokenService(b"r" * 32),
            cancellation=token,
        )

    return (
        AlwaysOnAgentRuntime(
            coordinator_factory=coordinator_factory,
            orchestrator_factory=orchestrator_factory,
            policy_context_provider=lambda _action: PolicyContext(
                addressed_confidence=0.99,
                intent_confidence=0.99,
                target_confidence=0.99,
                target_matches_plan=True,
            ),
            planner_context_provider=planner_context_provider,
        ),
        classifier,
        selected_planner,
    )


def test_runtime_caps_policy_confidence_at_accepted_voice_gate() -> None:
    policy = RecordingPolicy()
    runtime, _classifier, _planner = runtime_with_executor(
        MockExecutor({"system.info.read": lambda _action: {}}),
        classifier_confidence=0.91,
        policy=policy,
    )

    runtime.submit_capture(capture("자비스 시스템 정보 확인"))
    completed = runtime.wait_for_idle(timeout_seconds=1)

    assert completed is not None and completed.event_type is RuntimeEventType.TASK_COMPLETED
    assert len(policy.contexts) == 1
    assert policy.contexts[0].addressed_confidence == 0.91
    assert policy.contexts[0].intent_confidence == 0.91
    assert policy.contexts[0].target_confidence == 0.91


def test_active_task_accepts_exact_stop_without_classifier_or_planner() -> None:
    executor = BlockingExecutor()
    runtime, classifier, planner = runtime_with_executor(executor)
    started = runtime.submit_capture(capture("자비스 시스템 정보 확인"))
    assert started.event_type is RuntimeEventType.TASK_STARTED
    assert executor.started.wait(timeout=1)
    calls = (classifier.calls, planner.calls)

    busy = runtime.submit_capture(capture("다른 작업도 실행해"))
    stopped = runtime.submit_capture(capture("멈춰"))
    completed = runtime.wait_for_idle(timeout_seconds=1)

    assert busy.event_type is RuntimeEventType.BUSY_IGNORED
    assert stopped.event_type is RuntimeEventType.STOP_ACCEPTED
    assert (classifier.calls, planner.calls) == calls
    assert completed is not None
    assert completed.event_type is RuntimeEventType.TASK_ABORTED
    assert completed.reason == "LOCAL_STOP"
    assert runtime.state is AlwaysOnState.IDLE


def test_completed_runtime_accepts_next_task_with_fresh_token() -> None:
    executor = MockExecutor({"system.info.read": lambda _action: {}})
    runtime, classifier, planner = runtime_with_executor(executor)

    first = runtime.submit_capture(capture("첫 작업"))
    first_done = runtime.wait_for_idle(timeout_seconds=1)
    second = runtime.submit_capture(capture("둘째 작업"))
    second_done = runtime.wait_for_idle(timeout_seconds=1)

    assert first.event_type is RuntimeEventType.TASK_STARTED
    assert second.event_type is RuntimeEventType.TASK_STARTED
    assert first_done is not None and first_done.event_type is RuntimeEventType.TASK_COMPLETED
    assert second_done is not None and second_done.event_type is RuntimeEventType.TASK_COMPLETED
    assert classifier.calls == 2
    assert planner.calls == 2
    assert len(executor.calls) == 2


def test_close_cancels_active_task_and_rejects_new_capture() -> None:
    executor = BlockingExecutor()
    runtime, _classifier, _planner = runtime_with_executor(executor)
    runtime.submit_capture(capture("작업"))
    assert executor.started.wait(timeout=1)

    closed = runtime.close(timeout_seconds=1)
    rejected = runtime.submit_capture(capture("새 작업"))

    assert closed.event_type is RuntimeEventType.CLOSED
    assert rejected.event_type is RuntimeEventType.CLOSED
    assert runtime.state is AlwaysOnState.CLOSED


def test_exact_stop_during_planning_prevents_executor_start() -> None:
    executor = MockExecutor({"system.info.read": lambda _action: {}})
    planner = BlockingPlanner()
    runtime, classifier, _planner = runtime_with_executor(executor, planner=planner)
    started = runtime.submit_capture(capture("긴 계획"))
    assert planner.started.wait(timeout=1)

    stopped = runtime.submit_capture(capture("자비스 스톱"))
    planner.release.set()
    completed = runtime.wait_for_idle(timeout_seconds=1)

    assert started.event_type is RuntimeEventType.TASK_STARTED
    assert stopped.event_type is RuntimeEventType.STOP_ACCEPTED
    assert completed is not None
    assert completed.event_type is RuntimeEventType.ROUTED
    assert completed.reason == "PLANNING_CANCELLED"
    assert classifier.calls == 1
    assert executor.calls == ()
    assert runtime.state is AlwaysOnState.IDLE


class FailingThread(threading.Thread):
    def start(self) -> None:
        raise RuntimeError("thread unavailable")


def test_worker_start_failure_restores_idle_state() -> None:
    runtime, _classifier, _planner = runtime_with_executor(
        MockExecutor({"system.info.read": lambda _action: {}})
    )
    runtime._thread_factory = lambda **kwargs: FailingThread(**kwargs)

    result = runtime.submit_capture(capture("작업"))

    assert result.event_type is RuntimeEventType.FAILED
    assert result.reason == "TASK_WORKER_START_FAILED"
    assert runtime.state is AlwaysOnState.IDLE
    status = runtime.status_snapshot()
    assert status.state is AlwaysOnState.IDLE
    assert status.last_event_type is RuntimeEventType.FAILED
    assert status.last_reason == "TASK_WORKER_START_FAILED"


def test_status_snapshot_is_non_consuming_and_privacy_minimized() -> None:
    executor = MockExecutor({"system.info.read": lambda _action: {}})
    runtime, _classifier, _planner = runtime_with_executor(executor)

    initial = runtime.status_snapshot()
    runtime.submit_capture(capture("민감한 발화 C:\\private\\secret.txt"))
    completed = runtime.wait_for_idle(timeout_seconds=1)
    status = runtime.status_snapshot()

    assert initial.state is AlwaysOnState.IDLE
    assert completed is not None
    assert status.state is AlwaysOnState.IDLE
    assert status.last_event_type is RuntimeEventType.TASK_COMPLETED
    assert status.completed_actions == 1
    serialized = status.model_dump_json()
    assert "민감한" not in serialized
    assert "private" not in serialized
    assert "task_id" not in serialized


def test_stop_bypasses_blocked_context_and_prevents_model_calls() -> None:
    context_started = threading.Event()
    context_release = threading.Event()

    def context_provider() -> Mapping[str, object]:
        context_started.set()
        assert context_release.wait(timeout=2)
        return {"verified_foreground_app_id": "notepad"}

    executor = MockExecutor({"system.info.read": lambda _action: {}})
    runtime, classifier, planner = runtime_with_executor(
        executor,
        planner_context_provider=context_provider,
    )
    started = runtime.submit_capture(capture("작업"))
    assert context_started.wait(timeout=1)

    stopped = runtime.submit_capture(capture("멈춰"))
    context_release.set()
    completed = runtime.wait_for_idle(timeout_seconds=1)

    assert started.event_type is RuntimeEventType.TASK_STARTED
    assert stopped.event_type is RuntimeEventType.STOP_ACCEPTED
    assert completed is not None
    assert completed.reason == "PLANNING_CANCELLED"
    assert classifier.calls == 0
    assert planner.calls == 0
    assert executor.calls == ()


def test_context_failure_is_redacted_and_prevents_model_calls() -> None:
    def failing_context() -> Mapping[str, object]:
        raise RuntimeError("secret context detail")

    runtime, classifier, planner = runtime_with_executor(
        MockExecutor({"system.info.read": lambda _action: {}}),
        planner_context_provider=failing_context,
    )

    started = runtime.submit_capture(capture("작업"))
    completed = runtime.wait_for_idle(timeout_seconds=1)

    assert started.event_type is RuntimeEventType.TASK_STARTED
    assert completed is not None
    assert completed.event_type is RuntimeEventType.FAILED
    assert completed.reason == "CONTEXT_COLLECTION_FAILED"
    assert "secret" not in completed.model_dump_json()
    assert classifier.calls == 0
    assert planner.calls == 0


def test_invalid_timeout_is_rejected() -> None:
    runtime, _classifier, _planner = runtime_with_executor(
        MockExecutor({"system.info.read": lambda _action: {}})
    )
    for invalid in (0, 31, True):
        try:
            runtime.wait_for_idle(timeout_seconds=invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid timeout was accepted")
