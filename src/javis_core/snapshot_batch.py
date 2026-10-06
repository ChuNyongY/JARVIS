from __future__ import annotations

import base64
import hashlib
import hmac
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from pydantic import Field, model_validator

from .models import FrozenModel
from .snapshot_vm import (
    SnapshotEvidenceError,
    SnapshotEvidenceGate,
    SnapshotScenarioManifest,
    load_snapshot_evidence,
)

SNAPSHOT_SCENARIO_IDS = ("S08", "S09", "S10", "S12")


class SnapshotBatchResult(FrozenModel):
    scenario_id: str = Field(pattern=r"^S(?:08|09|10|12)$")
    accepted: bool
    reason_codes: tuple[str, ...]
    signed_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class SnapshotBatchReport(FrozenModel):
    schema_version: str = "1.0"
    results: tuple[SnapshotBatchResult, ...] = Field(min_length=4, max_length=4)
    accepted: int = Field(ge=0, le=4)
    rejected: int = Field(ge=0, le=4)
    all_accepted: bool

    @model_validator(mode="after")
    def validate_summary(self) -> SnapshotBatchReport:
        if tuple(item.scenario_id for item in self.results) != SNAPSHOT_SCENARIO_IDS:
            raise ValueError("snapshot batch must contain ordered S08/S09/S10/S12 results")
        accepted = sum(item.accepted for item in self.results)
        if self.accepted != accepted or self.rejected != 4 - accepted:
            raise ValueError("snapshot batch counts are inconsistent")
        if self.all_accepted != (accepted == 4):
            raise ValueError("snapshot batch completion flag is inconsistent")
        return self


class HmacSha256SnapshotVerifier:
    """Verify URL-safe base64 HMAC-SHA256 evidence signatures for one exact key ID."""

    def __init__(self, key_id: str, key: bytes) -> None:
        if (
            not key_id
            or len(key_id) > 128
            or re.fullmatch(r"[A-Za-z0-9._-]{1,128}", key_id) is None
            or not 32 <= len(key) <= 4096
        ):
            raise ValueError("invalid snapshot HMAC verifier configuration")
        self._key_id = key_id
        self._key = bytes(key)

    def verify(self, key_id: str, payload: bytes, signature: str) -> bool:
        if key_id != self._key_id:
            return False
        expected = base64.urlsafe_b64encode(
            hmac.digest(self._key, payload, hashlib.sha256)
        ).decode("ascii")
        return hmac.compare_digest(expected, signature)


def load_snapshot_hmac_key(path: Path, *, max_bytes: int = 4096) -> bytes:
    if (
        max_bytes < 32
        or not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
    ):
        raise SnapshotEvidenceError("invalid snapshot HMAC key file")
    try:
        if path.stat().st_size > max_bytes:
            raise SnapshotEvidenceError("snapshot HMAC key exceeded size limit")
        key = path.read_bytes()
    except SnapshotEvidenceError:
        raise
    except OSError as error:
        raise SnapshotEvidenceError("snapshot HMAC key could not be read") from error
    if not 32 <= len(key) <= max_bytes:
        raise SnapshotEvidenceError("snapshot HMAC key length is invalid")
    return key


class SnapshotEvidenceBatchEvaluator:
    """Evaluate exactly the destructive VM-only scenario set; never controls a VM."""

    def __init__(
        self,
        manifests: Sequence[SnapshotScenarioManifest],
        gate: SnapshotEvidenceGate,
    ) -> None:
        manifest_ids = tuple(item.scenario_id for item in manifests)
        if manifest_ids != SNAPSHOT_SCENARIO_IDS:
            raise SnapshotEvidenceError(
                "snapshot manifests must be ordered S08/S09/S10/S12 exactly once"
            )
        self._manifests = tuple(manifests)
        self._gate = gate

    def evaluate_paths(
        self,
        evidence_paths: Mapping[str, Path],
        *,
        now: datetime | None = None,
    ) -> SnapshotBatchReport:
        if set(evidence_paths) != set(SNAPSHOT_SCENARIO_IDS):
            raise SnapshotEvidenceError(
                "snapshot evidence must contain S08/S09/S10/S12 exactly once"
            )
        evidence = {
            scenario_id: load_snapshot_evidence(evidence_paths[scenario_id])
            for scenario_id in SNAPSHOT_SCENARIO_IDS
        }
        run_ids = [item.run_id for item in evidence.values()]
        if len(run_ids) != len(set(run_ids)):
            raise SnapshotEvidenceError("snapshot run_id values must be unique")

        results: list[SnapshotBatchResult] = []
        for manifest in self._manifests:
            report = self._gate.validate(
                manifest,
                evidence[manifest.scenario_id],
                now=now,
            )
            results.append(
                SnapshotBatchResult(
                    scenario_id=manifest.scenario_id,
                    accepted=report.accepted,
                    reason_codes=report.reason_codes,
                    signed_payload_sha256=report.signed_payload_sha256,
                )
            )
        accepted = sum(item.accepted for item in results)
        return SnapshotBatchReport(
            results=tuple(results),
            accepted=accepted,
            rejected=4 - accepted,
            all_accepted=accepted == 4,
        )
