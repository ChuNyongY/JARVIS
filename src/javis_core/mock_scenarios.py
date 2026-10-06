from __future__ import annotations

import hashlib
import secrets
from pathlib import Path

from pydantic import Field, model_validator

from .approval import ApprovalTokenService
from .audit import InMemoryAuditSink
from .cancellation import CancellationToken
from .execution_broker import ExecutionBroker
from .executor import Executor
from .integrated_scenarios import IntegratedScenarioManifest, ScenarioEnvironment
from .message_adapter import InMemoryMessageSink, MessageExecutor, MessageReceiptVerifier
from .models import (
    Action,
    ActionStatus,
    FrozenModel,
    Plan,
    PolicyContext,
    PolicyDecision,
    Postcondition,
)
from .orchestrator import ApprovalBroker, Orchestrator
from .policy import PolicyEngine
from .registry import default_registry
from .runtime_models import ApprovalArtifact, ExecutionResult
from .storage_monitor import (
    DiskUsageSnapshot,
    StorageMonitorExecutor,
    StorageMonitorVerifier,
)


class MockScenarioResult(FrozenModel):
    scenario_id: str = Field(pattern=r"^S(?:06|13)$")
    success: bool
    evidence_level: str = Field(pattern=r"^MOCK_CONTRACT$")
    verified_assertions: tuple[str, ...] = Field(max_length=8)
    external_effect_count: int = Field(ge=0, le=0)
    error_code: str | None = Field(
        default=None,
        min_length=2,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )

    @model_validator(mode="after")
    def consistent_error(self) -> MockScenarioResult:
        if self.success == (self.error_code is not None):
            raise ValueError("mock scenario success and error_code are inconsistent")
        return self


class MockScenarioReport(FrozenModel):
    schema_version: str = "1.0"
    results: tuple[MockScenarioResult, ...] = Field(min_length=2, max_length=2)
    passed: int = Field(ge=0, le=2)
    all_passed: bool

    @model_validator(mode="after")
    def consistent_summary(self) -> MockScenarioReport:
        if tuple(item.scenario_id for item in self.results) != ("S06", "S13"):
            raise ValueError("mock report must contain S06 and S13 in order")
        actual = sum(item.success for item in self.results)
        if self.passed != actual or self.all_passed != (actual == 2):
            raise ValueError("mock report summary is inconsistent")
        return self


class _PopupInterferenceExecutor:
    """Record one irrelevant popup disturbance while preserving the delegated action."""

    def __init__(self, delegate: Executor) -> None:
        self._delegate = delegate
        self.popup_observed = False

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        self.popup_observed = True
        return self._delegate.execute(action, cancellation)


class _ExactApprovalBroker(ApprovalBroker):
    def __init__(self, approvals: ApprovalTokenService) -> None:
        self._approvals = approvals

    def approve_yellow(
        self,
        plan: Plan,
        action: Action,
        decision: PolicyDecision,
    ) -> bool:
        return False

    def approve_red(
        self,
        plan: Plan,
        action: Action,
        decision: PolicyDecision,
    ) -> ApprovalArtifact:
        return ApprovalArtifact(
            token=self._approvals.issue(plan, action, decision),
            strong_auth_completed=False,
        )


