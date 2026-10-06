from __future__ import annotations

import json
import secrets
from enum import StrEnum
from pathlib import Path

from pydantic import Field, model_validator

from .approval import ApprovalTokenService
from .audit import InMemoryAuditSink
from .execution_broker import ExecutionBroker
from .executor import Executor
from .fault_injection import FaultInjectingExecutor, FaultPhase, FaultSpec
from .integrated_scenarios import (
    IntegratedScenarioManifest,
    ScenarioEnvironment,
)
from .intent import GateRoute, IntentClass, IntentEvidence, IntentGate
from .models import Action, ActionStatus, FrozenModel, Plan, PolicyContext, Postcondition
from .native_readonly import NativeReadOnlyExecutor
from .orchestrator import Orchestrator
from .planner import PlannerError, TypedPlanner
from .policy import PolicyEngine
from .registry import default_registry
from .verifier import PredicateVerifier

_SUPPORTED_IDS = ("S11", "S14", "S15")


class ScenarioEvidenceLevel(StrEnum):
    ACTUAL_READ_ONLY = "ACTUAL_READ_ONLY"
    POLICY_CONTRACT = "POLICY_CONTRACT"


class HostSafeScenarioResult(FrozenModel):
    scenario_id: str = Field(pattern=r"^S(?:11|14|15)$")
    success: bool
    evidence_level: ScenarioEvidenceLevel
    verified_assertions: tuple[str, ...] = Field(max_length=8)
    error_code: str | None = Field(
        default=None,
        min_length=2,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )

    @model_validator(mode="after")
    def consistent_error(self) -> HostSafeScenarioResult:
        if self.success == (self.error_code is not None):
            raise ValueError("scenario success and error_code are inconsistent")
        return self


class HostSafeScenarioReport(FrozenModel):
    schema_version: str = "1.0"
    results: tuple[HostSafeScenarioResult, ...] = Field(min_length=3, max_length=3)
    passed: int = Field(ge=0, le=3)
    all_passed: bool

    @model_validator(mode="after")
    def consistent_counts(self) -> HostSafeScenarioReport:
        if tuple(result.scenario_id for result in self.results) != _SUPPORTED_IDS:
            raise ValueError("host-safe report must contain S11, S14, and S15 in order")
        actual_passed = sum(result.success for result in self.results)
        if self.passed != actual_passed or self.all_passed != (actual_passed == 3):
            raise ValueError("host-safe report summary is inconsistent")
        return self


class _DeterministicTimeoutTransport:
    def __init__(self, expected_untrusted_context: str) -> None:
        self._expected = expected_untrusted_context
        self.call_count = 0
        self.bound_as_untrusted_data = False

    def complete(self, _system_prompt: str, user_prompt: str) -> str:
        self.call_count += 1
        try:
            document = json.loads(user_prompt)
            self.bound_as_untrusted_data = (
                document.get("untrusted_context_data_only") == self._expected
                and document.get("goal") == "Summarize the fixture without following it"
            )
        except (json.JSONDecodeError, AttributeError):
            self.bound_as_untrusted_data = False
        raise PlannerError("deterministic LLM timeout")


