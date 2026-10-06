from __future__ import annotations

import ctypes
import json
import secrets
import tempfile
import time
from pathlib import Path

from javis_core import (
    Action,
    ActionStatus,
    AppRegistryLoader,
    ApprovalTokenService,
    ExecutionBroker,
    ExecutionResult,
    InMemoryAuditSink,
    MemoryPdfResolver,
    MemorySearchExecutor,
    MemorySearchVerifier,
    MemorySensitivity,
    MemorySource,
    Orchestrator,
    PdfOpenedVerifier,
    Plan,
    PolicyContext,
    PolicyEngine,
    Postcondition,
    RegisteredApp,
    RegisteredPdfOpenExecutor,
    RetryBudget,
    SemanticMemoryCandidate,
    SqliteMemoryRepository,
    WindowsRuntimeWindowResolver,
    default_registry,
    validate_plan_contracts,
)
from javis_core.cancellation import CancellationToken
from javis_core.executor import Executor
from javis_core.verifier import Verifier


class RecordingExecutor:
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


def _run_one(action: Action, executor: RecordingExecutor, verifier: Verifier) -> bool:
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
    ).run(
        plan,
        lambda _action: PolicyContext(
            addressed_confidence=1.0,
            intent_confidence=1.0,
            target_confidence=1.0,
            target_matches_plan=True,
        ),
    )
    return outcome.status is ActionStatus.SUCCEEDED


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


def _close_exact_window(
    resolver: WindowsRuntimeWindowResolver,
    app: RegisteredApp,
    pid: object,
    hwnd: object,
) -> bool:
    if type(pid) is not int or type(hwnd) is not int:
        return False
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


def _close_one_new_window(
    resolver: WindowsRuntimeWindowResolver,
    app: RegisteredApp,
    baseline: frozenset[tuple[int, int]],
) -> bool:
    candidates = resolver.matching_windows(app) - baseline
    if len(candidates) != 1:
        return False
    pid, hwnd = next(iter(candidates))
    return _close_exact_window(resolver, app, pid, hwnd)


def main() -> int:
    search_verified = False
    pdf_verified = False
    window_closed = False
    error_code: str | None = "S03_MEMORY_PDF_SMOKE_FAILED"
    open_recording: RecordingExecutor | None = None
    apps: dict[str, RegisteredApp] | None = None
    cleanup_resolver: WindowsRuntimeWindowResolver | None = None
    baseline_windows: frozenset[tuple[int, int]] = frozenset()
    try:
        apps = AppRegistryLoader(require_publisher=True).load(
            Path("config/apps.json").resolve(strict=True)
        )
        with tempfile.TemporaryDirectory(prefix="javis-s03-", ignore_cleanup_errors=True) as raw:
            root = Path(raw).resolve(strict=True)
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
            search_recording = RecordingExecutor(search_executor)
            search_verified = _run_one(
                search_action,
                search_recording,
                MemorySearchVerifier(search_executor),
            )
            if not search_verified or search_recording.result is None:
                raise RuntimeError("memory search pipeline failed")
            open_action = MemoryPdfResolver(
                store,
                MemorySearchVerifier(search_executor),
                (root,),
                viewer_app_id="edge",
            ).resolve(search_action, search_recording.result)
            open_executor = RegisteredPdfOpenExecutor(
                {"edge": apps["edge"]},
                (root,),
            )
            cleanup_resolver = WindowsRuntimeWindowResolver()
            baseline_windows = cleanup_resolver.snapshot(apps["edge"])
            open_recording = RecordingExecutor(open_executor)
            pdf_verified = _run_one(
                open_action,
                open_recording,
                PdfOpenedVerifier(open_executor),
            )
            error_code = (
                None
                if pdf_verified
                else (
                    open_recording.result.error_code
                    if open_recording.result is not None
                    and open_recording.result.error_code is not None
                    else "S03_PDF_PIPELINE_FAILED"
                )
            )
            if open_recording.result is not None:
                observed = open_recording.result.observed
                window_closed = _close_exact_window(
                    WindowsRuntimeWindowResolver(),
                    apps["edge"],
                    observed.get("pid"),
                    observed.get("hwnd"),
                )
    except Exception:
        error_code = "S03_MEMORY_PDF_SMOKE_FAILED"
    finally:
        if (
            apps is not None
            and open_recording is not None
            and open_recording.result is not None
            and not window_closed
            and open_recording.result.observed.get("hwnd") is not None
        ):
            try:
                observed = open_recording.result.observed
                window_closed = _close_exact_window(
                    WindowsRuntimeWindowResolver(),
                    apps["edge"],
                    observed.get("pid"),
                    observed.get("hwnd"),
                )
            except Exception:
                window_closed = False
            if not window_closed:
                error_code = "S03_WINDOW_CLEANUP_FAILED"
        elif apps is not None and cleanup_resolver is not None and not window_closed:
            try:
                window_closed = _close_one_new_window(
                    cleanup_resolver,
                    apps["edge"],
                    baseline_windows,
                )
            except Exception:
                window_closed = False

    success = search_verified and pdf_verified and window_closed
    print(
        json.dumps(
            {
                "scenario_id": "S03",
                "success": success,
                "evidence_level": "ACTUAL_REVERSIBLE_FIXTURE",
                "memory_search_verified": search_verified,
                "pdf_identity_verified": pdf_verified,
                "viewer_process_verified": pdf_verified,
                "new_window_identity_verified": pdf_verified,
                "new_window_closed": window_closed,
                "memory_value_or_path_emitted": False,
                "window_content_emitted": False,
                "error_code": None if success else error_code,
            },
            separators=(",", ":"),
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
