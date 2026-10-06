from __future__ import annotations

import json
import math
import os
import re
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import Field, StrictBool, StrictFloat, StrictInt, StrictStr, model_validator

from .models import FrozenModel
from .windows_acl import WindowsUserOnlyAcl

Scalar = StrictStr | StrictInt | StrictFloat | StrictBool | None
_TOKEN = re.compile(r"[0-9A-Za-z가-힣_]+")
_SECRET = re.compile(
    r"(?i)(password\s*[:=]|api[_ -]?key\s*[:=]|bearer\s+[a-z0-9._-]+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\bsk-[a-z0-9_-]{12,})"
)


class MemoryError(RuntimeError):
    pass


class MemorySensitivity(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"


class MemorySource(StrEnum):
    EXPLICIT_USER_INSTRUCTION = "explicit_user_instruction"
    VERIFIED_TASK = "verified_task"


class EpisodeResult(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    ABORTED = "ABORTED"


class SemanticMemoryCandidate(FrozenModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")
    value: Scalar
    source: MemorySource
    confidence: float = Field(ge=0.0, le=1.0)
    sensitivity: MemorySensitivity
    expires_at: datetime | None = None

    @model_validator(mode="after")
    def validate_value(self) -> SemanticMemoryCandidate:
        _validate_time(self.expires_at)
        if isinstance(self.value, str) and len(self.value) > 4_096:
            raise ValueError("memory string value exceeds size limit")
        if isinstance(self.value, float) and not math.isfinite(self.value):
            raise ValueError("memory value must be finite")
        _reject_secret(self.key, self.value)
        return self


class SemanticMemoryRecord(SemanticMemoryCandidate):
    memory_id: UUID
    created_at: datetime
    last_used_at: datetime | None = None


class EpisodeCandidate(FrozenModel):
    task_id: UUID
    summary: str = Field(min_length=1, max_length=1_000)
    result: EpisodeResult
    applications: tuple[str, ...] = Field(default=(), max_length=16)
    sensitivity: MemorySensitivity

    @model_validator(mode="after")
    def validate_content(self) -> EpisodeCandidate:
        if any(not re.fullmatch(r"[a-z0-9._-]{1,64}", app) for app in self.applications):
            raise ValueError("invalid application ID")
        _reject_secret("episode.summary", self.summary)
        return self


class EpisodeRecord(EpisodeCandidate):
    episode_id: UUID
    created_at: datetime
    expires_at: datetime


class FileAcl(Protocol):
    def harden(self, path: Path) -> None: ...


class SqliteMemoryRepository:
    def __init__(
        self,
        path: Path,
        *,
        max_rows: int = 10_000,
        max_bytes: int = 10 * 1024 * 1024,
        acl: FileAcl | None = None,
    ) -> None:
        if not path.is_absolute() or path.is_symlink() or max_rows < 1 or max_bytes < 4096:
            raise ValueError("invalid memory repository configuration")
        if not path.parent.is_dir() or path.parent.is_symlink():
            raise ValueError("memory parent must be an existing non-symlink directory")
        if path.exists() and (not path.is_file() or path.stat().st_size > max_bytes):
            raise ValueError("invalid memory database file")
        self._path = path
        self._max_rows = max_rows
        self._max_bytes = max_bytes
        self._acl = acl if acl is not None else (WindowsUserOnlyAcl() if os.name == "nt" else None)
        self._initialize()

    def remember(
        self, candidate: SemanticMemoryCandidate, *, now: datetime | None = None
    ) -> SemanticMemoryRecord:
        instant = _utc(now)
        if candidate.expires_at is not None and candidate.expires_at <= instant:
            raise MemoryError("cannot store expired memory")
        memory_id = uuid4()
        value_json = json.dumps(candidate.value, ensure_ascii=False, allow_nan=False)
        with closing(self._connect()) as db, db:
            existing = db.execute(
                "SELECT memory_id, created_at FROM semantic WHERE key=?", (candidate.key,)
            ).fetchone()
            if existing is None:
                self._require_capacity(db)
                created = instant
            else:
                memory_id = UUID(existing[0])
                created = datetime.fromisoformat(existing[1])
                db.execute("DELETE FROM semantic_fts WHERE memory_id=?", (str(memory_id),))
            values = (
                str(memory_id),
                candidate.key,
                value_json,
                candidate.source.value,
                candidate.confidence,
                candidate.sensitivity.value,
                created.isoformat(),
                None,
                candidate.expires_at.isoformat() if candidate.expires_at else None,
            )
            db.execute("INSERT OR REPLACE INTO semantic VALUES (?,?,?,?,?,?,?,?,?)", values)
            db.execute(
                "INSERT INTO semantic_fts(memory_id,key,value_text) VALUES (?,?,?)",
                (str(memory_id), candidate.key, str(candidate.value)),
            )
        self._check_size()
        return SemanticMemoryRecord(
            memory_id=memory_id, created_at=created, **candidate.model_dump()
        )

    def get(self, key: str, *, now: datetime | None = None) -> SemanticMemoryRecord | None:
        instant = _utc(now)
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT * FROM semantic WHERE key=? AND (expires_at IS NULL OR expires_at>?)",
                (key, instant.isoformat()),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE semantic SET last_used_at=? WHERE memory_id=?",
                (instant.isoformat(), row[0]),
            )
        return _semantic(row, last_used_at=instant)

    def get_by_id(
        self,
        memory_id: UUID,
        *,
        now: datetime | None = None,
    ) -> SemanticMemoryRecord | None:
        instant = _utc(now)
        with closing(self._connect()) as db, db:
            row = db.execute(
                "SELECT * FROM semantic WHERE memory_id=? "
                "AND (expires_at IS NULL OR expires_at>?)",
                (str(memory_id), instant.isoformat()),
            ).fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE semantic SET last_used_at=? WHERE memory_id=?",
                (instant.isoformat(), str(memory_id)),
            )
        return _semantic(row, last_used_at=instant)

    def search(
        self, query: str, *, k: int = 10, now: datetime | None = None
    ) -> tuple[SemanticMemoryRecord, ...]:
        if not 1 <= k <= 50:
            raise ValueError("memory search k must be within 1..50")
        tokens = _TOKEN.findall(query)[:16]
        if not tokens:
            return ()
        expression = " AND ".join(f'"{token}"' for token in tokens)
        instant = _utc(now)
        sql = (
            "SELECT s.* FROM semantic_fts f JOIN semantic s ON s.memory_id=f.memory_id "
            "WHERE semantic_fts MATCH ? AND (s.expires_at IS NULL OR s.expires_at>?) "
            "ORDER BY bm25(semantic_fts) LIMIT ?"
        )
        with closing(self._connect()) as db, db:
            rows = db.execute(sql, (expression, instant.isoformat(), k)).fetchall()
        return tuple(_semantic(row) for row in rows)

    def delete(self, memory_id: UUID) -> bool:
        with closing(self._connect()) as db, db:
            db.execute("DELETE FROM semantic_fts WHERE memory_id=?", (str(memory_id),))
            cursor = db.execute("DELETE FROM semantic WHERE memory_id=?", (str(memory_id),))
        return cursor.rowcount == 1

    def record_episode(
        self, candidate: EpisodeCandidate, *, now: datetime | None = None
    ) -> EpisodeRecord:
        instant = _utc(now)
        expires = instant + timedelta(days=30)
        episode_id = uuid4()
        with closing(self._connect()) as db, db:
            self._require_capacity(db)
            db.execute(
                "INSERT INTO episodes VALUES (?,?,?,?,?,?,?,?)",
                (
                    str(episode_id),
                    str(candidate.task_id),
                    candidate.summary,
                    candidate.result.value,
                    json.dumps(candidate.applications),
                    candidate.sensitivity.value,
                    instant.isoformat(),
                    expires.isoformat(),
                ),
            )
        self._check_size()
        return EpisodeRecord(
            episode_id=episode_id, created_at=instant, expires_at=expires, **candidate.model_dump()
        )

    def purge_expired(self, *, now: datetime | None = None) -> int:
        instant = _utc(now).isoformat()
        with closing(self._connect()) as db, db:
            ids = [
                row[0]
                for row in db.execute(
                    "SELECT memory_id FROM semantic WHERE expires_at<=?", (instant,)
                )
            ]
            db.executemany("DELETE FROM semantic_fts WHERE memory_id=?", ((item,) for item in ids))
            first = db.execute("DELETE FROM semantic WHERE expires_at<=?", (instant,)).rowcount
            second = db.execute("DELETE FROM episodes WHERE expires_at<=?", (instant,)).rowcount
        return first + second

    def _initialize(self) -> None:
        with closing(self._connect()) as db, db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1):
                raise MemoryError("unsupported memory schema version")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS semantic(
                  memory_id TEXT PRIMARY KEY,key TEXT UNIQUE NOT NULL,
                  value_json TEXT NOT NULL,source TEXT NOT NULL,confidence REAL NOT NULL,
                  sensitivity TEXT NOT NULL,
                  created_at TEXT NOT NULL,last_used_at TEXT,expires_at TEXT);
                CREATE VIRTUAL TABLE IF NOT EXISTS semantic_fts USING fts5(
                  memory_id UNINDEXED,key,value_text);
                CREATE TABLE IF NOT EXISTS episodes(
                  episode_id TEXT PRIMARY KEY,task_id TEXT NOT NULL,
                  summary TEXT NOT NULL,result TEXT NOT NULL,applications_json TEXT NOT NULL,
                  sensitivity TEXT NOT NULL,
                  created_at TEXT NOT NULL,expires_at TEXT NOT NULL);
                PRAGMA user_version=1;
            """)
        if self._acl is not None:
            self._acl.harden(self._path)
        self._check_size()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self._path, timeout=5)
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA temp_store=MEMORY")
        db.execute("PRAGMA busy_timeout=5000")
        page_size = int(db.execute("PRAGMA page_size").fetchone()[0])
        max_pages = max(1, self._max_bytes // page_size)
        db.execute(f"PRAGMA max_page_count={max_pages}")
        return db

    def _require_capacity(self, db: sqlite3.Connection) -> None:
        count = db.execute(
            "SELECT (SELECT count(*) FROM semantic)+(SELECT count(*) FROM episodes)"
        ).fetchone()[0]
        if count >= self._max_rows:
            raise MemoryError("memory row limit reached")

    def _check_size(self) -> None:
        if self._path.stat().st_size > self._max_bytes:
            raise MemoryError("memory database size limit exceeded")


def _validate_time(value: datetime | None) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() != timedelta(0)):
        raise ValueError("memory timestamps must be UTC-aware")


def _utc(value: datetime | None) -> datetime:
    result = value or datetime.now(UTC)
    _validate_time(result)
    return result


def _reject_secret(key: str, value: object) -> None:
    if _SECRET.search(f"{key}={value}"):
        raise ValueError("credential-like content cannot be stored in memory")


def _semantic(
    row: tuple[object, ...], *, last_used_at: datetime | None = None
) -> SemanticMemoryRecord:
    return SemanticMemoryRecord(
        memory_id=UUID(str(row[0])),
        key=str(row[1]),
        value=json.loads(str(row[2])),
        source=MemorySource(str(row[3])),
        confidence=float(str(row[4])),
        sensitivity=MemorySensitivity(str(row[5])),
        created_at=datetime.fromisoformat(str(row[6])),
        last_used_at=last_used_at or (datetime.fromisoformat(str(row[7])) if row[7] else None),
        expires_at=datetime.fromisoformat(str(row[8])) if row[8] else None,
    )
