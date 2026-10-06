from pathlib import Path

from javis_core import AuditEvent, JsonlAuditSink, WindowsUserOnlyAcl


def test_acl_hardener_applies_and_verifies_exact_dacl(tmp_path: Path) -> None:
    path = tmp_path / "sensitive.jsonl"
    path.write_text("fixture", encoding="utf-8")
    hardener = WindowsUserOnlyAcl()

    hardener.harden(path)

    assert hardener.verify(path)


def test_audit_sink_hardens_log_and_lock_files(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    JsonlAuditSink(path).append(
        AuditEvent(
            task_id="00000000-0000-0000-0000-000000000001",
            event_type="TASK_ABORTED",
            payload={"reason": "fixture"},
        )
    )
    hardener = WindowsUserOnlyAcl()

    assert hardener.verify(path)
    assert hardener.verify(path.with_name("audit.jsonl.lock"))
