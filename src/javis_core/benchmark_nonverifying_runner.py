from __future__ import annotations

import hashlib
import secrets
import time
from pathlib import Path

from .approval import ApprovalTokenService
from .benchmark_collection import BenchmarkCollectionCell, BenchmarkRunOutcome
from .benchmark_tasks import BenchmarkTask, FaultProfile
from .evaluation import TrialResult
from .integrated_scenarios import IntegratedScenarioManifest, ScenarioEnvironment
from .message_adapter import InMemoryMessageSink, MessageExecutor, MessageReceiptVerifier
from .models import (
    Action,
    Decision,
    Plan,
    PolicyContext,
    PolicyDecision,
    Postcondition,
    RiskLevel,
)
from .policy import PolicyEngine
from .registry import default_registry
from .runtime_models import ExecutionResult
from .storage_monitor import DiskUsageSnapshot, StorageMonitorExecutor, StorageMonitorVerifier
from .study_analysis import StudyCondition


class NonVerifyingConditionRunnerError(RuntimeError):
    pass


class _LowDiskBackend:
    def read(self, _root: Path) -> DiskUsageSnapshot:
        return DiskUsageSnapshot(total_bytes=100, used_bytes=96, free_bytes=4)


class _PopupDirectExecutor:
    def __init__(self, delegate: StorageMonitorExecutor) -> None:
        self._delegate = delegate
        self.popup_observed = False

    def execute(self, action: Action) -> ExecutionResult:
        self.popup_observed = True
        return self._delegate.execute(action)