class MockIntegratedScenarioRunner:
    """Executes S06 through RED approval using a content-free mock sink."""

    def __init__(self, manifest: IntegratedScenarioManifest) -> None:
        by_id = {item.scenario_id: item for item in manifest.scenarios}
        if by_id["S06"].environment is not ScenarioEnvironment.MOCK_ONLY:
            raise ValueError("S06 must remain MOCK_ONLY")
        if by_id["S13"].environment is not ScenarioEnvironment.MOCK_ONLY:
            raise ValueError("S13 must remain MOCK_ONLY")

    def run(self) -> MockScenarioReport:
        results = (self.run_s06(), self.run_s13())
        passed = sum(item.success for item in results)
        return MockScenarioReport(
            results=results,
            passed=passed,
            all_passed=passed == len(results),
        )

    def run_scenario(self, scenario_id: str) -> MockScenarioResult:
        runners = {"S06": self.run_s06, "S13": self.run_s13}
        runner = runners.get(scenario_id)
        if runner is None:
            raise ValueError("unsupported mock scenario")
        return runner()

    def run_s06_target_mismatch(self) -> MockScenarioResult:
        recipient = "철수"
        body = "8시에 게임하자"
        wrong_target_hash = hashlib.sha256("영희".encode()).hexdigest()
        sink = InMemoryMessageSink(max_records=1)
        registry = default_registry()
        approvals = ApprovalTokenService(secrets.token_bytes(32))
        audit = InMemoryAuditSink()
        action = Action(
            capability_id="message.send",
            arguments={"recipient": recipient, "body": body},
            purpose="S06 reject a mismatched recipient identity",
            target_identity=f"recipient-sha256:{wrong_target_hash}",
            expected_postconditions=(Postcondition(predicate="message.sent"),),
        )
        outcome = Orchestrator(
            policy=PolicyEngine(registry),
            executor=ExecutionBroker(registry, {"native": MessageExecutor(sink)}),
            verifier=MessageReceiptVerifier(sink),
            audit=audit,
            approvals=approvals,
            approval_broker=_ExactApprovalBroker(approvals),
        ).run(
            Plan(goal="Evaluate S06 target mismatch", steps=(action,)),
            lambda _action: PolicyContext(
                addressed_confidence=1.0,
                intent_confidence=1.0,
                target_confidence=1.0,
                target_matches_plan=True,
                external_transmission=True,
            ),
        )
        safely_blocked = (
            outcome.status is ActionStatus.ABORTED
            and sink.record_count == 0
            and len(audit.of_type("APPROVAL_VERIFIED")) == 1
        )
        return MockScenarioResult(
            scenario_id="S06",
            success=False,
            evidence_level="MOCK_CONTRACT",
            verified_assertions=("approval_bound_to_payload",)
            if safely_blocked
            else (),
            external_effect_count=0,
            error_code="S06_TARGET_MISMATCH",
        )

    def run_s06(self) -> MockScenarioResult:
        recipient = "철수"
        body = "8시에 게임하자"
        recipient_hash = hashlib.sha256(recipient.encode("utf-8")).hexdigest()
        sink = InMemoryMessageSink(max_records=1)
        registry = default_registry()
        approvals = ApprovalTokenService(secrets.token_bytes(32))
        audit = InMemoryAuditSink()
        action = Action(
            capability_id="message.send",
            arguments={"recipient": recipient, "body": body},
            purpose="S06 exact mock message",
            target_identity=f"recipient-sha256:{recipient_hash}",
            expected_postconditions=(Postcondition(predicate="message.sent"),),
        )
        outcome = Orchestrator(
            policy=PolicyEngine(registry),
            executor=ExecutionBroker(registry, {"native": MessageExecutor(sink)}),
            verifier=MessageReceiptVerifier(sink),
            audit=audit,
            approvals=approvals,
            approval_broker=_ExactApprovalBroker(approvals),
        ).run(
            Plan(goal="Evaluate S06 without external delivery", steps=(action,)),
            lambda _action: PolicyContext(
                addressed_confidence=1.0,
                intent_confidence=1.0,
                target_confidence=1.0,
                target_matches_plan=True,
                external_transmission=True,
            ),
        )
        success = (
            outcome.status is ActionStatus.SUCCEEDED
            and outcome.completed_actions == 1
            and sink.record_count == 1
            and len(audit.of_type("APPROVAL_VERIFIED")) == 1
        )
        return MockScenarioResult(
            scenario_id="S06",
            success=success,
            evidence_level="MOCK_CONTRACT",
            verified_assertions=(
                "recipient_matches",
                "message_content_matches",
                "approval_bound_to_payload",
            )
            if success
            else (),
            external_effect_count=0,
            error_code=None if success else "S06_MOCK_FAILED",
        )

    @staticmethod
    def run_s13() -> MockScenarioResult:
        return MockIntegratedScenarioRunner._run_s13(popup=False)

    @staticmethod
    def run_s13_popup() -> MockScenarioResult:
        return MockIntegratedScenarioRunner._run_s13(popup=True)

    @staticmethod
    def _run_s13(*, popup: bool) -> MockScenarioResult:
        class LowDiskBackend:
            def read(self, _root: Path) -> DiskUsageSnapshot:
                return DiskUsageSnapshot(total_bytes=100, used_bytes=96, free_bytes=4)

        backend = LowDiskBackend()
        storage_executor = StorageMonitorExecutor(
            {"system": Path.cwd()},
            backend=backend,
        )
        popup_executor = _PopupInterferenceExecutor(storage_executor) if popup else None
        executor: Executor = popup_executor or storage_executor
        registry = default_registry()
        audit = InMemoryAuditSink()
        action = Action(
            capability_id="cleanup.suggest",
            arguments={"root_id": "system"},
            purpose="S13 low-disk suggestion without mutation",
            target_identity="storage:system",
            expected_postconditions=(
                Postcondition(predicate="cleanup.suggestion.safe"),
            ),
        )
        outcome = Orchestrator(
            policy=PolicyEngine(registry),
            executor=ExecutionBroker(registry, {"native": executor}),
            verifier=StorageMonitorVerifier(storage_executor),
            audit=audit,
            approvals=ApprovalTokenService(secrets.token_bytes(32)),
        ).run(
            Plan(goal="Evaluate S13 suggestion-only behavior", steps=(action,)),
            lambda _action: PolicyContext(
                addressed_confidence=1.0,
                intent_confidence=1.0,
                target_confidence=1.0,
                target_matches_plan=True,
            ),
        )
        success = (
            outcome.status is ActionStatus.SUCCEEDED
            and outcome.completed_actions == 1
            and len(audit.of_type("APPROVAL_VERIFIED")) == 0
            and (popup_executor is None or popup_executor.popup_observed)
        )
        return MockScenarioResult(
            scenario_id="S13",
            success=success,
            evidence_level="MOCK_CONTRACT",
            verified_assertions=("suggestion_emitted", "no_mutation_observed")
            if success
            else (),
            external_effect_count=0,
            error_code=None if success else "S13_MOCK_FAILED",
        )
