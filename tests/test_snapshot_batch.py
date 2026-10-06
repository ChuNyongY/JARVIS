import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from javis_core.cli import main
from javis_core.snapshot_batch import (
    SNAPSHOT_SCENARIO_IDS,
    HmacSha256SnapshotVerifier,
    SnapshotEvidenceBatchEvaluator,
    load_snapshot_hmac_key,
)
from javis_core.snapshot_vm import (
    SnapshotEvidenceError,
    SnapshotEvidenceGate,
    SnapshotScenarioManifest,
    SnapshotVmEvidence,
    canonical_signed_payload,
)

NOW = datetime(2026, 9, 30, 1, 0, tzinfo=UTC)
KEY = b"k" * 32
KEY_ID = "vm-harness-1"


def manifests() -> tuple[SnapshotScenarioManifest, ...]:
    return tuple(
        SnapshotScenarioManifest(
            scenario_id=scenario_id,
            vm_provider="test-provider",
            base_snapshot_id="sha256:base",
            guest_machine_id="guest-01",
            capability_id=f"capability.{scenario_id.lower()}",
            expected_outcome=f"{scenario_id}_EXPECTED",
        )
        for scenario_id in SNAPSHOT_SCENARIO_IDS
    )


def evidence(
    manifest: SnapshotScenarioManifest,
    run_id: str,
    *,
    at: datetime = NOW,
) -> SnapshotVmEvidence:
    unsigned = SnapshotVmEvidence(
        **manifest.model_dump(),
        run_id=run_id,
        started_at=at - timedelta(minutes=2),
        finished_at=at - timedelta(minutes=1),
        observed_outcome=manifest.expected_outcome,
        restore_attempted=True,
        restore_succeeded=True,
        artifact_sha256="a" * 64,
        signature_key_id=KEY_ID,
        signature="x" * 16,
    )
    signature = base64.urlsafe_b64encode(
        hmac.digest(KEY, canonical_signed_payload(unsigned), hashlib.sha256)
    ).decode("ascii")
    return unsigned.model_copy(update={"signature": signature})


def write_evidence(tmp_path: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for index, manifest in enumerate(manifests(), start=1):
        path = tmp_path / f"{manifest.scenario_id}.json"
        path.write_text(
            evidence(manifest, f"run-{index}").model_dump_json(),
            encoding="utf-8",
        )
        paths[manifest.scenario_id] = path
    return paths


def evaluator() -> SnapshotEvidenceBatchEvaluator:
    return SnapshotEvidenceBatchEvaluator(
        manifests(),
        SnapshotEvidenceGate(HmacSha256SnapshotVerifier(KEY_ID, KEY)),
    )


def test_batch_accepts_exact_signed_restored_scenario_set(tmp_path: Path) -> None:
    report = evaluator().evaluate_paths(write_evidence(tmp_path), now=NOW)

    assert report.all_accepted
    assert report.accepted == 4
    assert report.rejected == 0
    assert tuple(item.scenario_id for item in report.results) == SNAPSHOT_SCENARIO_IDS
    assert "signature" not in report.model_dump_json()


def test_batch_rejects_missing_extra_or_misordered_inputs(tmp_path: Path) -> None:
    paths = write_evidence(tmp_path)
    paths.pop("S12")
    with pytest.raises(SnapshotEvidenceError, match="exactly once"):
        evaluator().evaluate_paths(paths, now=NOW)

    extra = write_evidence(tmp_path)
    extra["S11"] = extra["S08"]
    with pytest.raises(SnapshotEvidenceError, match="exactly once"):
        evaluator().evaluate_paths(extra, now=NOW)

    with pytest.raises(SnapshotEvidenceError, match="ordered"):
        SnapshotEvidenceBatchEvaluator(
            tuple(reversed(manifests())),
            SnapshotEvidenceGate(HmacSha256SnapshotVerifier(KEY_ID, KEY)),
        )


def test_batch_rejects_duplicate_run_ids(tmp_path: Path) -> None:
    paths = write_evidence(tmp_path)
    duplicate = evidence(manifests()[1], "run-1")
    paths["S09"].write_text(duplicate.model_dump_json(), encoding="utf-8")

    with pytest.raises(SnapshotEvidenceError, match="run_id"):
        evaluator().evaluate_paths(paths, now=NOW)


def test_batch_reports_invalid_signature_without_leaking_it(tmp_path: Path) -> None:
    paths = write_evidence(tmp_path)
    document = json.loads(paths["S10"].read_text(encoding="utf-8"))
    document["signature"] = "invalid-signature"
    paths["S10"].write_text(json.dumps(document), encoding="utf-8")

    report = evaluator().evaluate_paths(paths, now=NOW)

    assert not report.all_accepted
    assert report.accepted == 3
    assert report.results[2].reason_codes == ("SIGNATURE_INVALID",)
    assert "invalid-signature" not in report.model_dump_json()


def test_hmac_key_loader_rejects_relative_symlink_and_bad_lengths(tmp_path: Path) -> None:
    valid = (tmp_path / "key.bin").resolve()
    valid.write_bytes(KEY)
    assert load_snapshot_hmac_key(valid) == KEY
    with pytest.raises(SnapshotEvidenceError, match="invalid"):
        load_snapshot_hmac_key(Path("relative-key.bin"))

    short = (tmp_path / "short.bin").resolve()
    short.write_bytes(b"short")
    with pytest.raises(SnapshotEvidenceError, match="length"):
        load_snapshot_hmac_key(short)

    link = (tmp_path / "link.bin").resolve()
    try:
        link.symlink_to(valid)
    except OSError:
        return
    with pytest.raises(SnapshotEvidenceError, match="invalid"):
        load_snapshot_hmac_key(link)


def test_snapshot_batch_cli_returns_only_verified_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest_path = (tmp_path / "manifests.json").resolve()
    manifest_path.write_text(
        json.dumps([item.model_dump(mode="json") for item in manifests()]),
        encoding="utf-8",
    )
    evidence_directory = (tmp_path / "evidence").resolve()
    evidence_directory.mkdir()
    at = datetime.now(UTC)
    for index, manifest in enumerate(manifests(), start=1):
        (evidence_directory / f"{manifest.scenario_id}.json").write_text(
            evidence(manifest, f"cli-run-{index}", at=at).model_dump_json(),
            encoding="utf-8",
        )
    key_file = (tmp_path / "key.bin").resolve()
    key_file.write_bytes(KEY)

    assert main(
        [
            "verify-snapshot-batch",
            str(manifest_path),
            str(evidence_directory),
            str(key_file),
            KEY_ID,
        ]
    ) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["all_accepted"] is True
    assert "signature" not in json.dumps(output)
    assert str(evidence_directory) not in json.dumps(output)