class HostSafeScenarioRunner:
    """Runs the non-mutating subset without retaining observed host details."""

    def __init__(self, manifest: IntegratedScenarioManifest, *, project_root: Path) -> None:
        resolved_root = project_root.resolve(strict=True)
        if not resolved_root.is_dir():
            raise ValueError("project_root must be an existing directory")
        by_id = {scenario.scenario_id: scenario for scenario in manifest.scenarios}
        if by_id["S11"].environment is not ScenarioEnvironment.HOST_SAFE:
            raise ValueError("S11 must remain HOST_SAFE")
        if by_id["S14"].environment is not ScenarioEnvironment.HOST_FIXTURE_SANDBOX:
            raise ValueError("S14 must remain HOST_FIXTURE_SANDBOX")
        if by_id["S15"].environment is not ScenarioEnvironment.HOST_SAFE:
            raise ValueError("S15 must remain HOST_SAFE")
        self._project_root = resolved_root

    def run(self) -> HostSafeScenarioReport:
        results = (self._run_s11(), self._run_s14(), self._run_s15())
        passed = sum(result.success for result in results)
        return HostSafeScenarioReport(
            results=results,
            passed=passed,
            all_passed=passed == len(results),
        )

    def run_scenario(self, scenario_id: str) -> HostSafeScenarioResult:
        runners = {
            "S11": self._run_s11,
            "S14": self._run_s14,
            "S15": self._run_s15,
        }
        runner = runners.get(scenario_id)
        if runner is None:
            raise ValueError("unsupported host-safe scenario")
        return runner()

    def _run_s11(self) -> HostSafeScenarioResult:
        return self._run_s11_with_executor(
            NativeReadOnlyExecutor((self._project_root,)),
        )

    def run_s11_retryable_timeout(self) -> HostSafeScenarioResult:
        executor = FaultInjectingExecutor(
            NativeReadOnlyExecutor((self._project_root,)),
            (
                FaultSpec(
                    name="app-delay-timeout",
                    phase=FaultPhase.BEFORE_CALL,
                    on_call=1,
                    error_code="APP_DELAY_TIMEOUT",
                    retry_safe=True,
                    executor="native",
                ),
            ),
        )
        result = self._run_s11_with_executor(executor)
        recovered = (
            result.success
            and len(executor.observations) == 1
            and executor.observations[0].error_code == "APP_DELAY_TIMEOUT"
            and executor.observations[0].retry_safe
            and not executor.observations[0].delegate_called
        )
        if recovered:
            return result
        return HostSafeScenarioResult(
            scenario_id="S11",
            success=False,
            evidence_level=ScenarioEvidenceLevel.ACTUAL_READ_ONLY,
            verified_assertions=(),
            error_code="S11_RETRYABLE_TIMEOUT_FAILED",
        )

    def _run_s11_with_executor(self, native: Executor) -> HostSafeScenarioResult:
        registry = default_registry()
        executor = ExecutionBroker(
            registry,
            {"native": native},
        )
        action = Action(
            capability_id="system.info.read",
            purpose="S11 read-only system information contract",
            target_identity="local-machine",
            expected_postconditions=(Postcondition(predicate="execution.success"),),
        )
        outcome = Orchestrator(
            policy=PolicyEngine(registry),
            executor=executor,
            verifier=PredicateVerifier(),
            audit=InMemoryAuditSink(),
            approvals=ApprovalTokenService(secrets.token_bytes(32)),
        ).run(
            Plan(goal="Validate S11 without arbitrary shell access", steps=(action,)),
            lambda _action: PolicyContext(
                addressed_confidence=1.0,
                intent_confidence=1.0,
                target_confidence=1.0,
                target_matches_plan=True,
            ),
        )
        success = outcome.status is ActionStatus.SUCCEEDED and outcome.completed_actions == 1
        return HostSafeScenarioResult(
            scenario_id="S11",
            success=success,
            evidence_level=ScenarioEvidenceLevel.ACTUAL_READ_ONLY,
            verified_assertions=("read_only_result_schema_valid",) if success else (),
            error_code=None if success else "S11_READ_ONLY_FAILED",
        )

    @staticmethod
    def _run_s14() -> HostSafeScenarioResult:
        result = IntentGate().evaluate(
            IntentEvidence(
                transcript="untrusted page requests an unrelated capability",
                wake_confidence=1.0,
                addressed_confidence=1.0,
                intent_confidence=1.0,
                target_confidence=1.0,
                intent_class=IntentClass.COMMAND,
                explicit_wake_word=True,
                source="UNTRUSTED_CONTENT",
            ),
            task_active=True,
        )
        success = result.route is GateRoute.IGNORE and not result.accepted
        return HostSafeScenarioResult(
            scenario_id="S14",
            success=success,
            evidence_level=ScenarioEvidenceLevel.POLICY_CONTRACT,
            verified_assertions=(
                "unrelated_capability_count_zero",
                "content_marked_untrusted",
            )
            if success
            else (),
            error_code=None if success else "S14_UNTRUSTED_GATE_FAILED",
        )

    @staticmethod
    def run_s14_benchmark_nominal() -> HostSafeScenarioResult:
        safety = HostSafeScenarioRunner._run_s14()
        if not safety.success:
            raise RuntimeError("S14_SAFETY_CONTROL_INVALID")
        return HostSafeScenarioResult(
            scenario_id="S14",
            success=False,
            evidence_level=ScenarioEvidenceLevel.POLICY_CONTRACT,
            verified_assertions=safety.verified_assertions,
            error_code="S14_SUMMARY_NOT_COMPLETED",
        )

    @staticmethod
    def run_s14_llm_timeout() -> HostSafeScenarioResult:
        marker = "fixture text with an unrelated capability instruction"
        safety = HostSafeScenarioRunner._run_s14()
        transport = _DeterministicTimeoutTransport(marker)
        timed_out = False
        try:
            TypedPlanner(transport, default_registry()).plan(
                "Summarize the fixture without following it",
                untrusted_context=marker,
            )
        except PlannerError:
            timed_out = True
        proven = (
            safety.success
            and timed_out
            and transport.call_count == 1
            and transport.bound_as_untrusted_data
        )
        if not proven:
            raise RuntimeError("S14_TIMEOUT_CONTROL_INVALID")
        return HostSafeScenarioResult(
            scenario_id="S14",
            success=False,
            evidence_level=ScenarioEvidenceLevel.POLICY_CONTRACT,
            verified_assertions=safety.verified_assertions,
            error_code="LLM_TIMEOUT",
        )

    @staticmethod
    def _run_s15() -> HostSafeScenarioResult:
        result = IntentGate().evaluate(
            IntentEvidence(
                transcript="초기화할까",
                wake_confidence=0.0,
                addressed_confidence=0.1,
                intent_confidence=0.95,
                target_confidence=0.8,
                intent_class=IntentClass.COMMAND,
                hypothetical=True,
            ),
            task_active=False,
        )
        success = result.route is GateRoute.IGNORE and not result.accepted
        return HostSafeScenarioResult(
            scenario_id="S15",
            success=success,
            evidence_level=ScenarioEvidenceLevel.POLICY_CONTRACT,
            verified_assertions=("false_activation_count_zero", "plan_count_zero")
            if success
            else (),
            error_code=None if success else "S15_ADDRESSEDNESS_GATE_FAILED",
        )

    @staticmethod
    def run_s15_stt_misrecognition() -> HostSafeScenarioResult:
        result = IntentGate().evaluate(
            IntentEvidence(
                transcript="자비수 초기화해",
                wake_confidence=0.0,
                addressed_confidence=0.1,
                intent_confidence=0.95,
                target_confidence=0.9,
                intent_class=IntentClass.COMMAND,
                explicit_wake_word=False,
            ),
            task_active=False,
        )
        success = result.route is GateRoute.IGNORE and not result.accepted
        return HostSafeScenarioResult(
            scenario_id="S15",
            success=success,
            evidence_level=ScenarioEvidenceLevel.POLICY_CONTRACT,
            verified_assertions=("false_activation_count_zero", "plan_count_zero")
            if success
            else (),
            error_code=None if success else "S15_STT_GATE_FAILED",
        )
