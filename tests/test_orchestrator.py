from collections.abc import Callable

from javis_core import (
    Action,
    ActionStatus,
    ApprovalArtifact,
    ApprovalTokenService,
    ExecutionResult,
    InMemoryAuditSink,
    MockExecutor,
    Orchestrator,
    Plan,
    PolicyContext,
    PolicyEngine,
    Postcondition,
    PredicateVerifier,
    RetryBudget,
    default_registry,
)
from javis_core.cancellation import CancellationToken
from javis_core.models import PolicyDecision


def safe_context(**overrides: object) -> PolicyContext:
    values: dict[str, object] = {
        "addressed_confidence": 1.0,
        "intent_confidence": 1.0,
        "target_confidence": 1.0,
        "target_matches_plan": True,
    }
    values.update(overrides)
    return PolicyContext.model_validate(values)


class SigningBroker:
    def __init__(
        self,
        approvals: ApprovalTokenService,
        *,
        yellow: bool = True,
        strong_auth: bool = True,
    ) -> None:
        self._approvals = approvals
        self._yellow = yellow
        self._strong_auth = strong_auth

    def approve_yellow(
        self,
        plan: Plan,
        action: Action,
        decision: PolicyDecision,
    ) -> bool:
        return self._yellow

    def approve_red(
        self,
        plan: Plan,
        action: Action,
        decision: PolicyDecision,
    ) -> ApprovalArtifact:
        return ApprovalArtifact(
            token=self._approvals.issue(plan, action, decision),
            strong_auth_completed=self._strong_auth,
        )


def build_orchestrator(
    handlers: dict[str, Callable[[Action], dict[str, object]]],
    *,
    broker: SigningBroker | None = None,
    attempts: int = 2,
) -> tuple[Orchestrator, MockExecutor, InMemoryAuditSink]:
    approvals = ApprovalTokenService(b"b" * 32)
    executor = MockExecutor(handlers)  # type: ignore[arg-type]
    audit = InMemoryAuditSink()
    orchestrator = Orchestrator(
        policy=PolicyEngine(default_registry()),
        executor=executor,
        verifier=PredicateVerifier(),
        audit=audit,
        approvals=approvals,
        approval_broker=broker or SigningBroker(approvals),
        retry_budget=RetryBudget(max_attempts=attempts),
    )
    return orchestrator, executor, audit


def test_green_action_executes_and_verifies() -> None:
    action = Action(
        capability_id="system.info.read",
        purpose="read host name",
        target_identity="local-machine",
        expected_postconditions=(
            Postcondition(
                predicate="observed.contains",
                arguments={"values": {"host": "test-pc"}},
            ),
        ),
    )
    plan = Plan(goal="read system info", steps=(action,))
    orchestrator, executor, audit = build_orchestrator(
        {"system.info.read": lambda _action: {"host": "test-pc"}}
    )

    outcome = orchestrator.run(plan, lambda _action: safe_context())

    assert outcome.status is ActionStatus.SUCCEEDED
    assert outcome.completed_actions == 1
    assert len(executor.calls) == 1
    assert len(audit.of_type("TASK_FINISHED")) == 1


def test_policy_denial_never_calls_executor() -> None:
    action = Action(capability_id="system.info.read", purpose="blocked")
    plan = Plan(goal="blocked", steps=(action,))
    orchestrator, executor, audit = build_orchestrator(
        {"system.info.read": lambda _action: {}}
    )

    outcome = orchestrator.run(
        plan,
        lambda _action: safe_context(suspected_prompt_injection=True),
    )

    assert outcome.status is ActionStatus.ABORTED
    assert outcome.reason.startswith("POLICY_DENIED")
    assert executor.calls == ()
    assert len(audit.of_type("TASK_ABORTED")) == 1


def test_recoverable_failure_retries_within_budget() -> None:
    call_count = 0

    def flaky(_action: Action) -> dict[str, object]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("injected")
        return {"ready": True}

    action = Action(
        capability_id="system.info.read",
        purpose="retry",
        expected_postconditions=(
            Postcondition(
                predicate="observed.contains",
                arguments={"values": {"ready": True}},
            ),
        ),
    )
    plan = Plan(goal="retry", steps=(action,))
    orchestrator, executor, audit = build_orchestrator({"system.info.read": flaky})

    outcome = orchestrator.run(plan, lambda _action: safe_context())

    assert outcome.status is ActionStatus.SUCCEEDED
    assert len(executor.calls) == 2
    assert len(audit.of_type("RETRY_SCHEDULED")) == 1


def test_unknown_postcondition_is_not_retried() -> None:
    action = Action(
        capability_id="system.info.read",
        purpose="unknown verifier",
        expected_postconditions=(Postcondition(predicate="unknown.predicate"),),
    )
    plan = Plan(goal="fail closed", steps=(action,))
    orchestrator, executor, _audit = build_orchestrator(
        {"system.info.read": lambda _action: {}}
    )

    outcome = orchestrator.run(plan, lambda _action: safe_context())

    assert outcome.status is ActionStatus.ABORTED
    assert outcome.reason == "UNKNOWN_POSTCONDITION"
    assert len(executor.calls) == 1


def test_r4_requires_strong_auth_before_execution() -> None:
    action = Action(
        capability_id="system.reset.commit",
        purpose="mock reset",
        target_identity="vm-test",
    )
    plan = Plan(goal="test strong auth", steps=(action,))
    approvals = ApprovalTokenService(b"c" * 32)
    broker = SigningBroker(approvals, strong_auth=False)
    executor = MockExecutor({"system.reset.commit": lambda _action: {"mocked": True}})
    orchestrator = Orchestrator(
        policy=PolicyEngine(default_registry()),
        executor=executor,
        verifier=PredicateVerifier(),
        audit=InMemoryAuditSink(),
        approvals=approvals,
        approval_broker=broker,
    )

    outcome = orchestrator.run(plan, lambda _action: safe_context())

    assert outcome.status is ActionStatus.ABORTED
    assert outcome.reason == "STRONG_AUTH_REQUIRED"
    assert executor.calls == ()


class AmbiguousFailureExecutor:
    def __init__(self) -> None:
        self.calls = 0

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        self.calls += 1
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=False,
            error_code="AMBIGUOUS_SIDE_EFFECT",
            retry_safe=False,
            duration_ms=1,
        )


def test_ambiguous_side_effect_failure_is_never_retried() -> None:
    action = Action(capability_id="process.launch", purpose="ambiguous launch")
    plan = Plan(goal="do not duplicate", steps=(action,))
    executor = AmbiguousFailureExecutor()
    orchestrator = Orchestrator(
        policy=PolicyEngine(default_registry()),
        executor=executor,
        verifier=PredicateVerifier(),
        audit=InMemoryAuditSink(),
        approvals=ApprovalTokenService(b"h" * 32),
        retry_budget=RetryBudget(max_attempts=3),
    )

    outcome = orchestrator.run(plan, lambda _action: safe_context())

    assert outcome.status is ActionStatus.ABORTED
    assert outcome.reason == "AMBIGUOUS_SIDE_EFFECT"
    assert executor.calls == 1
