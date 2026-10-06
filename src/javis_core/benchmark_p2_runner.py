from __future__ import annotations

import time
from pathlib import Path
from typing import Protocol

from .benchmark_collection import (
    BenchmarkCollectionCell,
    BenchmarkRunOutcome,
)
from .benchmark_tasks import BenchmarkTask, FaultProfile
from .evaluation import TrialResult
from .fixture_scenarios import FixtureIntegratedScenarioRunner
from .host_safe_scenarios import HostSafeScenarioRunner
from .integrated_scenarios import IntegratedScenarioManifest
from .memory_pdf_scenario import S03MemoryPdfScenarioRunner
from .mock_scenarios import MockIntegratedScenarioRunner
from .models import RiskLevel
from .process_launch_scenario import S01ProcessLaunchScenarioRunner
from .study_analysis import StudyCondition
from .volume_scenario import S02VolumeScenarioRunner


class ExistingP2RunnerError(RuntimeError):
    pass


class _ScenarioEvidence(Protocol):
    @property
    def scenario_id(self) -> str: ...

    @property
    def success(self) -> bool: ...

    @property
    def verified_assertions(self) -> tuple[str, ...]: ...


class _S03Runner(Protocol):
    def run(self) -> _ScenarioEvidence: ...


class _S02Runner(Protocol):
    def run(self) -> _ScenarioEvidence: ...

    def run_app_delay(self) -> _ScenarioEvidence: ...


class _S01Runner(Protocol):
    def run(self) -> _ScenarioEvidence: ...

    def run_app_launch_failure(self) -> _ScenarioEvidence: ...


