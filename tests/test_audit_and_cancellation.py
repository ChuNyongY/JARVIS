import json
from pathlib import Path
from uuid import uuid4

import pytest

from javis_core import (
    Action,
    ActionStatus,
    ApprovalTokenService,
    AuditEvent,
    AuditIntegrityError,
    AuditRetentionPolicy,
    CancellationToken,
    InMemoryAuditSink,
    JsonlAuditSink,
    MockExecutor,
    Orchestrator,
    Plan,
    PolicyContext,
    PolicyEngine,
    PredicateVerifier,
    default_registry,
)


def context() -> PolicyContext:
    return PolicyContext(
        addressed_confidence=1.0,
        intent_confidence=1.0,
        target_confidence=1.0,
        target_matches_plan=True,
    )


def test_jsonl_audit_redacts_nested_secrets(tmp_path: Path) -> None:
    path = tmp_path / "audit" / "events.jsonl"
    event = AuditEvent(
        task_id=Plan(
            goal="audit",
            steps=(Action(capability_id="file.list", purpose="audit"),),
        ).task_id,
        event_type="POLICY_DECISION",
        payload={
            "token": "must-not-appear",
            "nested": {"api_key": "also-secret", "safe": "visible"},
        },
    )

    JsonlAuditSink(path).append(event)
    raw = path.read_text(encoding="utf-8")
    parsed = json.loads(raw)

    assert "must-not-appear" not in raw
    assert "also-secret" not in raw
    assert parsed["payload"]["token"] == "[REDACTED]"
    assert parsed["payload"]["nested"]["safe"] == "visible"
    assert len(parsed["audit_previous_hash"]) == 64
    assert len(parsed["audit_record_hash"]) == 64


def test_jsonl_audit_rotates_with_bounded_retention(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    sink = JsonlAuditSink(
        path,
        AuditRetentionPolicy(max_file_bytes=1_024, backup_count=2),
    )
    for index in range(30):
        sink.append(
            AuditEvent(
                task_id=uuid4(),
                event_type="TASK_ABORTED",
                payload={"reason": "x" * 100, "index": index},
            )
        )

    assert path.exists()
    assert path.with_name("audit.jsonl.1").exists()
    assert path.with_name("audit.jsonl.2").exists()
    assert not path.with_name("audit.jsonl.3").exists()
    assert path.stat().st_size <= 1_024
    report = sink.verify_integrity()
    assert report.records > 0
    assert report.files == 3
    assert report.tip_sha256 is not None


def test_jsonl_audit_hash_chain_detects_tampering(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    sink = JsonlAuditSink(path)
    for index in range(3):
        sink.append(
            AuditEvent(
                task_id=uuid4(),
                event_type="TASK_ABORTED",
                payload={"reason": "SAFE_CODE", "index": index},
            )
        )

    report = sink.verify_integrity()
    assert report.records == 3
    assert report.files == 1
    lines = path.read_text(encoding="utf-8").splitlines()
    tampered = json.loads(lines[1])
    tampered["payload"]["reason"] = "CHANGED"
    lines[1] = json.dumps(tampered, separators=(",", ":"), ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(AuditIntegrityError, match="hash mismatch"):
        sink.verify_integrity()


def test_in_memory_audit_has_bounded_retention() -> None:
    sink = InMemoryAuditSink(max_events=2)
    for index in range(3):
        sink.append(
            AuditEvent(
                task_id=uuid4(),
                event_type="TASK_ABORTED",
                payload={"index": index},
            )
        )

    assert [event.payload["index"] for event in sink.events] == [1, 2]


def test_pre_cancelled_task_never_executes() -> None:
    cancellation = CancellationToken()
    cancellation.cancel("USER_STOP")
    action = Action(capability_id="system.info.read", purpose="cancel test")
    plan = Plan(goal="cancel", steps=(action,))
    executor = MockExecutor({"system.info.read": lambda _action: {"ran": True}})
    orchestrator = Orchestrator(
        policy=PolicyEngine(default_registry()),
        executor=executor,
        verifier=PredicateVerifier(),
        audit=InMemoryAuditSink(),
        approvals=ApprovalTokenService(b"d" * 32),
        cancellation=cancellation,
    )

    outcome = orchestrator.run(plan, lambda _action: context())

    assert outcome.status is ActionStatus.ABORTED
    assert outcome.reason == "USER_STOP"
    assert executor.calls == ()


def test_stop_during_mock_execution_aborts_before_verification() -> None:
    cancellation = CancellationToken()

    def stop_handler(_action: Action) -> dict[str, object]:
        cancellation.cancel()
        return {"ran": True}

    action = Action(capability_id="system.info.read", purpose="cancel during execution")
    plan = Plan(goal="cancel", steps=(action,))
    audit = InMemoryAuditSink()
    executor = MockExecutor({"system.info.read": stop_handler})  # type: ignore[arg-type]
    orchestrator = Orchestrator(
        policy=PolicyEngine(default_registry()),
        executor=executor,
        verifier=PredicateVerifier(),
        audit=audit,
        approvals=ApprovalTokenService(b"e" * 32),
        cancellation=cancellation,
    )

    outcome = orchestrator.run(plan, lambda _action: context())

    assert outcome.status is ActionStatus.ABORTED
    assert outcome.reason == "GLOBAL_STOP"
    assert len(executor.calls) == 1
    assert audit.of_type("VERIFICATION_FINISHED") == ()
