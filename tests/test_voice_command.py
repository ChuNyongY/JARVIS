import json
import threading
import time
from collections.abc import Mapping

from javis_core import (
    Action,
    ActionStatus,
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
    PolicyEngine,
    Postcondition,
    PredicateVerifier,
    TaskOutcome,
    TypedIntentClassifier,
    TypedPlanner,
    VoiceCaptureResult,
    default_registry,
)
from javis_core.intent import InputSource
from javis_core.voice_command import VoiceCommandCoordinator, VoiceCommandStatus


class Classifier:
    def __init__(self, intent_class: IntentClass, *, confidence: float = 0.99) -> None:
        self.intent_class = intent_class
        self.confidence = confidence
        self.calls = 0

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
            intent_class=self.intent_class,
            explicit_wake_word=explicit_wake_word,
            source="MIC",
        )


class Planner:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, Mapping[str, object] | None]] = []

    def plan(
        self,
        goal: str,
        *,
        trusted_context: Mapping[str, object] | None = None,
        untrusted_context: str | None = None,
    ) -> Plan:
        self.calls.append((goal, trusted_context))
        if self.fail:
            raise RuntimeError("private planner detail")
        return Plan(
            goal=goal,
            steps=(
                Action(
                    capability_id="system.info.read",
                    purpose="read system information",
                    target_identity="local-machine",
                    expected_postconditions=(
                        Postcondition(predicate="execution.success"),
                    ),
                ),
            ),
        )


def capture(
    transcript: str | None = "자비스 시스템 정보 확인",
    *,
    status: CaptureStatus = CaptureStatus.TRANSCRIBED,
) -> VoiceCaptureResult:
    return VoiceCaptureResult(
        status=status,
        transcript=transcript,
        wake_confidence=0.98,
        frame_count=10,
        reason="UTTERANCE_COMPLETE" if status is CaptureStatus.TRANSCRIBED else "STT_FAILED",
    )


def test_command_routes_to_typed_plan_with_minimized_context() -> None:
    classifier = Classifier(IntentClass.COMMAND)
    planner = Planner()
    token = CancellationToken()
    decision = VoiceCommandCoordinator(classifier, planner, token).process(
        capture(),
        task_active=False,
        trusted_context={"foreground_app_id": "notepad"},
    )
    assert decision.status is VoiceCommandStatus.PLAN_READY
    assert decision.plan is not None
    assert planner.calls == [
        ("자비스 시스템 정보 확인", {"foreground_app_id": "notepad"})
    ]
    assert not token.is_cancelled


def test_aborted_capture_never_reaches_classifier_or_planner() -> None:
    classifier = Classifier(IntentClass.COMMAND)
    planner = Planner()
    decision = VoiceCommandCoordinator(classifier, planner, CancellationToken()).process(
        capture(None, status=CaptureStatus.ABORTED), task_active=False
    )
    assert decision.status is VoiceCommandStatus.CAPTURE_ABORTED
    assert classifier.calls == 0
    assert planner.calls == []


def test_uncertain_command_does_not_reach_planner() -> None:
    planner = Planner()
    decision = VoiceCommandCoordinator(
        Classifier(IntentClass.COMMAND, confidence=0.8), planner, CancellationToken()
    ).process(capture(), task_active=False)
    assert decision.status is VoiceCommandStatus.ROUTED
    assert decision.gate is not None
    assert not decision.gate.accepted
    assert planner.calls == []


def test_local_cancel_sets_shared_token_without_planning() -> None:
    planner = Planner()
    token = CancellationToken()
    decision = VoiceCommandCoordinator(
        Classifier(IntentClass.CANCEL), planner, token
    ).process(capture("멈춰"), task_active=True)
    assert decision.status is VoiceCommandStatus.ROUTED
    assert token.is_cancelled
    assert token.reason == "LOCAL_STOP"
    assert planner.calls == []


def test_cancel_without_active_task_is_ignored() -> None:
    token = CancellationToken()
    decision = VoiceCommandCoordinator(
        Classifier(IntentClass.CANCEL), Planner(), token
    ).process(capture("멈춰"), task_active=False)
    assert decision.status is VoiceCommandStatus.ROUTED
    assert not token.is_cancelled


def test_invalid_transcribed_capture_fails_closed() -> None:
    decision = VoiceCommandCoordinator(
        Classifier(IntentClass.COMMAND), Planner(), CancellationToken()
    ).process(capture(None), task_active=False)
    assert decision.status is VoiceCommandStatus.FAILED
    assert decision.reason == "INVALID_VOICE_CAPTURE"


