import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from javis_core.snapshot_vm import (
    SnapshotEvidenceError,
    SnapshotEvidenceGate,
    SnapshotScenarioManifest,
    SnapshotVmEvidence,
    canonical_signed_payload,
    load_snapshot_evidence,
    load_snapshot_manifests,
)


class DigestVerifier:
    def verify(self, key_id: str, payload: bytes, signature: str) -> bool:
        return key_id == "harness-key-1" and signature == payload.hex()


class FailingVerifier:
    def verify(self, key_id: str, payload: bytes, signature: str) -> bool:
        raise RuntimeError("provider failure")


NOW = datetime(2026, 9, 21, 4, 0, tzinfo=UTC)


def manifest() -> SnapshotScenarioManifest:
    return SnapshotScenarioManifest(
        scenario_id="permanent-delete-001",
        vm_provider="hyper-v",
        base_snapshot_id="sha256:immutable-base",
        guest_machine_id="win11-lab-01",
        capability_id="file.delete.permanent",
        expected_outcome="FILE_ABSENT_AFTER_RESTORE_TEST",
    )


def evidence(**updates: object) -> SnapshotVmEvidence:
    values: dict[str, object] = {
        **manifest().model_dump(),
        "run_id": "run-001",
        "started_at": NOW - timedelta(minutes=3),
        "finished_at": NOW - timedelta(minutes=1),
        "observed_outcome": "FILE_ABSENT_AFTER_RESTORE_TEST",
        "restore_attempted": True,
        "restore_succeeded": True,
        "artifact_sha256": "a" * 64,
        "signature_key_id": "harness-key-1",
        "signature": "x" * 16,
    }
    values.update(updates)
    unsigned = SnapshotVmEvidence.model_validate(values)
    return unsigned.model_copy(update={"signature": canonical_signed_payload(unsigned).hex()})


def test_gate_accepts_exact_fresh_signed_restored_evidence() -> None:
    report = SnapshotEvidenceGate(DigestVerifier()).validate(manifest(), evidence(), now=NOW)

    assert report.accepted
    assert report.reason_codes == ()
    assert json.loads(report.canonical_json())["accepted"] is True
    assert "signature" not in report.canonical_json()


def test_gate_rejects_binding_outcome_restore_time_and_signature_failures() -> None:
    item = evidence(
        base_snapshot_id="different",
        observed_outcome="UNEXPECTED",
        restore_succeeded=False,
        started_at=NOW - timedelta(hours=2),
        finished_at=NOW - timedelta(hours=1, minutes=29),
    ).model_copy(update={"signature": "invalid-signature"})

    report = SnapshotEvidenceGate(DigestVerifier()).validate(manifest(), item, now=NOW)

    assert not report.accepted
    assert report.reason_codes == (
        "MANIFEST_MISMATCH",
        "RUN_DURATION_EXCEEDED",
        "STALE_EVIDENCE",
        "RESTORE_NOT_CONFIRMED",
        "OUTCOME_MISMATCH",
        "SIGNATURE_INVALID",
    )


def test_verifier_exception_fails_closed() -> None:
    report = SnapshotEvidenceGate(FailingVerifier()).validate(manifest(), evidence(), now=NOW)

    assert report.reason_codes == ("SIGNATURE_INVALID",)
    assert not report.accepted


def test_future_finished_time_is_rejected() -> None:
    item = evidence(
        started_at=NOW,
        finished_at=NOW + timedelta(minutes=3),
    )

    report = SnapshotEvidenceGate(DigestVerifier()).validate(manifest(), item, now=NOW)

    assert "FUTURE_EVIDENCE" in report.reason_codes


def test_naive_or_non_utc_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="explicitly use UTC"):
        evidence(started_at=datetime(2026, 9, 21, 3, 0))
    with pytest.raises(ValueError, match="explicitly use UTC"):
        evidence(started_at="2026-09-21T12:00:00+09:00")


def test_loader_rejects_duplicate_keys_and_oversize(tmp_path: Path) -> None:
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":"1.0","schema_version":"1.0"}', encoding="utf-8")
    with pytest.raises(SnapshotEvidenceError, match="duplicate"):
        load_snapshot_evidence(duplicate)

    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{}" + b" " * 10)
    with pytest.raises(SnapshotEvidenceError, match="size"):
        load_snapshot_evidence(oversized, max_bytes=4)


def test_manifest_loader_requires_unique_ids(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    item = manifest().model_dump(mode="json")
    path.write_text(json.dumps([item, item]), encoding="utf-8")

    with pytest.raises(SnapshotEvidenceError, match="unique"):
        load_snapshot_manifests(path)


def test_evidence_loader_rejects_symlink_when_supported(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    with pytest.raises(SnapshotEvidenceError, match="non-symlink"):
        load_snapshot_evidence(link)
