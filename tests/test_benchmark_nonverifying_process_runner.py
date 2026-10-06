from __future__ import annotations

import hashlib
import json
from pathlib import Path

from javis_core.benchmark_collection import BenchmarkExperimentCollector, CollectionAttemptStatus
from javis_core.benchmark_nonverifying_process_runner import (
    NonVerifyingProcessConditionRunner,
)
from javis_core.benchmark_tasks import load_benchmark_tasks
from javis_core.cancellation import CancellationToken
from javis_core.integrated_scenarios import ScenarioEnvironment, load_integrated_scenarios
from javis_core.models import Action, VerificationResult
from javis_core.process_launch import RegisteredApp
from javis_core.runtime_models import ExecutionResult
from javis_core.study_analysis import StudyCondition


class _Resolver:
    def __init__(self) -> None:
        self.windows: set[tuple[int, int]] = set()

    def snapshot(self, _app: RegisteredApp) -> frozenset[tuple[int, int]]:
        return frozenset(self.windows)

    def matching_windows(self, _app: RegisteredApp) -> set[tuple[int, int]]:
        return set(self.windows)


class _Executor:
    name = "native"

    def __init__(self, resolver: _Resolver) -> None:
        self._resolver = resolver
        self.calls = 0

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        del cancellation
        self.calls += 1
        self._resolver.windows.add((101, 202))
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=True,
            observed={"app_id": "chrome", "pid": 101, "hwnd": 202},
            duration_ms=1,
        )


class _Evaluator:
    def __init__(self, resolver: _Resolver) -> None:
        self._resolver = resolver

    def verify(self, action: Action, result: ExecutionResult) -> VerificationResult:
        success = (
            result.action_id == action.action_id
            and result.success
            and (result.observed.get("pid"), result.observed.get("hwnd")) in self._resolver.windows
        )
        return VerificationResult(success=success, reason="PASS" if success else "FAIL")


def test_b1_and_p1_s01_launch_once_and_cleanup_exact_window(tmp_path: Path) -> None:
    root = Path.cwd().resolve()
    tasks = load_benchmark_tasks(root / "evaluation" / "benchmark_tasks.json")
    scenarios = load_integrated_scenarios(root / "evaluation" / "integrated_scenarios.json")
    executable = tmp_path / "chrome.exe"
    executable.write_bytes(b"registered fixture")
    app = RegisteredApp(
        app_id="chrome",
        executable=executable.resolve(),
        sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        single_instance_handoff=True,
        window_class="Chrome_WidgetWin_1",
    )
    resolver = _Resolver()
    executor = _Executor(resolver)
    closed: list[tuple[int, int]] = []

    def close(_app: RegisteredApp, pid: int, hwnd: int) -> bool:
        closed.append((pid, hwnd))
        resolver.windows.remove((pid, hwnd))
        return True

    runner = NonVerifyingProcessConditionRunner(
        scenarios,
        project_root=root,
        apps_loader=lambda: {"chrome": app},
        executor_factory=lambda _apps: executor,
        evaluator_factory=lambda _apps: _Evaluator(resolver),
        window_resolver=resolver,
        window_closer=close,
    )
    collector = BenchmarkExperimentCollector(
        tasks,
        scenarios,
        (tmp_path / "collection").resolve(),
        repetitions_per_condition=1,
        evaluator_id="javis-nonverifying-process",
        evaluator_version="1.0.0",
        runners={
            (StudyCondition.B1, ScenarioEnvironment.HOST_SAFE): runner,
            (StudyCondition.P1, ScenarioEnvironment.HOST_SAFE): runner,
        },
        environment_available={environment: True for environment in ScenarioEnvironment},
        hardener=lambda _path: None,
    )
    ready = tuple(cell for cell in collector.readiness().cells if cell.status.value == "READY")
    assert len(ready) == 4
    attempts = tuple(collector.collect_next() for _ in range(4))
    assert all(item.status is CollectionAttemptStatus.RECORDED for item in attempts)
    observations = tuple(
        json.loads(line)["observation"]
        for line in (tmp_path / "collection" / "ledger.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    by_key = {(item["condition"], item["task_id"]): item for item in observations}
    for condition in ("B1", "P1"):
        nominal = by_key[(condition, "s01.launch.nominal")]["result"]
        assert nominal["executed"] is True
        assert nominal["task_success"] is True
        assert nominal["actual_success"] is True
        assert nominal["verifier_declared_success"] is False
        assert nominal["confirmations"] == (1 if condition == "B1" else 0)
        failure = by_key[(condition, "s01.launch.failure")]["result"]
        assert failure["executed"] is False
        assert failure["task_success"] is False
        assert failure["actual_success"] is False
    assert executor.calls == 2
    assert closed == [(101, 202), (101, 202)]
    assert resolver.windows == set()
