from __future__ import annotations

import ctypes
import hashlib
import tempfile
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol
from uuid import UUID

from .app_registry import AppRegistryLoader
from .benchmark_collection import BenchmarkCollectionCell, BenchmarkRunOutcome
from .benchmark_tasks import BenchmarkTask, FaultProfile
from .evaluation import TrialResult
from .integrated_scenarios import IntegratedScenarioManifest, ScenarioEnvironment
from .memory import (
    MemorySensitivity,
    MemorySource,
    SemanticMemoryCandidate,
    SqliteMemoryRepository,
)
from .memory_pdf_scenario import _write_minimal_pdf
from .memory_search import MemorySearchExecutor
from .models import Action, Decision, PolicyContext, Postcondition, RiskLevel
from .pdf_open import PdfOpenedVerifier, PdfWindowResolver, RegisteredPdfOpenExecutor
from .policy import PolicyEngine
from .process_launch import RegisteredApp
from .registry import default_registry
from .runtime_models import ExecutionResult
from .study_analysis import StudyCondition
from .verifier import Verifier
from .window_handoff import WindowsRuntimeWindowResolver


class NonVerifyingMemoryPdfConditionRunnerError(RuntimeError):
    pass


class _PdfExecutor(Protocol):
    def execute(self, action: Action) -> ExecutionResult: ...


