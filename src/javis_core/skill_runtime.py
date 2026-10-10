from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from .models import FrozenModel, Plan
from .registry import CapabilityRegistry


class SkillRuntimeError(RuntimeError):
    pass


class SkillStatus(StrEnum):
    DRAFT = "DRAFT"
    VERIFIED = "VERIFIED"
    RETIRED = "RETIRED"


class SkillOutcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class SkillProposal(FrozenModel):
    """A reusable procedure, not executable code and not an authority grant."""

    key: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=1_000)
    triggers: tuple[str, ...] = Field(min_length=1, max_length=16)
    procedure: tuple[str, ...] = Field(min_length=1, max_length=20)
    allowed_capabilities: tuple[str, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def normalized_and_unique(self) -> SkillProposal:
        if len(set(self.triggers)) != len(self.triggers):
            raise ValueError("skill triggers must be unique")
        if len(set(self.allowed_capabilities)) != len(self.allowed_capabilities):
            raise ValueError("skill capabilities must be unique")
        if any(not item.strip() or "\0" in item for item in (*self.triggers, *self.procedure)):
            raise ValueError("skill text must be non-empty and must not contain NUL")
        return self


class SkillRecord(SkillProposal):
    skill_id: UUID
    version: int = Field(ge=1)
    status: SkillStatus
    created_at: datetime
    updated_at: datetime
    supersedes_skill_id: UUID | None = None

    @model_validator(mode="after")
    def timestamps_are_utc(self) -> SkillRecord:
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("skill timestamps must be timezone-aware")
        return self


class SkillUseRecord(FrozenModel):
    use_id: UUID
    skill_id: UUID
    skill_version: int = Field(ge=1)
    outcome: SkillOutcome
    observed_at: datetime
    detail_code: str = Field(pattern=r"^[A-Z0-9_.:-]{2,80}$")


class PlanProposer(Protocol):
    def plan(
        self,
        goal: str,
        *,
        trusted_context: Mapping[str, object] | None = None,
        untrusted_context: str | None = None,
    ) -> Plan: ...


class LocalSkillCatalog:
    """Versioned local skills with explicit promotion before runtime use.

    A skill can restrict the capabilities a planner may use; it can never add a
    capability that is absent from the immutable Windows capability registry.
    """

    def __init__(self, path: Path, registry: CapabilityRegistry) -> None:
        if not path.is_absolute() or path.is_symlink() or not path.parent.is_dir():
            raise ValueError("skill catalog path must be absolute in an existing directory")
        self._path = path
        self._registry = registry
        self._initialize()

    def propose(self, proposal: SkillProposal, *, now: datetime | None = None) -> SkillRecord:
        self._validate_capabilities(proposal)
        instant = _utc(now)
        existing = self._latest_by_key(proposal.key)
        if existing is not None and existing.status is not SkillStatus.RETIRED:
            raise SkillRuntimeError("active skill key already exists; propose a revision instead")
        return self._insert(
            proposal,
            version=1,
            status=SkillStatus.DRAFT,
            instant=instant,
            supersedes=None,
        )

    def approve(self, skill_id: UUID, *, now: datetime | None = None) -> SkillRecord:
        record = self.require(skill_id)
        if record.status is not SkillStatus.DRAFT:
            raise SkillRuntimeError("only a draft skill can be approved")
        instant = _utc(now)
        with closing(self._connect()) as db, db:
            if record.supersedes_skill_id is not None:
                db.execute(
                    "UPDATE skills SET status=?, updated_at=? WHERE skill_id=?",
                    (
                        SkillStatus.RETIRED.value,
                        instant.isoformat(),
                        str(record.supersedes_skill_id),
                    ),
                )
            db.execute(
                "UPDATE skills SET status=?, updated_at=? WHERE skill_id=?",
                (SkillStatus.VERIFIED.value, instant.isoformat(), str(skill_id)),
            )
        return self.require(skill_id)

    def propose_revision(
        self,
        skill_id: UUID,
        proposal: SkillProposal,
        *,
        now: datetime | None = None,
    ) -> SkillRecord:
        previous = self.require(skill_id)
        if previous.status is not SkillStatus.VERIFIED:
            raise SkillRuntimeError("only a verified skill can be revised")
        if proposal.key != previous.key:
            raise SkillRuntimeError("a revision must keep the same skill key")
        self._validate_capabilities(proposal)
        return self._insert(
            proposal,
            version=previous.version + 1,
            status=SkillStatus.DRAFT,
            instant=_utc(now),
            supersedes=previous.skill_id,
        )

    def retire(self, skill_id: UUID, *, now: datetime | None = None) -> SkillRecord:
        record = self.require(skill_id)
        instant = _utc(now)
        with closing(self._connect()) as db, db:
            db.execute(
                "UPDATE skills SET status=?, updated_at=? WHERE skill_id=?",
                (SkillStatus.RETIRED.value, instant.isoformat(), str(skill_id)),
            )
        return self.require(record.skill_id)

    def require(self, skill_id: UUID) -> SkillRecord:
        with closing(self._connect()) as db:
            row = db.execute("SELECT * FROM skills WHERE skill_id=?", (str(skill_id),)).fetchone()
        if row is None:
            raise SkillRuntimeError("skill not found")
        return _record(row)

    def search(self, query: str, *, limit: int = 3) -> tuple[SkillRecord, ...]:
        if not 1 <= limit <= 20:
            raise ValueError("skill search limit must be within 1..20")
        tokens = tuple(_tokens(query))
        if not tokens:
            return ()
        with closing(self._connect()) as db:
            rows = db.execute(
                "SELECT * FROM skills WHERE status=? ORDER BY updated_at DESC",
                (SkillStatus.VERIFIED.value,),
            ).fetchall()
        scored = [(_score(tokens, _record(row)), _record(row)) for row in rows]
        return tuple(record for score, record in scored if score > 0)[:limit]

    def record_use(
        self,
        skill: SkillRecord,
        outcome: SkillOutcome,
        detail_code: str,
        *,
        now: datetime | None = None,
    ) -> SkillUseRecord:
        if skill.status is not SkillStatus.VERIFIED:
            raise SkillRuntimeError("only verified skills may be used")
        result = SkillUseRecord(
            use_id=uuid4(),
            skill_id=skill.skill_id,
            skill_version=skill.version,
            outcome=outcome,
            observed_at=_utc(now),
            detail_code=detail_code,
        )
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO skill_uses VALUES (?,?,?,?,?,?)",
                (
                    str(result.use_id), str(result.skill_id), result.skill_version,
                    result.outcome.value, result.observed_at.isoformat(), result.detail_code,
                ),
            )
        return result

    def _latest_by_key(self, key: str) -> SkillRecord | None:
        with closing(self._connect()) as db:
            row = db.execute(
                "SELECT * FROM skills WHERE key=? ORDER BY version DESC LIMIT 1", (key,)
            ).fetchone()
        return _record(row) if row is not None else None

    def _insert(
        self,
        proposal: SkillProposal,
        *,
        version: int,
        status: SkillStatus,
        instant: datetime,
        supersedes: UUID | None,
    ) -> SkillRecord:
        result = SkillRecord(
            skill_id=uuid4(), version=version, status=status, created_at=instant,
            updated_at=instant, supersedes_skill_id=supersedes, **proposal.model_dump(),
        )
        with closing(self._connect()) as db, db:
            db.execute(
                "INSERT INTO skills VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    str(result.skill_id), result.key, result.version, result.status.value,
                    result.title, result.description,
                    json.dumps(result.triggers, ensure_ascii=False),
                    json.dumps(result.procedure, ensure_ascii=False),
                    json.dumps(result.allowed_capabilities), result.created_at.isoformat(),
                    result.updated_at.isoformat(),
                    str(result.supersedes_skill_id) if result.supersedes_skill_id else None,
                ),
            )
        return result

    def _validate_capabilities(self, proposal: SkillProposal) -> None:
        for capability_id in proposal.allowed_capabilities:
            if self._registry.get(capability_id) is None:
                raise SkillRuntimeError("skill references an unregistered capability")

    def _initialize(self) -> None:
        with closing(self._connect()) as db, db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS skills(
                  skill_id TEXT PRIMARY KEY, key TEXT NOT NULL, version INTEGER NOT NULL,
                  status TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL,
                  triggers_json TEXT NOT NULL, procedure_json TEXT NOT NULL,
                  capabilities_json TEXT NOT NULL, created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL, supersedes_skill_id TEXT);
                CREATE UNIQUE INDEX IF NOT EXISTS skill_version ON skills(key, version);
                CREATE TABLE IF NOT EXISTS skill_uses(
                  use_id TEXT PRIMARY KEY, skill_id TEXT NOT NULL, skill_version INTEGER NOT NULL,
                  outcome TEXT NOT NULL, observed_at TEXT NOT NULL, detail_code TEXT NOT NULL);
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path)
        connection.row_factory = sqlite3.Row
        return connection


