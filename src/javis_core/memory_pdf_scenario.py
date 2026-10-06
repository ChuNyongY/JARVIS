from __future__ import annotations

import ctypes
import secrets
import tempfile
import time
from pathlib import Path

from pydantic import Field, model_validator

from .app_registry import AppRegistryLoader
from .approval import ApprovalTokenService
from .audit import InMemoryAuditSink
from .cancellation import CancellationToken
from .contracts import validate_plan_contracts
from .execution_broker import ExecutionBroker
from .executor import Executor
from .memory import (
    MemorySensitivity,
    MemorySource,
    SemanticMemoryCandidate,
    SqliteMemoryRepository,
)
from .memory_pdf import MemoryPdfResolver
from .memory_search import MemorySearchExecutor, MemorySearchVerifier
from .models import Action, ActionStatus, FrozenModel, Plan, PolicyContext, Postcondition
from .orchestrator import Orchestrator
from .pdf_open import PdfOpenedVerifier, RegisteredPdfOpenExecutor
from .policy import PolicyEngine
from .process_launch import RegisteredApp
from .registry import default_registry
from .runtime_models import ExecutionResult, RetryBudget
from .verifier import Verifier
from .window_handoff import WindowsRuntimeWindowResolver


class MemoryPdfScenarioResult(FrozenModel):
    scenario_id: str = Field(default="S03", pattern=r"^S03$")
    success: bool
    evidence_level: str = Field(
        default="ACTUAL_REVERSIBLE_FIXTURE",
        pattern=r"^ACTUAL_REVERSIBLE_FIXTURE$",
    )
    verified_assertions: tuple[str, ...] = Field(max_length=4)
    error_code: str | None = Field(default=None, pattern=r"^[A-Z0-9_]{2,64}$")

    @model_validator(mode="after")
    def consistent_error(self) -> MemoryPdfScenarioResult:
        if self.success == (self.error_code is not None):
            raise ValueError("memory PDF scenario result is inconsistent")
        return self


class _RecordingExecutor:
    def __init__(self, executor: Executor) -> None:
        self._executor = executor
        self.result: ExecutionResult | None = None

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        self.result = self._executor.execute(action, cancellation)
        return self.result