def test_planner_failure_is_redacted() -> None:
    decision = VoiceCommandCoordinator(
        Classifier(IntentClass.COMMAND), Planner(fail=True), CancellationToken()
    ).process(capture(), task_active=False)
    assert decision.status is VoiceCommandStatus.FAILED
    assert decision.reason == "PLANNING_FAILED"
    assert "private" not in decision.reason


class StaticTransport:
    def __init__(self, response: str) -> None:
        self.response = response

    def complete(self, system_prompt: str, user_prompt: str) -> str:
        return self.response


def test_voice_to_intent_to_plan_to_orchestrator_closed_loop() -> None:
    intent_response = json.dumps(
        {
            "intent_class": "COMMAND",
            "addressed_confidence": 0.99,
            "intent_confidence": 0.98,
            "target_confidence": 0.97,
            "quoted_command": False,
            "negated_command": False,
            "hypothetical": False,
        }
    )
    plan_response = json.dumps(
        {
            "schema_version": "1.0",
            "goal": "model cannot replace this goal",
            "steps": [
                {
                    "capability_id": "system.info.read",
                    "arguments": {},
                    "purpose": "read system information",
                    "expected_postconditions": [
                        {"predicate": "execution.success", "arguments": {}}
                    ],
                    "target_identity": "local-machine",
                }
            ],
        }
    )
    token = CancellationToken()
    coordinator = VoiceCommandCoordinator(
        TypedIntentClassifier(StaticTransport(intent_response)),
        TypedPlanner(StaticTransport(plan_response), default_registry()),
        token,
    )
    decision = coordinator.process(capture(), task_active=False)
    assert decision.status is VoiceCommandStatus.PLAN_READY
    assert decision.plan is not None
    assert decision.plan.goal == "자비스 시스템 정보 확인"

    executor = MockExecutor({"system.info.read": lambda _action: {}})
    outcome = Orchestrator(
        policy=PolicyEngine(default_registry()),
        executor=executor,
        verifier=PredicateVerifier(),
        audit=InMemoryAuditSink(),
        approvals=ApprovalTokenService(b"v" * 32),
        cancellation=token,
    ).run(
        decision.plan,
        lambda _action: PolicyContext(
            addressed_confidence=0.99,
            intent_confidence=0.98,
            target_confidence=0.97,
            target_matches_plan=True,
        ),
    )
    assert outcome.status is ActionStatus.SUCCEEDED
    assert len(executor.calls) == 1


class BlockingCancelableExecutor:
    def __init__(self) -> None:
        self.started = threading.Event()

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = time.monotonic()
        self.started.set()
        while cancellation is not None and not cancellation.is_cancelled:
            if time.monotonic() - started > 2:
                raise AssertionError("cancellation was not delivered")
            time.sleep(0.005)
        return ExecutionResult(
            action_id=action.action_id,
            executor="mock",
            success=False,
            error_code="STOP_REQUESTED",
            retry_safe=False,
            duration_ms=int((time.monotonic() - started) * 1_000),
        )


def test_local_stop_interrupts_active_executor_without_planner() -> None:
    token = CancellationToken()
    executor = BlockingCancelableExecutor()
    plan = Planner().plan("자비스 시스템 정보 확인")
    orchestrator = Orchestrator(
        policy=PolicyEngine(default_registry()),
        executor=executor,
        verifier=PredicateVerifier(),
        audit=InMemoryAuditSink(),
        approvals=ApprovalTokenService(b"i" * 32),
        cancellation=token,
    )
    outcomes: list[TaskOutcome] = []
    worker = threading.Thread(
        target=lambda: outcomes.append(
            orchestrator.run(
                plan,
                lambda _action: PolicyContext(
                    addressed_confidence=0.99,
                    intent_confidence=0.99,
                    target_confidence=0.99,
                    target_matches_plan=True,
                ),
            )
        )
    )
    worker.start()
    assert executor.started.wait(timeout=1)

    cancel_planner = Planner()
    decision = VoiceCommandCoordinator(
        Classifier(IntentClass.CANCEL), cancel_planner, token
    ).process(capture("멈춰"), task_active=True)
    worker.join(timeout=1)

    assert decision.status is VoiceCommandStatus.ROUTED
    assert cancel_planner.calls == []
    assert not worker.is_alive()
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.status is ActionStatus.ABORTED
    assert outcome.reason == "LOCAL_STOP"