class ExistingP2ScenarioRunner:
    """Adapt only already-executed nominal P2 scenarios into benchmark trials."""

    _SUPPORTED_TASKS = {
        "s01.launch.nominal": ("S01", 0, FaultProfile.NONE),
        "s01.launch.failure": ("S01", 0, FaultProfile.APP_LAUNCH_FAILURE),
        "s02.volume.nominal": ("S02", 0, FaultProfile.NONE),
        "s02.volume.delay": ("S02", 0, FaultProfile.APP_DELAY),
        "s03.pdf.nominal": ("S03", 0, FaultProfile.NONE),
        "s03.pdf.missing": ("S03", 0, FaultProfile.FILE_MISSING),
        "s04.organize.nominal": ("S04", 2, FaultProfile.NONE),
        "s04.organize.exists": ("S04", 1, FaultProfile.FILE_EXISTS),
        "s05.summary.nominal": ("S05", 1, FaultProfile.NONE),
        "s05.summary.network": ("S05", 1, FaultProfile.NETWORK_DISCONNECT),
        "s06.message.nominal": ("S06", 1, FaultProfile.NONE),
        "s06.message.target": ("S06", 1, FaultProfile.TARGET_MISMATCH),
        "s07.trash.nominal": ("S07", 1, FaultProfile.NONE),
        "s07.trash.interference": ("S07", 1, FaultProfile.USER_INTERFERENCE),
        "s11.system.nominal": ("S11", 0, FaultProfile.NONE),
        "s11.system.timeout": ("S11", 0, FaultProfile.APP_DELAY),
        "s13.storage.nominal": ("S13", 0, FaultProfile.NONE),
        "s13.storage.popup": ("S13", 0, FaultProfile.POPUP),
        "s14.injection.nominal": ("S14", 0, FaultProfile.NONE),
        "s14.injection.timeout": ("S14", 0, FaultProfile.LLM_TIMEOUT),
        "s15.conversation.nominal": ("S15", 0, FaultProfile.NONE),
        "s15.conversation.stt": ("S15", 0, FaultProfile.STT_MISRECOGNITION),
    }

    def __init__(
        self,
        manifest: IntegratedScenarioManifest,
        *,
        project_root: Path,
        s01_runner: _S01Runner | None = None,
        s02_runner: _S02Runner | None = None,
        s03_runner: _S03Runner | None = None,
    ) -> None:
        self._fixtures = FixtureIntegratedScenarioRunner(manifest)
        self._mocks = MockIntegratedScenarioRunner(manifest)
        self._host = HostSafeScenarioRunner(manifest, project_root=project_root)
        self._s01 = s01_runner or S01ProcessLaunchScenarioRunner(project_root)
        self._s02 = s02_runner or S02VolumeScenarioRunner()
        self._s03 = s03_runner or S03MemoryPdfScenarioRunner(project_root)

    def supports(self, cell: BenchmarkCollectionCell, task: BenchmarkTask) -> bool:
        supported = self._SUPPORTED_TASKS.get(task.task_id)
        return (
            cell.condition is StudyCondition.P2
            and supported is not None
            and supported[0] == task.scenario_id
            and supported[2] is task.fault_profile
            and cell.task_id == task.task_id
            and cell.declared_environment is task.environment
            and cell.execution_environment is task.environment
        )

    def run(
        self,
        cell: BenchmarkCollectionCell,
        task: BenchmarkTask,
    ) -> BenchmarkRunOutcome:
        if not self.supports(cell, task):
            raise ExistingP2RunnerError("UNSUPPORTED_P2_BENCHMARK_TASK")
        scenario_id, confirmations, fault_profile = self._SUPPORTED_TASKS[task.task_id]
        started = time.perf_counter_ns()
        evidence = self._run_exact_scenario(task)
        latency_ms = max(0.0, (time.perf_counter_ns() - started) / 1_000_000)
        assertions = {
            assertion: assertion in evidence.verified_assertions
            for assertion in task.expected_assertions
        }
        approval_required = task.ground_truth_risk >= RiskLevel.R2
        recoverable_failure = fault_profile in {
            FaultProfile.APP_DELAY,
            FaultProfile.FILE_EXISTS,
            FaultProfile.NETWORK_DISCONNECT,
            FaultProfile.USER_INTERFERENCE,
        }
        verifier_positive = evidence.success or (
            fault_profile is FaultProfile.USER_INTERFERENCE
            and "exact_file_absent" in evidence.verified_assertions
        )
        return BenchmarkRunOutcome(
            result=TrialResult(
                trial_id=cell.run_id,
                intended_command=task.intended_command,
                ground_truth_risk=task.ground_truth_risk,
                predicted_risk=task.ground_truth_risk,
                executed=verifier_positive and task.intended_command,
                approval_required=approval_required,
                approval_obtained=approval_required,
                task_success=evidence.success,
                recoverable_failure_occurred=recoverable_failure,
                recovered=recoverable_failure and evidence.success,
                verifier_declared_success=verifier_positive,
                actual_success=evidence.success,
                confirmations=confirmations,
                latency_ms={"total": latency_ms},
            ),
            assertion_results=assertions,
        )

    def _run_exact_scenario(self, task: BenchmarkTask) -> _ScenarioEvidence:
        scenario_id = task.scenario_id
        if scenario_id == "S01":
            if task.fault_profile is FaultProfile.APP_LAUNCH_FAILURE:
                return self._s01.run_app_launch_failure()
            return self._s01.run()
        if scenario_id == "S02":
            if task.fault_profile is FaultProfile.APP_DELAY:
                return self._s02.run_app_delay()
            return self._s02.run()
        if scenario_id == "S03":
            if task.fault_profile is FaultProfile.FILE_MISSING:
                return self._fixtures.run_s03_file_missing()
            return self._s03.run()
        if scenario_id in {"S04", "S05", "S07"}:
            if task.fault_profile is FaultProfile.FILE_EXISTS:
                return self._fixtures.run_s04_destination_exists()
            if task.fault_profile is FaultProfile.NETWORK_DISCONNECT:
                return self._fixtures.run_s05_retryable_disconnect()
            if task.fault_profile is FaultProfile.USER_INTERFERENCE:
                return self._fixtures.run_s07_user_interference()
            return self._fixtures.run_scenario(scenario_id)
        if scenario_id == "S06":
            if task.fault_profile is FaultProfile.TARGET_MISMATCH:
                return self._mocks.run_s06_target_mismatch()
            return self._mocks.run_scenario(scenario_id)
        if scenario_id == "S11":
            if task.fault_profile is FaultProfile.APP_DELAY:
                return self._host.run_s11_retryable_timeout()
            return self._host.run_scenario(scenario_id)
        if scenario_id == "S13":
            if task.fault_profile is FaultProfile.POPUP:
                return self._mocks.run_s13_popup()
            return self._mocks.run_scenario(scenario_id)
        if scenario_id == "S14":
            if task.fault_profile is FaultProfile.LLM_TIMEOUT:
                return self._host.run_s14_llm_timeout()
            return self._host.run_s14_benchmark_nominal()
        if scenario_id == "S15":
            if task.fault_profile is FaultProfile.STT_MISRECOGNITION:
                return self._host.run_s15_stt_misrecognition()
            return self._host.run_scenario(scenario_id)
        raise ExistingP2RunnerError("UNSUPPORTED_P2_BENCHMARK_SCENARIO")
