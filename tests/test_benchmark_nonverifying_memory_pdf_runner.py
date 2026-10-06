from __future__ import annotations

import hashlib
import json
from pathlib import Path

from javis_core.benchmark_collection import BenchmarkExperimentCollector, CollectionAttemptStatus
from javis_core.benchmark_nonverifying_memory_pdf_runner import (
    NonVerifyingMemoryPdfConditionRunner,
)
from javis_core.benchmark_tasks import load_benchmark_tasks
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

    def resolve(
        self,
        _app: RegisteredApp,
        _launcher_pid: int,
        _previous_windows: frozenset[tuple[int, int]],
    ) -> tuple[int, int] | None:
        return (101, 202)

    def matching_windows(self, _app: RegisteredApp) -> set[tuple[int, int]]:
        return set(self.windows)


class _Executor:
    def __init__(self, resolver: _Resolver) -> None:
        self._resolver = resolver
        self.calls = 0

    def execute(self, action: Action) -> ExecutionResult:
        self.calls += 1
        path = Path(str(action.arguments["path"]))
        if not path.is_file():
            return ExecutionResult(
                action_id=action.action_id,
                executor="native",
                success=False,
                error_code="PDF_NOT_FOUND",
                error_message="fixture unavailable",
                retry_safe=True,
                duration_ms=1,
            )
        self._resolver.windows.add((101, 202))
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=True,
            observed={
                "file_sha256": action.arguments["sha256"],
                "viewer_app_id": "edge",
                "pid": 101,
                "hwnd": 202,
                "process_creation_time_100ns": 303,
            },
            duration_ms=1,
        )


class _Evaluator:
    def __init__(self, resolver: _Resolver) -> None:
        self._resolver = resolver

    def verify(self, action: Action, result: ExecutionResult) -> VerificationResult:
        path = Path(str(action.arguments["path"]))
        expected_hash = str(action.arguments["sha256"])
        success = (
            result.success
            and path.is_file()
            and hashlib.sha256(path.read_bytes()).hexdigest() == expected_hash
            and result.observed.get("file_sha256") == expected_hash
            and (result.observed.get("pid"), result.observed.get("hwnd")) in self._resolver.windows
        )
        return VerificationResult(success=success, reason="PASS" if success else "FAIL")


def test_b1_and_p1_s03_execute_once_evaluate_externally_and_cleanup(tmp_path: Path) -> None:
    root = Path.cwd().resolve()
    tasks = load_benchmark_tasks(root / "evaluation" / "benchmark_tasks.json")
    scenarios = load_integrated_scenarios(root / "evaluation" / "integrated_scenarios.json")
    executable = tmp_path / "msedge.exe"
    executable.write_bytes(b"registered edge fixture")
    app = RegisteredApp(
        app_id="edge",
        executable=executable.resolve(),
        sha256=hashlib.sha256(executable.read_bytes()).hexdigest(),
        direct_launch=True,
        document_launch_mode="browser-new-window",
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

    runner = NonVerifyingMemoryPdfConditionRunner(
        scenarios,
        project_root=root,
        apps_loader=lambda: {"edge": app},
        executor_factory=lambda _apps, _roots: executor,
        evaluator_factory=lambda _executor: _Evaluator(resolver),
        window_resolver=resolver,
        window_closer=close,
    )
    collector = BenchmarkExperimentCollector(
        tasks,
        scenarios,
        (tmp_path / "collection").resolve(),
        repetitions_per_condition=1,
        evaluator_id="javis-nonverifying-memory-pdf",
        evaluator_version="1.0.0",
        runners={
            (StudyCondition.B1, ScenarioEnvironment.HOST_FIXTURE_SANDBOX): runner,
            (StudyCondition.P1, ScenarioEnvironment.HOST_FIXTURE_SANDBOX): runner,
        },
        environment_available={environment: True for environment in ScenarioEnvironment},
        hardener=lambda _path: None,
    )
    ready = tuple(cell for cell in collector.readiness().cells if cell.status.value == "READY")
    assert len(ready) == 4
    attempts = tuple(collector.collect_next() for _ in range(4))
    assert [item.status for item in attempts] == [CollectionAttemptStatus.RECORDED] * 4
    observations = tuple(
        json.loads(line)["observation"]
        for line in (tmp_path / "collection" / "ledger.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    )
    by_key = {(item["condition"], item["task_id"]): item for item in observations}
    for condition in ("B1", "P1"):
        nominal = by_key[(condition, "s03.pdf.nominal")]["result"]
        assert nominal["executed"] is True
        assert nominal["actual_success"] is True
        assert nominal["verifier_declared_success"] is False
        assert nominal["confirmations"] == (1 if condition == "B1" else 0)
        missing = by_key[(condition, "s03.pdf.missing")]["result"]
        assert missing["executed"] is False
        assert missing["actual_success"] is False
        assert missing["verifier_declared_success"] is False
        assert missing["confirmations"] == (1 if condition == "B1" else 0)
    assert executor.calls == 4
    assert closed == [(101, 202), (101, 202)]
    assert resolver.windows == set()
