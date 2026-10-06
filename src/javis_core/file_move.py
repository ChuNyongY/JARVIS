from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path
from time import perf_counter_ns
from typing import Protocol
from uuid import UUID, uuid4

from .cancellation import CancellationToken
from .models import Action, FrozenModel, Postcondition
from .runtime_models import ExecutionResult


class MoveState(StrEnum):
    PREPARED = "PREPARED"
    COMMITTED = "COMMITTED"
    ROLLED_BACK = "ROLLED_BACK"
    AMBIGUOUS = "AMBIGUOUS"


class MoveJournalRecord(FrozenModel):
    transaction_id: UUID
    source: Path
    destination: Path
    source_hash: str
    state: MoveState
    reason: str


class MoveJournal(Protocol):
    def append(self, record: MoveJournalRecord) -> None: ...


class InMemoryMoveJournal:
    def __init__(self) -> None:
        self._records: list[MoveJournalRecord] = []

    def append(self, record: MoveJournalRecord) -> None:
        self._records.append(record)

    @property
    def records(self) -> tuple[MoveJournalRecord, ...]:
        return tuple(self._records)


class FileMoveExecutor:
    name = "native"

    def __init__(self, allowed_roots: Iterable[Path], journal: MoveJournal) -> None:
        self._roots = tuple(root.resolve(strict=True) for root in allowed_roots)
        if any(not root.is_dir() for root in self._roots):
            raise ValueError("allowed roots must be existing directories")
        self._journal = journal

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        paths = self._validate(action)
        if isinstance(paths, str):
            return self._failure(action, started, paths, retry_safe=True)
        source, destination = paths
        source_hash = file_sha256(source)
        transaction_id = uuid4()
        self._record(transaction_id, source, destination, source_hash, MoveState.PREPARED, "READY")
        if cancellation is not None and cancellation.is_cancelled:
            self._record(
                transaction_id,
                source,
                destination,
                source_hash,
                MoveState.ROLLED_BACK,
                "STOP_BEFORE_MOVE",
            )
            return self._failure(action, started, "STOP_REQUESTED", retry_safe=False)
        try:
            os.rename(source, destination)
        except OSError:
            return self._resolve_after_error(
                action,
                started,
                transaction_id,
                source,
                destination,
                source_hash,
            )
        if self._move_verified(source, destination, source_hash):
            self._record(
                transaction_id,
                source,
                destination,
                source_hash,
                MoveState.COMMITTED,
                "MOVE_VERIFIED",
            )
            return self._success(action, started, transaction_id, source, destination, source_hash)
        self._record(
            transaction_id,
            source,
            destination,
            source_hash,
            MoveState.AMBIGUOUS,
            "POST_MOVE_VERIFICATION_FAILED",
        )
        return self._failure(action, started, "MOVE_STATE_AMBIGUOUS", retry_safe=False)

    def _validate(self, action: Action) -> tuple[Path, Path] | str:
        if action.capability_id != "file.move":
            return "UNSUPPORTED_CAPABILITY"
        if set(action.arguments) != {"source", "destination"}:
            return "INVALID_ARGUMENTS"
        raw_source = action.arguments.get("source")
        raw_destination = action.arguments.get("destination")
        if not isinstance(raw_source, str) or not isinstance(raw_destination, str):
            return "INVALID_ARGUMENTS"
        source_input = Path(raw_source)
        destination_input = Path(raw_destination)
        if source_input.is_symlink() or not destination_input.is_absolute():
            return "UNSAFE_PATH"
        try:
            source = source_input.resolve(strict=True)
            destination_parent = destination_input.parent.resolve(strict=True)
        except OSError:
            return "PATH_NOT_FOUND"
        destination = destination_parent / destination_input.name
        if not source.is_file() or destination.exists():
            return "SOURCE_OR_DESTINATION_INVALID"
        if not self._inside_allowed_root(source) or not self._inside_allowed_root(
            destination_parent
        ):
            return "PATH_OUTSIDE_ALLOWED_ROOTS"
        if action.target_identity != str(source):
            return "TARGET_IDENTITY_MISMATCH"
        if source.drive.casefold() != destination.drive.casefold():
            return "CROSS_VOLUME_MOVE_DENIED"
        return source, destination

    def _resolve_after_error(
        self,
        action: Action,
        started: int,
        transaction_id: UUID,
        source: Path,
        destination: Path,
        source_hash: str,
    ) -> ExecutionResult:
        if self._move_verified(source, destination, source_hash):
            self._record(
                transaction_id,
                source,
                destination,
                source_hash,
                MoveState.COMMITTED,
                "MOVE_VERIFIED_AFTER_ERROR",
            )
            return self._success(action, started, transaction_id, source, destination, source_hash)
        if source.exists() and not destination.exists():
            self._record(
                transaction_id,
                source,
                destination,
                source_hash,
                MoveState.ROLLED_BACK,
                "NO_MOVE_OBSERVED",
            )
            return self._failure(action, started, "MOVE_FAILED_NO_CHANGE", retry_safe=True)
        self._record(
            transaction_id,
            source,
            destination,
            source_hash,
            MoveState.AMBIGUOUS,
            "MOVE_ERROR_AMBIGUOUS",
        )
        return self._failure(action, started, "MOVE_STATE_AMBIGUOUS", retry_safe=False)

    def _record(
        self,
        transaction_id: UUID,
        source: Path,
        destination: Path,
        source_hash: str,
        state: MoveState,
        reason: str,
    ) -> None:
        self._journal.append(
            MoveJournalRecord(
                transaction_id=transaction_id,
                source=source,
                destination=destination,
                source_hash=source_hash,
                state=state,
                reason=reason,
            )
        )

    def _inside_allowed_root(self, path: Path) -> bool:
        return any(path.is_relative_to(root) for root in self._roots)

    @staticmethod
    def _move_verified(source: Path, destination: Path, expected_hash: str) -> bool:
        try:
            return (
                not source.exists()
                and destination.is_file()
                and file_sha256(destination) == expected_hash
            )
        except OSError:
            return False

    @staticmethod
    def _success(
        action: Action,
        started: int,
        transaction_id: UUID,
        source: Path,
        destination: Path,
        source_hash: str,
    ) -> ExecutionResult:
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=True,
            observed={
                "transaction_id": str(transaction_id),
                "source": str(source),
                "destination": str(destination),
                "source_hash": source_hash,
            },
            duration_ms=max(0, (perf_counter_ns() - started) // 1_000_000),
        )

    @staticmethod
    def _failure(
        action: Action,
        started: int,
        error_code: str,
        *,
        retry_safe: bool,
    ) -> ExecutionResult:
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=False,
            error_code=error_code,
            error_message="File move was not completed",
            retry_safe=retry_safe,
            duration_ms=max(0, (perf_counter_ns() - started) // 1_000_000),
        )


class FileMovedPredicate:
    def __call__(self, postcondition: Postcondition, result: ExecutionResult) -> bool:
        if postcondition.arguments:
            return False
        source = result.observed.get("source")
        destination = result.observed.get("destination")
        expected_hash = result.observed.get("source_hash")
        if (
            not isinstance(source, str)
            or not isinstance(destination, str)
            or not isinstance(expected_hash, str)
        ):
            return False
        try:
            return (
                not Path(source).exists()
                and Path(destination).is_file()
                and file_sha256(Path(destination)) == expected_hash
            )
        except OSError:
            return False


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