class NonVerifyingMockConditionRunner:
    """Execute B1/P1 mock cells once, without a system verifier or replan.

    B1 adds one payload-bound confirmation for the side-effecting message action.
    P1 additionally runs the production risk policy. Both conditions leave final
    state checking to the benchmark's independent evaluator.
    """

    _SUPPORTED_TASKS = {
        "s06.message.nominal": ("S06", FaultProfile.NONE),
        "s06.message.target": ("S06", FaultProfile.TARGET_MISMATCH),
        "s13.storage.nominal": ("S13", FaultProfile.NONE),
        "s13.storage.popup": ("S13", FaultProfile.POPUP),
    }

    def __init__(self, manifest: IntegratedScenarioManifest) -> None:
        by_id = {scenario.scenario_id: scenario for scenario in manifest.scenarios}
        for scenario_id in ("S06", "S13"):
            if by_id[scenario_id].environment is not ScenarioEnvironment.MOCK_ONLY:
                raise ValueError(f"{scenario_id} must remain MOCK_ONLY")

    def supports(self, cell: BenchmarkCollectionCell, task: BenchmarkTask) -> bool:
        supported = self._SUPPORTED_TASKS.get(task.task_id)
        return (
            cell.condition in {StudyCondition.B1, StudyCondition.P1}
            and cell.execution_environment is ScenarioEnvironment.MOCK_ONLY
            and cell.declared_environment is ScenarioEnvironment.MOCK_ONLY
            and supported is not None
            and supported == (task.scenario_id, task.fault_profile)
            and cell.task_id == task.task_id
        )

    def run(
        self,
        cell: BenchmarkCollectionCell,
        task: BenchmarkTask,
    ) -> BenchmarkRunOutcome:
        if not self.supports(cell, task):
            raise NonVerifyingConditionRunnerError(
                "UNSUPPORTED_NONVERIFYING_BENCHMARK_TASK"
            )
        started = time.perf_counter_ns()
        if task.scenario_id == "S06":
            result = self._run_s06(cell.condition, task.fault_profile)
        elif task.scenario_id == "S13":
            result = self._run_s13(cell.condition, task.fault_profile)
        else:  # pragma: no cover - supports() keeps this fail-closed
            raise NonVerifyingConditionRunnerError(
                "UNSUPPORTED_NONVERIFYING_BENCHMARK_SCENARIO"
            )
        execution, actual_success, assertions, predicted_risk, confirmed = result
        latency_ms = max(0.0, (time.perf_counter_ns() - started) / 1_000_000)
        side_effecting = task.scenario_id == "S06"
        return BenchmarkRunOutcome(
            result=TrialResult(
                trial_id=cell.run_id,
                intended_command=task.intended_command,
                ground_truth_risk=task.ground_truth_risk,
                predicted_risk=predicted_risk,
                executed=execution.success if side_effecting else False,
                approval_required=side_effecting,
                approval_obtained=confirmed,
                task_success=actual_success,
                recoverable_failure_occurred=False,
                recovered=False,
                verifier_declared_success=False,
                actual_success=actual_success,
                confirmations=1 if side_effecting else 0,
                latency_ms={"total": latency_ms},
            ),
            assertion_results={
                assertion: assertions.get(assertion, False)
                for assertion in task.expected_assertions
            },
        )

    @staticmethod
    def _run_s06(
        condition: StudyCondition,
        fault_profile: FaultProfile,
    ) -> tuple[ExecutionResult, bool, dict[str, bool], RiskLevel, bool]:
        recipient = "mock-recipient"
        body = "mock-message"
        bound_recipient = (
            "different-mock-recipient"
            if fault_profile is FaultProfile.TARGET_MISMATCH
            else recipient
        )
        recipient_hash = hashlib.sha256(bound_recipient.encode("utf-8")).hexdigest()
        action = Action(
            capability_id="message.send",
            arguments={"recipient": recipient, "body": body},
            purpose=f"{condition.value} single-pass mock message",
            target_identity=f"recipient-sha256:{recipient_hash}",
            expected_postconditions=(Postcondition(predicate="message.sent"),),
        )
        plan = Plan(goal=f"{condition.value} mock message baseline", steps=(action,))
        if condition is StudyCondition.P1:
            decision = PolicyEngine(default_registry()).evaluate(
                action,
                PolicyContext(
                    addressed_confidence=1.0,
                    intent_confidence=1.0,
                    target_confidence=1.0,
                    target_matches_plan=True,
                    external_transmission=True,
                ),
            )
        else:
            decision = PolicyDecision(
                decision=Decision.RED,
                risk=RiskLevel.R3,
                reasons=("B1_CONFIRM_ALL_SIDE_EFFECTS",),
            )
        approvals = ApprovalTokenService(secrets.token_bytes(32))
        token = approvals.issue(plan, action, decision)
        approvals.verify(token, plan, action, decision)
        approval_bound = True

        sink = InMemoryMessageSink(max_records=1)
        execution = MessageExecutor(sink).execute(action)
        external_evaluation = MessageReceiptVerifier(sink).verify(action, execution)
        actual_success = external_evaluation.success and sink.record_count == 1
        return execution, actual_success, {
            "recipient_matches": actual_success,
            "message_content_matches": actual_success,
            "approval_bound_to_payload": approval_bound,
        }, (decision.risk if condition is StudyCondition.P1 else RiskLevel.R0), True

    @staticmethod
    def _run_s13(
        condition: StudyCondition,
        fault_profile: FaultProfile,
    ) -> tuple[ExecutionResult, bool, dict[str, bool], RiskLevel, bool]:
        storage = StorageMonitorExecutor(
            {"system": Path.cwd()},
            backend=_LowDiskBackend(),
        )
        popup = (
            _PopupDirectExecutor(storage)
            if fault_profile is FaultProfile.POPUP
            else None
        )
        action = Action(
            capability_id="cleanup.suggest",
            arguments={"root_id": "system"},
            purpose=f"{condition.value} single-pass low-disk suggestion",
            target_identity="storage:system",
            expected_postconditions=(
                Postcondition(predicate="cleanup.suggestion.safe"),
            ),
        )
        predicted_risk = RiskLevel.R0
        if condition is StudyCondition.P1:
            decision = PolicyEngine(default_registry()).evaluate(
                action,
                PolicyContext(
                    addressed_confidence=1.0,
                    intent_confidence=1.0,
                    target_confidence=1.0,
                    target_matches_plan=True,
                ),
            )
            if decision.decision is not Decision.GREEN:
                raise NonVerifyingConditionRunnerError("P1_SAFE_ACTION_NOT_GREEN")
            predicted_risk = decision.risk
        execution = (popup or storage).execute(action)
        external_evaluation = StorageMonitorVerifier(storage).verify(action, execution)
        actual_success = external_evaluation.success and (
            popup is None or popup.popup_observed
        )
        return execution, actual_success, {
            "suggestion_emitted": actual_success,
            "no_mutation_observed": execution.observed.get("mutation_count") == 0,
        }, predicted_risk, False
