from __future__ import annotations

import hashlib
import tempfile
import time
from pathlib import Path

from .benchmark_collection import BenchmarkCollectionCell, BenchmarkRunOutcome
from .benchmark_tasks import BenchmarkTask, FaultProfile
from .document_write import (
    DocxDocumentExecutor,
    DocxDocumentVerifier,
    document_content_sha256,
    document_target_identity,
)
from .evaluation import TrialResult
from .fault_injection import FaultInjectingExecutor, FaultPhase, FaultSpec
from .file_move import FileMoveExecutor, InMemoryMoveJournal, file_sha256
from .file_trash import FileTrashExecutor
from .fixture_read import FixtureReadExecutor, FixtureReadVerifier, FixtureSourceCatalog
from .integrated_scenarios import IntegratedScenarioManifest, ScenarioEnvironment
from .models import Action, Decision, PolicyContext, Postcondition, RiskLevel
from .policy import PolicyEngine
from .registry import default_registry
from .runtime_models import ExecutionResult
from .study_analysis import StudyCondition


class NonVerifyingFixtureConditionRunnerError(RuntimeError):
    pass


class NonVerifyingFixtureConditionRunner:
    """Run disposable B1/P1 fixture actions without verifier or replan."""

    _SUPPORTED_TASKS = {
        "s04.organize.nominal": FaultProfile.NONE,
        "s04.organize.exists": FaultProfile.FILE_EXISTS,
        "s05.summary.nominal": FaultProfile.NONE,
        "s05.summary.network": FaultProfile.NETWORK_DISCONNECT,
        "s07.trash.nominal": FaultProfile.NONE,
        "s07.trash.interference": FaultProfile.USER_INTERFERENCE,
    }

    def __init__(self, manifest: IntegratedScenarioManifest) -> None:
        by_id = {item.scenario_id: item for item in manifest.scenarios}
        for scenario_id in ("S04", "S05", "S07"):
            if by_id[scenario_id].environment is not ScenarioEnvironment.HOST_FIXTURE_SANDBOX:
                raise ValueError(f"{scenario_id} must remain HOST_FIXTURE_SANDBOX")

    def supports(self, cell: BenchmarkCollectionCell, task: BenchmarkTask) -> bool:
        expected_fault = self._SUPPORTED_TASKS.get(task.task_id)
        return (
            cell.condition in {StudyCondition.B1, StudyCondition.P1}
            and cell.execution_environment is ScenarioEnvironment.HOST_FIXTURE_SANDBOX
            and cell.declared_environment is ScenarioEnvironment.HOST_FIXTURE_SANDBOX
            and cell.task_id == task.task_id
            and task.scenario_id in {"S04", "S05", "S07"}
            and expected_fault is task.fault_profile
        )

    def run(
        self,
        cell: BenchmarkCollectionCell,
        task: BenchmarkTask,
    ) -> BenchmarkRunOutcome:
        if not self.supports(cell, task):
            raise NonVerifyingFixtureConditionRunnerError("UNSUPPORTED_NONVERIFYING_FIXTURE_TASK")
        started = time.perf_counter_ns()
        if task.scenario_id == "S04" and task.fault_profile is FaultProfile.FILE_EXISTS:
            _executions, actual_success, assertions, executed = self._run_s04_exists(cell.condition)
        elif task.scenario_id == "S04":
            _executions, actual_success, assertions, executed = self._run_s04_nominal(
                cell.condition
            )
        elif task.scenario_id == "S05":
            _executions, actual_success, assertions, executed = self._run_s05(
                cell.condition,
                inject_disconnect=task.fault_profile is FaultProfile.NETWORK_DISCONNECT,
            )
        elif task.scenario_id == "S07":
            _executions, actual_success, assertions, executed = self._run_s07(
                cell.condition,
                inject_interference=task.fault_profile is FaultProfile.USER_INTERFERENCE,
            )
        else:  # pragma: no cover - supports() keeps this fail-closed
            raise NonVerifyingFixtureConditionRunnerError(
                "UNSUPPORTED_NONVERIFYING_FIXTURE_SCENARIO"
            )
        latency_ms = max(0.0, (time.perf_counter_ns() - started) / 1_000_000)
        recoverable_failure = task.fault_profile in {
            FaultProfile.FILE_EXISTS,
            FaultProfile.NETWORK_DISCONNECT,
            FaultProfile.USER_INTERFERENCE,
        }
        predicted_risk = (
            task.ground_truth_risk if cell.condition is StudyCondition.P1 else RiskLevel.R0
        )
        return BenchmarkRunOutcome(
            result=TrialResult(
                trial_id=cell.run_id,
                intended_command=task.intended_command,
                ground_truth_risk=task.ground_truth_risk,
                predicted_risk=predicted_risk,
                executed=executed,
                approval_required=True,
                approval_obtained=True,
                task_success=actual_success,
                recoverable_failure_occurred=recoverable_failure,
                recovered=False,
                verifier_declared_success=False,
                actual_success=actual_success,
                confirmations=1,
                latency_ms={"total": latency_ms},
            ),
            assertion_results={
                assertion: assertions.get(assertion, False)
                for assertion in task.expected_assertions
            },
        )

    @classmethod
    def _run_s04_nominal(
        cls,
        condition: StudyCondition,
    ) -> tuple[tuple[ExecutionResult, ...], bool, dict[str, bool], bool]:
        with tempfile.TemporaryDirectory(prefix="javis-condition-s04-") as temporary:
            root = Path(temporary).resolve()
            source_root = root / "downloads"
            destination_root = root / "organized"
            source_root.mkdir()
            destination_root.mkdir()
            fixture = (
                ("network.pdf", b"network fixture"),
                ("database.pdf", b"database fixture"),
            )
            unrelated = source_root / "keep.txt"
            unrelated.write_bytes(b"must remain")
            unrelated_hash = file_sha256(unrelated)
            actions: list[Action] = []
            expected_hashes: dict[Path, str] = {}
            for name, content in fixture:
                source = source_root / name
                destination = destination_root / name
                source.write_bytes(content)
                expected_hashes[destination] = hashlib.sha256(content).hexdigest()
                actions.append(cls._move_action(source, destination, condition))
            cls._authorize_p1(condition, tuple(actions))
            executor = FileMoveExecutor((root,), InMemoryMoveJournal())
            executions = tuple(executor.execute(action) for action in actions)
            all_moved = all(
                destination.is_file()
                and file_sha256(destination) == digest
                and not (source_root / destination.name).exists()
                for destination, digest in expected_hashes.items()
            )
            unrelated_unchanged = unrelated.is_file() and file_sha256(unrelated) == unrelated_hash
            success = (
                all_moved and unrelated_unchanged and all(result.success for result in executions)
            )
            return (
                executions,
                success,
                {
                    "all_expected_files_moved_once": all_moved,
                    "no_unexpected_file_moved": unrelated_unchanged,
                },
                any(result.success for result in executions),
            )

    @classmethod
    def _run_s04_exists(
        cls,
        condition: StudyCondition,
    ) -> tuple[tuple[ExecutionResult, ...], bool, dict[str, bool], bool]:
        with tempfile.TemporaryDirectory(prefix="javis-condition-s04-exists-") as temporary:
            root = Path(temporary).resolve()
            source_root = root / "downloads"
            destination_root = root / "organized"
            source_root.mkdir()
            destination_root.mkdir()
            source = source_root / "network.pdf"
            destination = destination_root / source.name
            unrelated = source_root / "keep.txt"
            source.write_bytes(b"source fixture")
            destination.write_bytes(b"existing destination")
            unrelated.write_bytes(b"must remain")
            before = {
                source: file_sha256(source),
                destination: file_sha256(destination),
                unrelated: file_sha256(unrelated),
            }
            action = cls._move_action(source, destination, condition)
            cls._authorize_p1(condition, (action,))
            execution = FileMoveExecutor((root,), InMemoryMoveJournal()).execute(action)
            unchanged = all(
                path.is_file() and file_sha256(path) == digest for path, digest in before.items()
            )
            return (
                (execution,),
                False,
                {
                    "all_expected_files_moved_once": False,
                    "no_unexpected_file_moved": unchanged,
                },
                execution.success,
            )

    @classmethod
    def _run_s05(
        cls,
        condition: StudyCondition,
        *,
        inject_disconnect: bool,
    ) -> tuple[tuple[ExecutionResult, ...], bool, dict[str, bool], bool]:
        with tempfile.TemporaryDirectory(prefix="javis-condition-s05-") as temporary:
            root = Path(temporary).resolve()
            (root / "source-a.html").write_text(
                "<article><h1>자료 A</h1><p>검증된 fixture 본문.</p></article>",
                encoding="utf-8",
            )
            (root / "source-b.txt").write_text(
                "두 번째 고정 자료.",
                encoding="utf-8",
            )
            catalog = FixtureSourceCatalog(
                root,
                {"s05-study": ("source-a.html", "source-b.txt")},
            )
            bundle = catalog.read("s05-study")
            output = root / "summary.docx"
            title = "S05 자료 요약"
            summary = "두 개의 고정 fixture 자료를 source-set hash에 결합한 요약입니다."
            digest = document_content_sha256(title, summary, bundle.source_set_digest)
            source_action = Action(
                capability_id="browser.fixture.read",
                arguments={"fixture_id": "s05-study"},
                purpose=f"{condition.value} single-pass fixture read",
                target_identity="browser-fixture:s05-study",
                expected_postconditions=(Postcondition(predicate="fixture.sources.bound"),),
            )
            document_action = Action(
                capability_id="document.write",
                arguments={
                    "path": str(output),
                    "title": title,
                    "body": summary,
                    "source_set_digest": bundle.source_set_digest,
                },
                purpose=f"{condition.value} single-pass fixture summary",
                target_identity=document_target_identity(output),
                expected_postconditions=(
                    Postcondition(
                        predicate="document.content.sha256",
                        arguments={"sha256": digest},
                    ),
                ),
            )
            cls._authorize_p1(condition, (source_action, document_action))
            source_delegate = FixtureReadExecutor(catalog)
            source_executor = (
                FaultInjectingExecutor(
                    source_delegate,
                    (
                        FaultSpec(
                            name="network-disconnect",
                            phase=FaultPhase.BEFORE_CALL,
                            on_call=1,
                            error_code="NETWORK_DISCONNECTED",
                            retry_safe=True,
                            executor="native",
                        ),
                    ),
                )
                if inject_disconnect
                else source_delegate
            )
            source_result = source_executor.execute(source_action)
            executions = [source_result]
            document_result: ExecutionResult | None = None
            document_executor = DocxDocumentExecutor((root,))
            if source_result.success:
                document_result = document_executor.execute(document_action)
                executions.append(document_result)

            source_matches = (
                FixtureReadVerifier(catalog)
                .verify(
                    source_action,
                    source_result,
                )
                .success
            )
            document_matches = (
                document_result is not None
                and DocxDocumentVerifier(document_executor)
                .verify(document_action, document_result)
                .success
            )
            success = source_matches and document_matches and output.is_file()
            return (
                tuple(executions),
                success,
                {
                    "source_set_matches": source_matches,
                    "document_content_hash_matches": document_matches,
                },
                document_result is not None and document_result.success,
            )

    @classmethod
    def _run_s07(
        cls,
        condition: StudyCondition,
        *,
        inject_interference: bool,
    ) -> tuple[tuple[ExecutionResult, ...], bool, dict[str, bool], bool]:
        with tempfile.TemporaryDirectory(prefix="javis-condition-s07-") as temporary:
            root = Path(temporary).resolve()
            trash_root = root / ".trash"
            trash_root.mkdir()
            target = root / "delete-me.txt"
            unrelated = root / "keep-me.txt"
            target.write_bytes(b"trash fixture")
            unrelated.write_bytes(b"unrelated fixture")
            target_hash = file_sha256(target)
            unrelated_hash = file_sha256(unrelated)

            class SandboxTrash:
                def trash(self, path: Path) -> None:
                    path.rename(trash_root / path.name)
                    if inject_interference:
                        unrelated.write_bytes(b"concurrent user change")

            action = Action(
                capability_id="file.delete.trash",
                arguments={"path": str(target)},
                purpose=f"{condition.value} single-pass fixture trash",
                target_identity=str(target.resolve()),
                expected_postconditions=(Postcondition(predicate="file.absent"),),
            )
            cls._authorize_p1(condition, (action,))
            execution = FileTrashExecutor((root,), SandboxTrash()).execute(action)
            trashed = trash_root / target.name
            exact_absent = (
                not target.exists() and trashed.is_file() and file_sha256(trashed) == target_hash
            )
            unrelated_unchanged = unrelated.is_file() and file_sha256(unrelated) == unrelated_hash
            success = execution.success and exact_absent and unrelated_unchanged
            return (
                (execution,),
                success,
                {
                    "exact_file_absent": exact_absent,
                    "unrelated_files_unchanged": unrelated_unchanged,
                },
                execution.success,
            )

    @staticmethod
    def _move_action(
        source: Path,
        destination: Path,
        condition: StudyCondition,
    ) -> Action:
        return Action(
            capability_id="file.move",
            arguments={"source": str(source), "destination": str(destination)},
            purpose=f"{condition.value} single-pass exact fixture move",
            target_identity=str(source.resolve()),
            expected_postconditions=(Postcondition(predicate="file.moved"),),
        )

    @staticmethod
    def _authorize_p1(condition: StudyCondition, actions: tuple[Action, ...]) -> None:
        if condition is not StudyCondition.P1:
            return
        policy = PolicyEngine(default_registry())
        decisions = []
        for action in actions:
            decision = policy.evaluate(
                action,
                PolicyContext(
                    addressed_confidence=1.0,
                    intent_confidence=1.0,
                    target_confidence=1.0,
                    target_matches_plan=True,
                ),
            )
            decisions.append(decision)
        if (
            any(item.decision not in {Decision.GREEN, Decision.YELLOW} for item in decisions)
            or max(item.risk for item in decisions) is not RiskLevel.R2
        ):
            raise NonVerifyingFixtureConditionRunnerError("P1_FIXTURE_POLICY_INVALID")
