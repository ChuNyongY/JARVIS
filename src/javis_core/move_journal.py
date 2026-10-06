from __future__ import annotations

import os
import threading
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from .file_lock import InterprocessFileLock
from .file_move import MoveJournalRecord, MoveState, file_sha256
from .windows_acl import WindowsUserOnlyAcl


class MoveJournalError(RuntimeError):
    """Raised when a durable move journal cannot be trusted."""


class DurableMoveJournal:
    """Append-only JSONL move journal with process-local serialization."""

    def __init__(self, path: Path) -> None:
        self.path = path
        if path.exists() and path.is_dir():
            raise MoveJournalError("journal path is a directory")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._acl = WindowsUserOnlyAcl()
        self._process_lock = InterprocessFileLock(
            path.with_name(f"{path.name}.lock"),
            hardener=self._acl.harden,
        )

    def append(self, record: MoveJournalRecord) -> None:
        payload = record.model_dump_json().encode("utf-8") + b"\n"
        with self._lock, self._process_lock:
            try:
                descriptor = os.open(
                    self.path,
                    os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                    0o600,
                )
                try:
                    written = os.write(descriptor, payload)
                    if written != len(payload):
                        raise OSError("partial journal write")
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                self._acl.harden(self.path)
                self._restrict_permissions_best_effort()
            except OSError as error:
                raise MoveJournalError("could not persist move journal record") from error

    def load(self) -> tuple[MoveJournalRecord, ...]:
        with self._lock, self._process_lock:
            try:
                content = self.path.read_bytes()
            except FileNotFoundError:
                return ()
            except OSError as error:
                raise MoveJournalError("could not read move journal") from error
        return tuple(self._parse_lines(content))

    @staticmethod
    def _parse_lines(content: bytes) -> Iterable[MoveJournalRecord]:
        lines = content.splitlines(keepends=True)
        for index, line in enumerate(lines):
            complete = line.endswith((b"\n", b"\r"))
            raw = line.rstrip(b"\r\n")
            if not raw:
                raise MoveJournalError(f"empty journal record at line {index + 1}")
            try:
                yield MoveJournalRecord.model_validate_json(raw)
            except (ValidationError, ValueError) as error:
                if index == len(lines) - 1 and not complete:
                    return
                raise MoveJournalError(
                    f"invalid journal record at line {index + 1}"
                ) from error

    def _restrict_permissions_best_effort(self) -> None:
        # chmod is not an ACL guarantee on Windows. Deployment must apply an ACL.
        with suppress(OSError):
            self.path.chmod(0o600)


class MoveRecoveryAnalyzer:
    """Classify interrupted moves without modifying source or destination files."""

    _TERMINAL_STATES = {
        MoveState.COMMITTED,
        MoveState.ROLLED_BACK,
        MoveState.AMBIGUOUS,
    }

    def __init__(self, journal: DurableMoveJournal) -> None:
        self._journal = journal

    def recover(self) -> tuple[MoveJournalRecord, ...]:
        latest: dict[UUID, MoveJournalRecord] = {}
        for record in self._journal.load():
            latest[record.transaction_id] = record

        recovered: list[MoveJournalRecord] = []
        for record in latest.values():
            if record.state in self._TERMINAL_STATES:
                continue
            terminal = self._classify(record)
            self._journal.append(terminal)
            recovered.append(terminal)
        return tuple(recovered)

    @staticmethod
    def _classify(record: MoveJournalRecord) -> MoveJournalRecord:
        source_exists = record.source.is_file()
        destination_exists = record.destination.is_file()
        state = MoveState.AMBIGUOUS
        reason = "RECOVERY_STATE_AMBIGUOUS"
        try:
            if source_exists and not destination_exists:
                if file_sha256(record.source) == record.source_hash:
                    state = MoveState.ROLLED_BACK
                    reason = "RECOVERY_NO_MOVE_OBSERVED"
            elif (
                not source_exists
                and destination_exists
                and file_sha256(record.destination) == record.source_hash
            ):
                state = MoveState.COMMITTED
                reason = "RECOVERY_MOVE_VERIFIED"
        except OSError:
            reason = "RECOVERY_INSPECTION_FAILED"
        return record.model_copy(update={"state": state, "reason": reason})
