from javis_core import (
    Action,
    ActionStatus,
    ApprovalTokenService,
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
from javis_core.models import VerificationResult
from javis_core.runtime_models import ExecutionResult


def context(_action: Action) -> PolicyContext:
    return PolicyContext(
        addressed_confidence=1.0,
        intent_confidence=1.0,
        target_confidence=1.0,
        target_matches_plan=True,
    )


class ReplacementReplanner:
    def __init__(self, replacement: Action) -> None:
        self.replacement = replacement
        self.calls = 0

    def replan(
        self,
        plan: Plan,
        failed_action: Action,
        execution: ExecutionResult,
        verification: VerificationResult,
        replan_number: int,
    ) -> Action:
        self.calls += 1
        return self.replacement


def test_replacement_action_is_reauthorized_and_verified() -> None:
    expected = (
        Postcondition(
            predicate="observed.contains",
            arguments={"values": {"ready": True}},
        ),
    )
    original = Action(
        capability_id="system.info.read",
        arguments={"mode": "bad"},
        purpose="initial attempt",
        expected_postconditions=expected,
    )
    replacement = Action(
        capability_id="system.info.read",
        arguments={"mode": "fixed"},
        purpose="replanned attempt",
        expected_postconditions=expected,
    )
    replanner = ReplacementReplanner(replacement)
    executor = MockExecutor(
        {
            "system.info.read": lambda action: {
                "ready": action.arguments.get("mode") == "fixed"
            }
        }
    )
    audit = InMemoryAuditSink()
    orchestrator = Orchestrator(
        policy=PolicyEngine(default_registry()),
        executor=executor,
        verifier=PredicateVerifier(),
        audit=audit,
        approvals=ApprovalTokenService(b"f" * 32),
        retry_budget=RetryBudget(max_attempts=1, max_replans=1),
        replanner=replanner,
    )

    outcome = orchestrator.run(Plan(goal="replan", steps=(original,)), context)

    assert outcome.status is ActionStatus.SUCCEEDED
    assert replanner.calls == 1
    assert [call.action_id for call in executor.calls] == [
        original.action_id,
        replacement.action_id,
    ]
    assert len(audit.of_type("POLICY_DECISION")) == 2
    assert len(audit.of_type("REPLAN_SCHEDULED")) == 1


def test_replan_must_use_new_action_id() -> None:
    action = Action(
        capability_id="system.info.read",
        purpose="will fail",
        expected_postconditions=(Postcondition(predicate="observed.equals", arguments={}),),
    )
    reused = action.model_copy(update={"arguments": {"changed": True}})
    executor = MockExecutor({"system.info.read": lambda _action: {"actual": True}})
    orchestrator = Orchestrator(
        policy=PolicyEngine(default_registry()),
        executor=executor,
        verifier=PredicateVerifier(),
        audit=InMemoryAuditSink(),
        approvals=ApprovalTokenService(b"g" * 32),
        retry_budget=RetryBudget(max_attempts=1, max_replans=1),
        replanner=ReplacementReplanner(reused),
    )

    outcome = orchestrator.run(Plan(goal="reject reused id", steps=(action,)), context)

    assert outcome.status is ActionStatus.ABORTED
    assert outcome.reason == "REPLAN_REUSED_ACTION_ID"
    assert len(executor.calls) == 1
