from __future__ import annotations

import hashlib
import json
import os
from collections import deque
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from threading import Lock
from typing import Any, Protocol

from pydantic import Field, JsonValue

from .file_lock import InterprocessFileLock
from .models import FrozenModel
from .runtime_models import AuditEvent
from .windows_acl import WindowsUserOnlyAcl


class AuditSink(Protocol):
    def append(self, event: AuditEvent) -> None: ...


class InMemoryAuditSink:
    def __init__(self, *, max_events: int = 10_000) -> None:
        if max_events < 1:
            raise ValueError("max_events must be positive")
        self._events: deque[AuditEvent] = deque(maxlen=max_events)

    def append(self, event: AuditEvent) -> None:
        self._events.append(event)

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._events)

    def of_type(self, event_type: str) -> tuple[AuditEvent, ...]:
        return tuple(event for event in self._events if event.event_type == event_type)

    def extend(self, events: Iterable[AuditEvent]) -> None:
        self._events.extend(events)


_SENSITIVE_KEYS = frozenset(
    {
        "token",
        "approval_token",
        "access_token",
        "refresh_token",
        "secret",
        "password",
        "api_key",
        "nim_api_key",
        "authorization",
    }
)


def redact_json(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if key.casefold() in _SENSITIVE_KEYS else redact_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    return value


class AuditRetentionPolicy(FrozenModel):
    max_file_bytes: int = Field(default=10 * 1024 * 1024, ge=1_024)
    backup_count: int = Field(default=5, ge=1, le=100)


class AuditIntegrityError(RuntimeError):
    pass


class AuditIntegrityReport(FrozenModel):
    records: int = Field(ge=0)
    files: int = Field(ge=0)
    tip_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class JsonlAuditSink:
    """Append-only, hash-chained JSONL sink with structural secret redaction."""

    _GENESIS_HASH = "0" * 64
    _MAX_RECORD_BYTES = 1024 * 1024

    def __init__(
        self,
        path: Path,
        retention: AuditRetentionPolicy | None = None,
    ) -> None:
        if path.is_symlink():
            raise ValueError("audit path must not be a symlink")
        self._path = path.resolve()
        if self._path.exists() and self._path.is_dir():
            raise ValueError("audit path must be a file")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._retention = retention
        self._acl = WindowsUserOnlyAcl()
        self._process_lock = InterprocessFileLock(
            self._path.with_name(f"{self._path.name}.lock"),
            hardener=self._acl.harden,
        )

    @property
    def path(self) -> Path:
        return self._path

    def append(self, event: AuditEvent) -> None:
        raw = event.model_dump(mode="json")
        redacted = redact_json(raw)
        with self._lock, self._process_lock:
            previous_hash = self._latest_record_hash()
            payload = self._encode_record(redacted, previous_hash)
            maximum = (
                self._retention.max_file_bytes
                if self._retention is not None
                else self._MAX_RECORD_BYTES
            )
            if len(payload) > maximum or len(payload) > self._MAX_RECORD_BYTES:
                raise ValueError("audit event exceeds retention file size")
            self._rotate_if_needed(len(payload))
            descriptor = os.open(
                self._path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o600,
            )
            try:
                written = os.write(descriptor, payload)
                if written != len(payload):
                    raise OSError("partial audit write")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            self._acl.harden(self._path)

    def verify_integrity(self) -> AuditIntegrityReport:
        with self._lock, self._process_lock:
            paths = self._retained_paths()
            expected_previous: str | None = None
            records = 0
            for path in paths:
                if path.is_symlink() or not path.is_file():
                    raise AuditIntegrityError("audit chain contains an invalid file")
                try:
                    with path.open("rb") as handle:
                        for raw_line in handle:
                            if len(raw_line) > self._MAX_RECORD_BYTES:
                                raise AuditIntegrityError("audit record exceeded size limit")
                            if not raw_line.endswith(b"\n"):
                                raise AuditIntegrityError("audit record is truncated")
                            document = self._decode_record(raw_line[:-1])
                            previous = document["audit_previous_hash"]
                            current = document["audit_record_hash"]
                            if expected_previous is not None and previous != expected_previous:
                                raise AuditIntegrityError("audit chain link mismatch")
                            expected_previous = current
                            records += 1
                except OSError as error:
                    raise AuditIntegrityError("audit chain could not be read") from error
            return AuditIntegrityReport(
                records=records,
                files=len(paths),
                tip_sha256=expected_previous,
            )

    def _rotate_if_needed(self, incoming_bytes: int) -> None:
        policy = self._retention
        if policy is None or not self._path.exists():
            return
        current_size = self._path.stat().st_size
        if current_size + incoming_bytes <= policy.max_file_bytes:
            return
        oldest = self._backup_path(policy.backup_count)
        with suppress(FileNotFoundError):
            oldest.unlink()
        for index in range(policy.backup_count - 1, 0, -1):
            source = self._backup_path(index)
            if source.exists():
                source.replace(self._backup_path(index + 1))
        self._path.replace(self._backup_path(1))

    def _backup_path(self, index: int) -> Path:
        return self._path.with_name(f"{self._path.name}.{index}")

    def _retained_paths(self) -> tuple[Path, ...]:
        backups: list[Path] = []
        if self._retention is not None:
            for index in range(self._retention.backup_count, 0, -1):
                candidate = self._backup_path(index)
                if candidate.exists():
                    backups.append(candidate)
        if self._path.exists():
            backups.append(self._path)
        return tuple(backups)

    def _latest_record_hash(self) -> str:
        candidates = [self._path]
        if self._retention is not None:
            candidates.extend(
                self._backup_path(index)
                for index in range(1, self._retention.backup_count + 1)
            )
        for candidate in candidates:
            if not candidate.exists():
                continue
            raw_line = self._last_record(candidate)
            if raw_line is not None:
                return str(self._decode_record(raw_line)["audit_record_hash"])
        return self._GENESIS_HASH

    def _last_record(self, path: Path) -> bytes | None:
        if path.is_symlink() or not path.is_file():
            raise AuditIntegrityError("audit chain contains an invalid file")
        try:
            size = path.stat().st_size
            if size == 0:
                return None
            with path.open("rb") as handle:
                read_size = min(size, self._MAX_RECORD_BYTES + 1)
                handle.seek(size - read_size)
                tail = handle.read(read_size)
        except OSError as error:
            raise AuditIntegrityError("audit chain could not be read") from error
        if not tail.endswith(b"\n"):
            raise AuditIntegrityError("audit record is truncated")
        lines = tail.splitlines()
        if not lines:
            return None
        last = lines[-1]
        if len(last) > self._MAX_RECORD_BYTES or (
            size > read_size and b"\n" not in tail[:-len(last)]
        ):
            raise AuditIntegrityError("audit record exceeded size limit")
        return last

    @classmethod
    def _encode_record(cls, event: JsonValue, previous_hash: str) -> bytes:
        if not isinstance(event, dict):
            raise ValueError("audit event must be an object")
        body = dict(event)
        body["audit_previous_hash"] = previous_hash
        canonical = json.dumps(
            body,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        body["audit_record_hash"] = hashlib.sha256(canonical).hexdigest()
        return (
            json.dumps(
                body,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")

    @classmethod
    def _decode_record(cls, raw: bytes) -> dict[str, Any]:
        try:
            document = json.loads(
                raw.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise AuditIntegrityError("audit record is invalid") from error
        if not isinstance(document, dict):
            raise AuditIntegrityError("audit record is not an object")
        previous = document.get("audit_previous_hash")
        current = document.pop("audit_record_hash", None)
        if not _is_sha256(previous) or not _is_sha256(current):
            raise AuditIntegrityError("audit record hash metadata is invalid")
        canonical = json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        if hashlib.sha256(canonical).hexdigest() != current:
            raise AuditIntegrityError("audit record hash mismatch")
        document["audit_record_hash"] = current
        return document


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate audit JSON key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite audit JSON number: {value}")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
