from __future__ import annotations

import hashlib
import secrets
import tempfile
from collections.abc import Callable
from pathlib import Path

from pydantic import Field, model_validator

from .approval import ApprovalTokenService
from .audit import InMemoryAuditSink
from .document_write import (
    DocxDocumentExecutor,
    DocxDocumentVerifier,
    document_content_sha256,
    document_target_identity,
)
from .execution_broker import ExecutionBroker
from .executor_router import CapabilityExecutorRouter
from .fault_injection import FaultInjectingExecutor, FaultPhase, FaultSpec
from .file_move import FileMovedPredicate, FileMoveExecutor, InMemoryMoveJournal, file_sha256
from .file_trash import FileAbsentPredicate, FileTrashExecutor
from .fixture_read import FixtureReadExecutor, FixtureReadVerifier, FixtureSourceCatalog
from .integrated_scenarios import IntegratedScenarioManifest, ScenarioEnvironment
from .memory import (
    MemorySensitivity,
    MemorySource,
    SemanticMemoryCandidate,
    SqliteMemoryRepository,
)
from .memory_pdf import MemoryPdfResolutionError, MemoryPdfResolver
from .memory_search import MemorySearchExecutor, MemorySearchVerifier
from .models import (
    Action,
    ActionStatus,
    FrozenModel,
    Plan,
    PolicyContext,
    PolicyDecision,
    Postcondition,
    VerificationResult,
)
from .orchestrator import ApprovalBroker, Orchestrator
from .policy import PolicyEngine
from .registry import default_registry
from .runtime_models import ApprovalArtifact, ExecutionResult
from .verifier import PredicateVerifier


class FixtureScenarioResult(FrozenModel):
    scenario_id: str = Field(pattern=r"^S(?:03|04|05|07)$")
    success: bool
    evidence_level: str = Field(pattern=r"^FIXTURE_SANDBOX$")
    verified_assertions: tuple[str, ...] = Field(max_length=8)
    external_effect_count: int = Field(ge=0, le=0)
    error_code: str | None = None

    @model_validator(mode="after")
    def consistent_error(self) -> FixtureScenarioResult:
        if self.success == (self.error_code is not None):
            raise ValueError("fixture scenario result is inconsistent")
        return self


class FixtureScenarioReport(FrozenModel):
    schema_version: str = "1.0"
    results: tuple[FixtureScenarioResult, ...] = Field(min_length=3, max_length=3)
    passed: int = Field(ge=0, le=3)
    all_passed: bool

    @model_validator(mode="after")
    def consistent_summary(self) -> FixtureScenarioReport:
        if tuple(item.scenario_id for item in self.results) != ("S04", "S05", "S07"):
            raise ValueError("fixture report must contain S04, S05, and S07 in order")
        actual = sum(item.success for item in self.results)
        if self.passed != actual or self.all_passed != (actual == 3):
            raise ValueError("fixture report summary is inconsistent")
        return self


class _YellowApproval(ApprovalBroker):
    def __init__(self) -> None:
        self.count = 0

    def approve_yellow(
        self,
        plan: Plan,
        action: Action,
        decision: PolicyDecision,
    ) -> bool:
        self.count += 1
        return True

    def approve_red(
        self,
        plan: Plan,
        action: Action,
        decision: PolicyDecision,
    ) -> ApprovalArtifact | None:
        return None


class _FixtureVerifierRouter:
    def __init__(
        self,
        source: FixtureReadVerifier,
        document: DocxDocumentVerifier,
    ) -> None:
        self._source = source
        self._document = document

    def verify(self, action: Action, result: ExecutionResult) -> VerificationResult:
        if action.capability_id == "browser.fixture.read":
            return self._source.verify(action, result)
        if action.capability_id == "document.write":
            return self._document.verify(action, result)
        return VerificationResult(
            success=False,
            reason="UNSUPPORTED_FIXTURE_CAPABILITY",
            recoverable=False,
        )