class S03MemoryPdfScenarioRunner:
    """Run the exact S03 nominal path and close only its verified new Edge window."""

    def __init__(self, project_root: Path) -> None:
        if not project_root.is_absolute() or project_root.is_symlink():
            raise ValueError("project root must be an absolute non-symlink directory")
        self._project_root = project_root.resolve(strict=True)
        if not self._project_root.is_dir():
            raise ValueError("project root must be a directory")

    def run(self) -> MemoryPdfScenarioResult:
        search_verified = False
        pdf_verified = False
        window_closed = False
        error_code = "S03_MEMORY_PDF_FAILED"
        app: RegisteredApp | None = None
        cleanup_resolver: WindowsRuntimeWindowResolver | None = None
        baseline: frozenset[tuple[int, int]] = frozenset()
        open_result: ExecutionResult | None = None
        try:
            apps = AppRegistryLoader(require_publisher=True).load(
                (self._project_root / "config" / "apps.json").resolve(strict=True)
            )
            app = apps.get("edge")
            if app is None:
                raise RuntimeError("registered PDF viewer is unavailable")
            with tempfile.TemporaryDirectory(
                prefix="javis-s03-benchmark-", ignore_cleanup_errors=True
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
                search_action = Action(
                    capability_id="memory.search",
                    arguments={"query": "certification study PDF", "k": 3},
                    purpose="find the unique remembered fixture PDF",
                    target_identity="memory-store:s03-fixture",
                    expected_postconditions=(
                        Postcondition(predicate="memory.search.matches"),
                    ),
                )
                search_executor = MemorySearchExecutor(store, store_id="s03-fixture")
                search_recording = _RecordingExecutor(search_executor)
                search_verifier = MemorySearchVerifier(search_executor)
                search_verified = _run_one(
                    search_action,
                    search_recording,
                    search_verifier,
                )
                if not search_verified or search_recording.result is None:
                    raise RuntimeError("memory search did not verify")
                open_action = MemoryPdfResolver(
                    store,
                    search_verifier,
                    (root,),
                    viewer_app_id="edge",
                ).resolve(search_action, search_recording.result)
                cleanup_resolver = WindowsRuntimeWindowResolver()
                baseline = cleanup_resolver.snapshot(app)
                open_executor = RegisteredPdfOpenExecutor(
                    {"edge": app},
                    (root,),
                    window_resolver=WindowsRuntimeWindowResolver(),
                )
                open_recording = _RecordingExecutor(open_executor)
                pdf_verified = _run_one(
                    open_action,
                    open_recording,
                    PdfOpenedVerifier(
                        open_executor,
                        window_resolver=WindowsRuntimeWindowResolver(),
                    ),
                )
                open_result = open_recording.result
                if not pdf_verified or open_result is None:
                    error_code = (
                        open_result.error_code
                        if open_result is not None and open_result.error_code is not None
                        else "S03_PDF_PIPELINE_FAILED"
                    )
                else:
                    window_closed = _close_observed_window(app, open_result)
                    error_code = (
                        "S03_WINDOW_CLEANUP_FAILED" if not window_closed else error_code
                    )
        except Exception:
            error_code = "S03_MEMORY_PDF_FAILED"
        finally:
            if app is not None and not window_closed:
                try:
                    if open_result is not None:
                        window_closed = _close_observed_window(app, open_result)
                    if not window_closed and cleanup_resolver is not None:
                        candidates = cleanup_resolver.matching_windows(app) - baseline
                        if len(candidates) == 1:
                            pid, hwnd = next(iter(candidates))
                            window_closed = _close_exact_window(app, pid, hwnd)
                except Exception:
                    window_closed = False
                if pdf_verified and not window_closed:
                    error_code = "S03_WINDOW_CLEANUP_FAILED"

        success = search_verified and pdf_verified and window_closed
        return MemoryPdfScenarioResult(
            success=success,
            verified_assertions=(
                ("expected_pdf_identity_matches", "viewer_process_exists")
                if pdf_verified
                else ()
            ),
            error_code=None if success else error_code,
        )


def _run_one(action: Action, executor: _RecordingExecutor, verifier: Verifier) -> bool:
    registry = default_registry()
    plan = Plan(goal=f"Verify {action.capability_id} for S03", steps=(action,))
    validate_plan_contracts(plan, registry)
    outcome = Orchestrator(
        policy=PolicyEngine(registry),
        executor=ExecutionBroker(registry, {"native": executor}),
        verifier=verifier,
        audit=InMemoryAuditSink(),
        approvals=ApprovalTokenService(secrets.token_bytes(32)),
        retry_budget=RetryBudget(max_attempts=1, max_replans=0),
    ).run(plan, _trusted_context)
    return outcome.status is ActionStatus.SUCCEEDED


def _trusted_context(_action: Action) -> PolicyContext:
    return PolicyContext(
        addressed_confidence=1.0,
        intent_confidence=1.0,
        target_confidence=1.0,
        target_matches_plan=True,
    )


def _close_observed_window(app: RegisteredApp, result: ExecutionResult) -> bool:
    pid = result.observed.get("pid")
    hwnd = result.observed.get("hwnd")
    if type(pid) is not int or type(hwnd) is not int:
        return False
    return _close_exact_window(app, pid, hwnd)


def _close_exact_window(app: RegisteredApp, pid: int, hwnd: int) -> bool:
    resolver = WindowsRuntimeWindowResolver()
    if (pid, hwnd) not in resolver.matching_windows(app):
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.IsWindow.argtypes = [ctypes.c_void_p]
    user32.IsWindow.restype = ctypes.c_bool
    user32.PostMessageW.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
    ]
    user32.PostMessageW.restype = ctypes.c_bool
    if not user32.IsWindow(hwnd) or not user32.PostMessageW(hwnd, 0x0010, 0, 0):
        return False
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if (pid, hwnd) not in resolver.matching_windows(app):
            return True
        time.sleep(0.05)
    return False


def _write_minimal_pdf(path: Path) -> None:
    stream = b"BT /F1 18 Tf 72 720 Td (JAVIS S03 fixture) Tj ET\n"
    objects = (
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n"
        + stream
        + b"endstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    )
    content = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(content))
        content.extend(f"{number} 0 obj\n".encode("ascii"))
        content.extend(body)
        content.extend(b"\nendobj\n")
    xref = len(content)
    content.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    content.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        content.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    content.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    path.write_bytes(content)
