from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from pydantic import Field, TypeAdapter, field_validator, model_validator

from .models import FrozenModel


class SnapshotEvidenceError(ValueError):
    """Raised when a manifest or evidence document cannot be trusted."""


class SnapshotSignatureVerifier(Protocol):
    def verify(self, key_id: str, payload: bytes, signature: str) -> bool: ...


class SnapshotScenarioManifest(FrozenModel):
    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    scenario_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    vm_provider: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    base_snapshot_id: str = Field(min_length=1, max_length=256)
    guest_machine_id: str = Field(min_length=1, max_length=256)
    capability_id: str = Field(min_length=1, max_length=128)
    expected_outcome: str = Field(min_length=1, max_length=128)


class SnapshotVmEvidence(FrozenModel):
    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    scenario_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    vm_provider: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    base_snapshot_id: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    started_at: datetime
    finished_at: datetime
    guest_machine_id: str = Field(min_length=1, max_length=256)
    capability_id: str = Field(min_length=1, max_length=128)
    expected_outcome: str = Field(min_length=1, max_length=128)
    observed_outcome: str = Field(min_length=1, max_length=128)
    restore_attempted: bool
    restore_succeeded: bool
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    signature_key_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    signature: str = Field(
        min_length=16,
        max_length=4096,
        pattern=r"^[A-Za-z0-9_+/=-]+$",
        repr=False,
    )

    @field_validator("started_at", "finished_at")
    @classmethod
    def timestamps_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("timestamps must explicitly use UTC")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def timestamps_must_be_ordered(self) -> SnapshotVmEvidence:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        return self


class SnapshotEvidenceReport(FrozenModel):
    scenario_id: str
    run_id: str
    accepted: bool
    reason_codes: tuple[str, ...]
    signed_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def canonical_json(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )


def canonical_signed_payload(evidence: SnapshotVmEvidence) -> bytes:
    """Return the only byte representation accepted by signature verifiers."""
    document = evidence.model_dump(mode="json", exclude={"signature"})
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class SnapshotEvidenceGate:
    """Validate external VM evidence without controlling a VM or the host."""

    def __init__(
        self,
        verifier: SnapshotSignatureVerifier,
        *,
        max_age: timedelta = timedelta(hours=1),
        max_duration: timedelta = timedelta(minutes=30),
        future_skew: timedelta = timedelta(minutes=2),
    ) -> None:
        if min(max_age, max_duration, future_skew) < timedelta(0):
            raise ValueError("time bounds must not be negative")
        if max_age == timedelta(0) or max_duration == timedelta(0):
            raise ValueError("age and duration bounds must be positive")
        self._verifier = verifier
        self._max_age = max_age
        self._max_duration = max_duration
        self._future_skew = future_skew

    def validate(
        self,
        manifest: SnapshotScenarioManifest,
        evidence: SnapshotVmEvidence,
        *,
        now: datetime | None = None,
    ) -> SnapshotEvidenceReport:
        current = now or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() != timedelta(0):
            raise ValueError("now must explicitly use UTC")
        reasons: list[str] = []
        expected_binding = (
            manifest.schema_version,
            manifest.scenario_id,
            manifest.vm_provider,
            manifest.base_snapshot_id,
            manifest.guest_machine_id,
            manifest.capability_id,
            manifest.expected_outcome,
        )
        observed_binding = (
            evidence.schema_version,
            evidence.scenario_id,
            evidence.vm_provider,
            evidence.base_snapshot_id,
            evidence.guest_machine_id,
            evidence.capability_id,
            evidence.expected_outcome,
        )
        if observed_binding != expected_binding:
            reasons.append("MANIFEST_MISMATCH")
        if evidence.finished_at - evidence.started_at > self._max_duration:
            reasons.append("RUN_DURATION_EXCEEDED")
        if evidence.finished_at < current - self._max_age:
            reasons.append("STALE_EVIDENCE")
        if max(evidence.started_at, evidence.finished_at) > current + self._future_skew:
            reasons.append("FUTURE_EVIDENCE")
        if not evidence.restore_attempted or not evidence.restore_succeeded:
            reasons.append("RESTORE_NOT_CONFIRMED")
        if evidence.observed_outcome != manifest.expected_outcome:
            reasons.append("OUTCOME_MISMATCH")

        payload = canonical_signed_payload(evidence)
        try:
            signature_valid = self._verifier.verify(
                evidence.signature_key_id,
                payload,
                evidence.signature,
            )
        except Exception:  # verifier is an external trust boundary
            signature_valid = False
        if not signature_valid:
            reasons.append("SIGNATURE_INVALID")
        return SnapshotEvidenceReport(
            scenario_id=evidence.scenario_id,
            run_id=evidence.run_id,
            accepted=not reasons,
            reason_codes=tuple(reasons),
            signed_payload_sha256=hashlib.sha256(payload).hexdigest(),
        )


def load_snapshot_manifests(
    path: Path,
    *,
    max_bytes: int = 256 * 1024,
) -> tuple[SnapshotScenarioManifest, ...]:
    raw = _load_strict_json(path, max_bytes=max_bytes)
    try:
        manifests = tuple(TypeAdapter(list[SnapshotScenarioManifest]).validate_python(raw))
    except ValueError as exc:
        raise SnapshotEvidenceError("invalid snapshot scenario manifest") from exc
    identifiers = [manifest.scenario_id for manifest in manifests]
    if len(identifiers) != len(set(identifiers)):
        raise SnapshotEvidenceError("scenario_id values must be unique")
    return manifests


def load_snapshot_evidence(
    path: Path,
    *,
    max_bytes: int = 64 * 1024,
) -> SnapshotVmEvidence:
    raw = _load_strict_json(path, max_bytes=max_bytes)
    try:
        return SnapshotVmEvidence.model_validate(raw)
    except ValueError as exc:
        raise SnapshotEvidenceError("invalid snapshot VM evidence") from exc


def _load_strict_json(path: Path, *, max_bytes: int) -> object:
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    if path.is_symlink() or not path.is_file():
        raise SnapshotEvidenceError("document must be a regular non-symlink file")
    try:
        if path.stat().st_size > max_bytes:
            raise SnapshotEvidenceError("document exceeded size limit")
        raw = path.read_bytes()
        if len(raw) > max_bytes:
            raise SnapshotEvidenceError("document exceeded size limit")
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except SnapshotEvidenceError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SnapshotEvidenceError("invalid JSON document") from exc


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SnapshotEvidenceError("duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise SnapshotEvidenceError(f"non-finite JSON number: {value}")