class SkillAwarePlanner:
    """Retrieve a verified skill first and bind the plan to its capability allowlist."""

    def __init__(self, planner: PlanProposer, catalog: LocalSkillCatalog) -> None:
        self._planner = planner
        self._catalog = catalog

    def plan(
        self,
        goal: str,
        *,
        trusted_context: Mapping[str, object] | None = None,
        untrusted_context: str | None = None,
    ) -> Plan:
        matches = self._catalog.search(goal, limit=1)
        if not matches:
            raise SkillRuntimeError("no verified skill matches this request")
        skill = matches[0]
        context = dict(trusted_context or {})
        context["verified_skill"] = {
            "skill_id": str(skill.skill_id),
            "version": skill.version,
            "title": skill.title,
            "procedure": skill.procedure,
            "allowed_capabilities": skill.allowed_capabilities,
        }
        plan = self._planner.plan(
            goal, trusted_context=context, untrusted_context=untrusted_context
        )
        allowed = set(skill.allowed_capabilities)
        if any(action.capability_id not in allowed for action in plan.steps):
            raise SkillRuntimeError("planner proposed a capability outside the selected skill")
        return plan


def _record(row: sqlite3.Row) -> SkillRecord:
    return SkillRecord(
        skill_id=UUID(row["skill_id"]), key=row["key"], version=row["version"],
        status=SkillStatus(row["status"]), title=row["title"], description=row["description"],
        triggers=tuple(json.loads(row["triggers_json"])),
        procedure=tuple(json.loads(row["procedure_json"])),
        allowed_capabilities=tuple(json.loads(row["capabilities_json"])),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        supersedes_skill_id=UUID(row["supersedes_skill_id"])
        if row["supersedes_skill_id"] else None,
    )


def _utc(value: datetime | None) -> datetime:
    instant = value or datetime.now(UTC)
    if instant.tzinfo is None:
        raise ValueError("skill time must be timezone-aware")
    return instant.astimezone(UTC)


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(item.lower() for item in re.findall(r"[0-9A-Za-z가-힣_]+", value)[:16])


def _score(tokens: tuple[str, ...], skill: SkillRecord) -> int:
    searchable = " ".join((skill.key, skill.title, skill.description, *skill.triggers)).lower()
    return sum(1 for token in tokens if token in searchable)