class FixtureIntegratedScenarioRunner:
    """Run supported scenarios inside fresh, automatically removed fixture roots."""

    def __init__(self, manifest: IntegratedScenarioManifest) -> None:
        by_id = {item.scenario_id: item for item in manifest.scenarios}
        for scenario_id in ("S03", "S04", "S05", "S07"):
            if by_id[scenario_id].environment is not ScenarioEnvironment.HOST_FIXTURE_SANDBOX:
                raise ValueError(f"{scenario_id} must remain HOST_FIXTURE_SANDBOX")

    def run(self) -> FixtureScenarioReport:
        results = (
            self._safe_run("S04", self._run_s04),
            self._safe_run("S05", self._run_s05),
            self._safe_run("S07", self._run_s07),
        )
        passed = sum(item.success for item in results)
        return FixtureScenarioReport(
            results=results,
            passed=passed,
            all_passed=passed == len(results),
        )

    def run_scenario(self, scenario_id: str) -> FixtureScenarioResult:
        runners = {
            "S04": self._run_s04,
            "S05": self._run_s05,
            "S07": self._run_s07,
        }
        runner = runners.get(scenario_id)
        if runner is None:
            raise ValueError("unsupported fixture scenario")
        return self._safe_run(scenario_id, runner)

    def run_s04_destination_exists(self) -> FixtureScenarioResult:
        return self._safe_run("S04", self._run_s04_destination_exists)

    def run_s03_file_missing(self) -> FixtureScenarioResult:
        return self._safe_run("S03", self._run_s03_file_missing)

    def run_s05_retryable_disconnect(self) -> FixtureScenarioResult:
        return self._safe_run(
            "S05",
            lambda: self._run_s05_impl(inject_disconnect=True),
        )

    def run_s07_user_interference(self) -> FixtureScenarioResult:
        return self._safe_run("S07", self._run_s07_user_interference)

    @classmethod
    def _safe_run(
        cls,
        scenario_id: str,
        runner: Callable[[], FixtureScenarioResult],
    ) -> FixtureScenarioResult:
        try:
            return runner()
        except Exception:
            return cls._result(scenario_id, False, f"{scenario_id}_FIXTURE_FAILED")

    @staticmethod
    def _run_s04() -> FixtureScenarioResult:
        with tempfile.TemporaryDirectory(prefix="javis-s04-") as temporary:
            root = Path(temporary).resolve()
            source = root / "downloads"
            destination = root / "organized"
            source.mkdir()
            destination.mkdir()
            first = source / "network.pdf"
            second = source / "database.pdf"
            unrelated = source / "keep.txt"
            first.write_bytes(b"network fixture")
            second.write_bytes(b"database fixture")
            unrelated.write_bytes(b"must remain")
            unrelated_hash = file_sha256(unrelated)
            first_destination = destination / first.name
            second_destination = destination / second.name
            actions = tuple(
                Action(
                    capability_id="file.move",
                    arguments={"source": str(item), "destination": str(target)},
                    purpose="S04 move exact fixture PDF",
                    target_identity=str(item.resolve()),
                    expected_postconditions=(Postcondition(predicate="file.moved"),),
                )
                for item, target in (
                    (first, first_destination),
                    (second, second_destination),
                )
            )
            registry = default_registry()
            approval = _YellowApproval()
            executor = FileMoveExecutor((root,), InMemoryMoveJournal())
            outcome = Orchestrator(
                policy=PolicyEngine(registry),
                executor=ExecutionBroker(registry, {"native": executor}),
                verifier=PredicateVerifier({"file.moved": FileMovedPredicate()}),
                audit=InMemoryAuditSink(),
                approvals=ApprovalTokenService(secrets.token_bytes(32)),
                approval_broker=approval,
            ).run(
                Plan(goal="Evaluate exact S04 fixture moves", steps=actions),
                FixtureIntegratedScenarioRunner._trusted_context,
            )
            success = (
                outcome.status is ActionStatus.SUCCEEDED
                and outcome.completed_actions == 2
                and approval.count == 2
                and not first.exists()
                and not second.exists()
                and file_sha256(first_destination)
                == hashlib.sha256(b"network fixture").hexdigest()
                and file_sha256(second_destination)
                == hashlib.sha256(b"database fixture").hexdigest()
                and file_sha256(unrelated) == unrelated_hash
            )
            return FixtureIntegratedScenarioRunner._result(
                "S04",
                success,
                None if success else "S04_FIXTURE_FAILED",
            )

    @staticmethod
    def _run_s03_file_missing() -> FixtureScenarioResult:
        with tempfile.TemporaryDirectory(prefix="javis-s03-missing-") as temporary:
            root = Path(temporary).resolve()
            missing_pdf = root / "remembered-missing.pdf"
            store = SqliteMemoryRepository((root / "memory.sqlite3").resolve())
            store.remember(
                SemanticMemoryCandidate(
                    key="study.remembered_missing_pdf",
                    value=str(missing_pdf),
                    source=MemorySource.VERIFIED_TASK,
                    confidence=1.0,
                    sensitivity=MemorySensitivity.LOW,
                )
            )
            search_action = Action(
                capability_id="memory.search",
                arguments={"query": "remembered missing PDF", "k": 3},
                purpose="find the exact remembered missing fixture PDF",
                target_identity="memory-store:s03-fixture",
                expected_postconditions=(
                    Postcondition(predicate="memory.search.matches"),
                ),
            )
            search_executor = MemorySearchExecutor(store, store_id="s03-fixture")
            search_result = search_executor.execute(search_action)
            search_verifier = MemorySearchVerifier(search_executor)
            if not search_verifier.verify(search_action, search_result).success:
                return FixtureIntegratedScenarioRunner._result(
                    "S03", False, "S03_MEMORY_SEARCH_FAILED"
                )
            resolver = MemoryPdfResolver(
                store,
                search_verifier,
                (root,),
                viewer_app_id="edge",
            )
            try:
                resolver.resolve(search_action, search_result)
            except MemoryPdfResolutionError:
                return FixtureScenarioResult(
                    scenario_id="S03",
                    success=False,
                    evidence_level="FIXTURE_SANDBOX",
                    verified_assertions=(),
                    external_effect_count=0,
                    error_code="S03_FILE_MISSING",
                )
            return FixtureIntegratedScenarioRunner._result(
                "S03", False, "S03_MISSING_FILE_ACCEPTED"
            )

    @staticmethod
    def _run_s04_destination_exists() -> FixtureScenarioResult:
        with tempfile.TemporaryDirectory(prefix="javis-s04-exists-") as temporary:
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
            source_hash = file_sha256(source)
            destination_hash = file_sha256(destination)
            unrelated_hash = file_sha256(unrelated)
            action = Action(
                capability_id="file.move",
                arguments={"source": str(source), "destination": str(destination)},
                purpose="S04 reject an occupied fixture destination",
                target_identity=str(source.resolve()),
                expected_postconditions=(Postcondition(predicate="file.moved"),),
            )
            registry = default_registry()
            outcome = Orchestrator(
                policy=PolicyEngine(registry),
                executor=ExecutionBroker(
                    registry,
                    {"native": FileMoveExecutor((root,), InMemoryMoveJournal())},
                ),
                verifier=PredicateVerifier({"file.moved": FileMovedPredicate()}),
                audit=InMemoryAuditSink(),
                approvals=ApprovalTokenService(secrets.token_bytes(32)),
                approval_broker=_YellowApproval(),
            ).run(
                Plan(goal="Evaluate S04 occupied destination", steps=(action,)),
                FixtureIntegratedScenarioRunner._trusted_context,
            )
            no_unexpected_move = (
                outcome.status is ActionStatus.ABORTED
                and source.is_file()
                and file_sha256(source) == source_hash
                and file_sha256(destination) == destination_hash
                and file_sha256(unrelated) == unrelated_hash
            )
            return FixtureScenarioResult(
                scenario_id="S04",
                success=False,
                evidence_level="FIXTURE_SANDBOX",
                verified_assertions=("no_unexpected_file_moved",)
                if no_unexpected_move
                else (),
                external_effect_count=0,
                error_code="S04_DESTINATION_EXISTS",
            )

    @staticmethod
    def _run_s05() -> FixtureScenarioResult:
        return FixtureIntegratedScenarioRunner._run_s05_impl(inject_disconnect=False)

    @staticmethod
    def _run_s05_impl(*, inject_disconnect: bool) -> FixtureScenarioResult:
        with tempfile.TemporaryDirectory(prefix="javis-s05-") as temporary:
            root = Path(temporary).resolve()
            (root / "source-a.html").write_text(
                "<article><h1>자료 A</h1><p>검증된 fixture 본문.</p></article>",
                encoding="utf-8",
            )
            (root / "source-b.txt").write_text("두 번째 고정 자료.", encoding="utf-8")
            catalog = FixtureSourceCatalog(
                root,
                {"s05-study": ("source-a.html", "source-b.txt")},
            )
            bundle = catalog.read("s05-study")
            output = root / "summary.docx"
            title = "S05 자료 요약"
            summary = "두 개의 고정 fixture 자료를 읽고 source-set hash에 결합한 요약입니다."
            content_digest = document_content_sha256(
                title,
                summary,
                bundle.source_set_digest,
            )
            source_action = Action(
                capability_id="browser.fixture.read",
                arguments={"fixture_id": "s05-study"},
                purpose="S05 read exact fixture source set",
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
                purpose="S05 write approved fixture summary",
                target_identity=document_target_identity(output),
                expected_postconditions=(
                    Postcondition(
                        predicate="document.content.sha256",
                        arguments={"sha256": content_digest},
                    ),
                ),
            )
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
            document_executor = DocxDocumentExecutor((root,))
            registry = default_registry()
            approval = _YellowApproval()
            outcome = Orchestrator(
                policy=PolicyEngine(registry),
                executor=ExecutionBroker(
                    registry,
                    {
                        "native": CapabilityExecutorRouter(
                            "native",
                            {
                                "browser.fixture.read": source_executor,
                                "document.write": document_executor,
                            },
                        )
                    },
                ),
                verifier=_FixtureVerifierRouter(
                    FixtureReadVerifier(catalog), DocxDocumentVerifier(document_executor)
                ),
                audit=InMemoryAuditSink(),
                approvals=ApprovalTokenService(secrets.token_bytes(32)),
                approval_broker=approval,
            ).run(
                Plan(
                    goal="Evaluate S05 entirely inside a disposable fixture sandbox",
                    steps=(source_action, document_action),
                ),
                FixtureIntegratedScenarioRunner._trusted_context,
            )
            success = (
                outcome.status is ActionStatus.SUCCEEDED
                and outcome.completed_actions == 2
                and approval.count == 1
                and output.is_file()
            )
            if inject_disconnect:
                success = (
                    success
                    and isinstance(source_executor, FaultInjectingExecutor)
                    and len(source_executor.observations) == 1
                    and source_executor.observations[0].retry_safe
                    and not source_executor.observations[0].delegate_called
                )
            return FixtureIntegratedScenarioRunner._result(
                "S05",
                success,
                None if success else "S05_FIXTURE_FAILED",
            )

    @staticmethod
    def _run_s07() -> FixtureScenarioResult:
        class SandboxTrash:
            def __init__(self, trash_root: Path) -> None:
                self._trash_root = trash_root
                self.trashed: list[Path] = []

            def trash(self, path: Path) -> None:
                destination = self._trash_root / path.name
                path.rename(destination)
                self.trashed.append(destination)

        with tempfile.TemporaryDirectory(prefix="javis-s07-") as temporary:
            root = Path(temporary).resolve()
            trash_root = root / ".trash"
            trash_root.mkdir()
            target = root / "delete-me.txt"
            unrelated = root / "keep-me.txt"
            target.write_bytes(b"trash fixture")
            unrelated.write_bytes(b"unrelated fixture")
            target_hash = file_sha256(target)
            unrelated_hash = file_sha256(unrelated)
            backend = SandboxTrash(trash_root)
            action = Action(
                capability_id="file.delete.trash",
                arguments={"path": str(target)},
                purpose="S07 move exact fixture file to recoverable sandbox trash",
                target_identity=str(target.resolve()),
                expected_postconditions=(Postcondition(predicate="file.absent"),),
            )
            registry = default_registry()
            approval = _YellowApproval()
            outcome = Orchestrator(
                policy=PolicyEngine(registry),
                executor=ExecutionBroker(
                    registry,
                    {"native": FileTrashExecutor((root,), backend)},
                ),
                verifier=PredicateVerifier({"file.absent": FileAbsentPredicate()}),
                audit=InMemoryAuditSink(),
                approvals=ApprovalTokenService(secrets.token_bytes(32)),
                approval_broker=approval,
            ).run(
                Plan(goal="Evaluate recoverable S07 fixture trash", steps=(action,)),
                FixtureIntegratedScenarioRunner._trusted_context,
            )
            success = (
                outcome.status is ActionStatus.SUCCEEDED
                and outcome.completed_actions == 1
                and approval.count == 1
                and not target.exists()
                and len(backend.trashed) == 1
                and file_sha256(backend.trashed[0]) == target_hash
                and file_sha256(unrelated) == unrelated_hash
            )
            return FixtureIntegratedScenarioRunner._result(
                "S07",
                success,
                None if success else "S07_FIXTURE_FAILED",
            )

    @staticmethod
    def _run_s07_user_interference() -> FixtureScenarioResult:
        with tempfile.TemporaryDirectory(prefix="javis-s07-interference-") as temporary:
            root = Path(temporary).resolve()
            trash_root = root / ".trash"
            trash_root.mkdir()
            target = root / "delete-me.txt"
            unrelated = root / "keep-me.txt"
            target.write_bytes(b"trash fixture")
            unrelated.write_bytes(b"unrelated fixture")

            class InterferingTrash:
                def trash(self, path: Path) -> None:
                    path.rename(trash_root / path.name)
                    unrelated.write_bytes(b"concurrent user change")

            action = Action(
                capability_id="file.delete.trash",
                arguments={"path": str(target)},
                purpose="S07 observe unrelated concurrent fixture interference",
                target_identity=str(target.resolve()),
                expected_postconditions=(Postcondition(predicate="file.absent"),),
            )
            registry = default_registry()
            outcome = Orchestrator(
                policy=PolicyEngine(registry),
                executor=ExecutionBroker(
                    registry,
                    {"native": FileTrashExecutor((root,), InterferingTrash())},
                ),
                verifier=PredicateVerifier({"file.absent": FileAbsentPredicate()}),
                audit=InMemoryAuditSink(),
                approvals=ApprovalTokenService(secrets.token_bytes(32)),
                approval_broker=_YellowApproval(),
            ).run(
                Plan(goal="Evaluate S07 concurrent interference", steps=(action,)),
                FixtureIntegratedScenarioRunner._trusted_context,
            )
            verifier_positive = (
                outcome.status is ActionStatus.SUCCEEDED and not target.exists()
            )
            return FixtureScenarioResult(
                scenario_id="S07",
                success=False,
                evidence_level="FIXTURE_SANDBOX",
                verified_assertions=("exact_file_absent",)
                if verifier_positive
                else (),
                external_effect_count=0,
                error_code="S07_USER_INTERFERENCE",
            )

    @staticmethod
    def _trusted_context(_action: Action) -> PolicyContext:
        return PolicyContext(
            addressed_confidence=1.0,
            intent_confidence=1.0,
            target_confidence=1.0,
            target_matches_plan=True,
        )

    @staticmethod
    def _result(
        scenario_id: str,
        success: bool,
        error_code: str | None,
    ) -> FixtureScenarioResult:
        assertions = {
            "S04": ("all_expected_files_moved_once", "no_unexpected_file_moved"),
            "S05": ("source_set_matches", "document_content_hash_matches"),
            "S07": ("exact_file_absent", "unrelated_files_unchanged"),
        }
        return FixtureScenarioResult(
            scenario_id=scenario_id,
            success=success,
            evidence_level="FIXTURE_SANDBOX",
            verified_assertions=assertions[scenario_id] if success else (),
            external_effect_count=0,
            error_code=error_code,
        )