class NonVerifyingMemoryPdfConditionRunner:
    """Run B1/P1 S03 once and leave success judgment to an external evaluator."""

    _SUPPORTED_TASKS = {
        "s03.pdf.nominal": FaultProfile.NONE,
        "s03.pdf.missing": FaultProfile.FILE_MISSING,
    }

    def __init__(
        self,
        manifest: IntegratedScenarioManifest,
        *,
        project_root: Path,
        apps_loader: Callable[[], Mapping[str, RegisteredApp]] | None = None,
        executor_factory: Callable[[Mapping[str, RegisteredApp], tuple[Path, ...]], _PdfExecutor]
        | None = None,
        evaluator_factory: Callable[[_PdfExecutor], Verifier] | None = None,
        window_resolver: PdfWindowResolver | None = None,
        window_closer: Callable[[RegisteredApp, int, int], bool] | None = None,
    ) -> None:
        scenario = next(item for item in manifest.scenarios if item.scenario_id == "S03")
        if scenario.environment is not ScenarioEnvironment.HOST_FIXTURE_SANDBOX:
            raise ValueError("S03 must remain HOST_FIXTURE_SANDBOX")
        self._project_root = project_root.resolve(strict=True)
        if not self._project_root.is_dir():
            raise ValueError("project_root must be an existing directory")
        self._apps_loader = apps_loader or self._load_apps
        self._executor_factory = executor_factory or self._default_executor
        self._evaluator_factory = evaluator_factory or self._default_evaluator
        self._resolver = window_resolver or WindowsRuntimeWindowResolver()
        self._closer = window_closer or self._close_exact_window

    def supports(self, cell: BenchmarkCollectionCell, task: BenchmarkTask) -> bool:
        expected_fault = self._SUPPORTED_TASKS.get(task.task_id)
        return (
            cell.condition in {StudyCondition.B1, StudyCondition.P1}
            and cell.execution_environment is ScenarioEnvironment.HOST_FIXTURE_SANDBOX
            and cell.declared_environment is ScenarioEnvironment.HOST_FIXTURE_SANDBOX
            and cell.task_id == task.task_id
            and task.scenario_id == "S03"
            and expected_fault is task.fault_profile
        )

    def run(
        self,
        cell: BenchmarkCollectionCell,
        task: BenchmarkTask,
    ) -> BenchmarkRunOutcome:
        if not self.supports(cell, task):
            raise NonVerifyingMemoryPdfConditionRunnerError(
                "UNSUPPORTED_NONVERIFYING_MEMORY_PDF_TASK"
            )
        apps = self._apps_loader()
        app = apps.get("edge")
        if app is None:
            raise NonVerifyingMemoryPdfConditionRunnerError("REGISTERED_PDF_VIEWER_UNAVAILABLE")

        started = time.perf_counter_ns()
        with tempfile.TemporaryDirectory(
            prefix="javis-condition-s03-", ignore_cleanup_errors=True
        ) as temporary:
            root = Path(temporary).resolve(strict=True)
            document = root / "certification-study.pdf"
            _write_minimal_pdf(document)
            store = SqliteMemoryRepository((root / "memory.sqlite3").resolve())
            store.remember(
                SemanticMemoryCandidate(
                    key="study.certification_pdf",
                    value=str(document.resolve()),
                    source=MemorySource.VERIFIED_TASK,
                    confidence=1.0,
                    sensitivity=MemorySensitivity.LOW,
                )
            )
            search_action = self._search_action(cell.condition)
            predicted_risk = self._authorize_p1(cell.condition, search_action)
            search_result = MemorySearchExecutor(store, store_id="s03-fixture").execute(
                search_action
            )
            open_action = self._derive_open_action(root, store, search_result)
            predicted_risk = max(
                predicted_risk,
                self._authorize_p1(cell.condition, open_action),
            )
            confirmation_required = cell.condition is StudyCondition.B1

            if task.fault_profile is FaultProfile.FILE_MISSING:
                document.unlink()

            baseline = self._resolver.snapshot(app)
            executor = self._executor_factory(apps, (root,))
            execution = executor.execute(open_action)
            external = self._evaluator_factory(executor).verify(open_action, execution)
            cleanup_ok = not execution.success
            if execution.success:
                cleanup_ok = self._close_observed(app, execution)
            identity_matches = external.success
            viewer_exists = external.success
            actual_success = identity_matches and viewer_exists and cleanup_ok
            if task.fault_profile is FaultProfile.FILE_MISSING:
                new_windows = self._resolver.matching_windows(app) - baseline
                if execution.success or new_windows:
                    raise NonVerifyingMemoryPdfConditionRunnerError(
                        "S03_FILE_MISSING_CONTROL_INVALID"
                    )

        latency_ms = max(0.0, (time.perf_counter_ns() - started) / 1_000_000)
        return BenchmarkRunOutcome(
            result=TrialResult(
                trial_id=cell.run_id,
                intended_command=task.intended_command,
                ground_truth_risk=task.ground_truth_risk,
                predicted_risk=(
                    predicted_risk if cell.condition is StudyCondition.P1 else RiskLevel.R0
                ),
                executed=execution.success,
                approval_required=confirmation_required,
                approval_obtained=confirmation_required,
                task_success=actual_success,
                recoverable_failure_occurred=False,
                recovered=False,
                verifier_declared_success=False,
                actual_success=actual_success,
                confirmations=1 if confirmation_required else 0,
                latency_ms={"total": latency_ms},
            ),
            assertion_results={
                assertion: {
                    "expected_pdf_identity_matches": identity_matches,
                    "viewer_process_exists": viewer_exists,
                }.get(assertion, False)
                for assertion in task.expected_assertions
            },
        )

    def _load_apps(self) -> Mapping[str, RegisteredApp]:
        return AppRegistryLoader(require_publisher=True).load(
            (self._project_root / "config" / "apps.json").resolve(strict=True)
        )

    @staticmethod
    def _default_executor(
        apps: Mapping[str, RegisteredApp], roots: tuple[Path, ...]
    ) -> RegisteredPdfOpenExecutor:
        viewer = apps.get("edge")
        if viewer is None:
            raise NonVerifyingMemoryPdfConditionRunnerError("REGISTERED_PDF_VIEWER_UNAVAILABLE")
        return RegisteredPdfOpenExecutor(
            {"edge": viewer},
            roots,
            window_resolver=WindowsRuntimeWindowResolver(),
        )

    @staticmethod
    def _default_evaluator(executor: _PdfExecutor) -> Verifier:
        if not isinstance(executor, RegisteredPdfOpenExecutor):
            raise NonVerifyingMemoryPdfConditionRunnerError("PDF_EVALUATOR_CONFIGURATION_INVALID")
        return PdfOpenedVerifier(
            executor,
            window_resolver=WindowsRuntimeWindowResolver(),
        )

    @staticmethod
    def _search_action(condition: StudyCondition) -> Action:
        return Action(
            capability_id="memory.search",
            arguments={"query": "certification study PDF", "k": 3},
            purpose=f"{condition.value} single-pass remembered PDF lookup",
            target_identity="memory-store:s03-fixture",
            expected_postconditions=(Postcondition(predicate="memory.search.matches"),),
        )

    @staticmethod
    def _derive_open_action(
        root: Path,
        store: SqliteMemoryRepository,
        search_result: ExecutionResult,
    ) -> Action:
        raw_id = search_result.observed.get("unique_memory_id")
        if (
            not search_result.success
            or search_result.observed.get("match_count") != 1
            or not isinstance(raw_id, str)
        ):
            raise NonVerifyingMemoryPdfConditionRunnerError("S03_MEMORY_RESULT_INVALID")
        try:
            record = store.get_by_id(UUID(raw_id))
        except (ValueError, OSError):
            record = None
        if record is None or not isinstance(record.value, str):
            raise NonVerifyingMemoryPdfConditionRunnerError("S03_MEMORY_RECORD_UNAVAILABLE")
        candidate = Path(record.value)
        try:
            document = candidate.resolve(strict=True)
            allowed = (
                candidate.is_absolute()
                and not candidate.is_symlink()
                and document.is_file()
                and document.suffix.casefold() == ".pdf"
                and document.is_relative_to(root)
                and 1 <= document.stat().st_size <= 512 * 1024 * 1024
            )
        except OSError:
            allowed = False
        if not allowed:
            raise NonVerifyingMemoryPdfConditionRunnerError("S03_MEMORY_PDF_NOT_ALLOWED")
        digest = NonVerifyingMemoryPdfConditionRunner._hash_file(document)
        return Action(
            capability_id="file.open",
            arguments={
                "path": str(document),
                "sha256": digest,
                "viewer_app_id": "edge",
            },
            purpose="open the exact remembered fixture PDF once",
            target_identity=f"file-sha256:{digest}",
            expected_postconditions=(Postcondition(predicate="file.opened"),),
        )

    @staticmethod
    def _authorize_p1(condition: StudyCondition, action: Action) -> RiskLevel:
        if condition is not StudyCondition.P1:
            return RiskLevel.R0
        decision = PolicyEngine(default_registry()).evaluate(
            action,
            PolicyContext(
                addressed_confidence=1.0,
                intent_confidence=1.0,
                target_confidence=1.0,
                target_matches_plan=True,
            ),
        )
        if decision.decision is not Decision.GREEN or decision.risk not in {
            RiskLevel.R0,
            RiskLevel.R1,
        }:
            raise NonVerifyingMemoryPdfConditionRunnerError("P1_MEMORY_PDF_POLICY_INVALID")
        return decision.risk

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError:
            raise NonVerifyingMemoryPdfConditionRunnerError(
                "S03_MEMORY_PDF_HASH_FAILED"
            ) from None
        return digest.hexdigest()

    def _close_observed(self, app: RegisteredApp, result: ExecutionResult) -> bool:
        pid = result.observed.get("pid")
        hwnd = result.observed.get("hwnd")
        if type(pid) is not int or type(hwnd) is not int:
            return False
        if (pid, hwnd) not in self._resolver.matching_windows(app):
            return False
        return self._closer(app, pid, hwnd)

    @staticmethod
    def _close_exact_window(app: RegisteredApp, pid: int, hwnd: int) -> bool:
        resolver = WindowsRuntimeWindowResolver()
        if (pid, hwnd) not in resolver.matching_windows(app):
            return False
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.PostMessageW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        ]
        user32.PostMessageW.restype = ctypes.c_bool
        if not user32.PostMessageW(hwnd, 0x0010, 0, 0):
            return False
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if (pid, hwnd) not in resolver.matching_windows(app):
                return True
            time.sleep(0.05)
        return False
