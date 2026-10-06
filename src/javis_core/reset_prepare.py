from __future__ import annotations

import hashlib
import re
import secrets
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import perf_counter_ns
from uuid import UUID

from pydantic import JsonValue

from .cancellation import CancellationToken
from .models import Action, VerificationResult, canonical_arguments_hash
from .runtime_models import ExecutionResult

_VM_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


class ResetPreparationStore:
    """Bounded volatile receipt store; it has no OS reset capability."""

    def __init__(self, *, max_receipts: int = 128) -> None:
        if not 1 <= max_receipts <= 10_000:
            raise ValueError("invalid reset receipt limit")
        self._max_receipts = max_receipts
        self._items: dict[UUID, tuple[str, str, datetime]] = {}
        self._lock = threading.Lock()

    def put(self, action: Action, receipt: str, expires_at: datetime) -> None:
        with self._lock:
            if action.action_id not in self._items and len(self._items) >= self._max_receipts:
                raise RuntimeError("reset receipt store is full")
            self._items[action.action_id] = (
                receipt,
                canonical_arguments_hash(action.arguments),
                expires_at,
            )

    def matches(self, action: Action, receipt: str, *, now: datetime) -> bool:
        with self._lock:
            stored = self._items.get(action.action_id)
        return bool(
            stored is not None
            and secrets.compare_digest(stored[0], receipt)
            and secrets.compare_digest(stored[1], canonical_arguments_hash(action.arguments))
            and stored[2] > now
        )


class ResetPrepareExecutor:
    name = "native"

    def __init__(
        self,
        store: ResetPreparationStore,
        *,
        vm_id: str,
        ttl: timedelta = timedelta(minutes=5),
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if _VM_ID.fullmatch(vm_id) is None or not (
            timedelta(seconds=1) <= ttl <= timedelta(minutes=15)
        ):
            raise ValueError("invalid reset preparation configuration")
        self._store = store
        self._target = f"snapshot-vm:{vm_id}"
        self._ttl = ttl
        self._clock = clock

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        if not self._valid_action(action):
            return self._failure(action, started, "INVALID_ARGUMENTS")
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED")
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            return self._failure(action, started, "RESET_CLOCK_INVALID")
        nonce = secrets.token_bytes(32)
        receipt = hashlib.sha256(
            action.action_id.bytes
            + canonical_arguments_hash(action.arguments).encode()
            + self._target.encode()
            + nonce
        ).hexdigest()
        expires_at = now + self._ttl
        try:
            self._store.put(action, receipt, expires_at)
        except Exception:
            return self._failure(action, started, "RESET_PREPARE_FAILED")
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=True,
            observed={
                "preparation_receipt": receipt,
                "expires_at": expires_at.isoformat(),
                "automatic_commit": False,
                "os_mutation_count": 0,
            },
            duration_ms=self._elapsed_ms(started),
        )

    def _valid_action(self, action: Action) -> bool:
        keep_files = action.arguments.get("keep_files")
        if (
            action.capability_id != "system.reset.prepare"
            or set(action.arguments) != {"keep_files"}
            or type(keep_files) is not bool
            or action.target_identity != self._target
            or len(action.expected_postconditions) != 1
        ):
            return False
        postcondition = action.expected_postconditions[0]
        return postcondition.predicate == "reset.prepared" and not postcondition.arguments

    @staticmethod
    def _elapsed_ms(started: int) -> int:
        return max(0, (perf_counter_ns() - started) // 1_000_000)

    @classmethod
    def _failure(cls, action: Action, started: int, code: str) -> ExecutionResult:
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=False,
            error_code=code,
            error_message="Reset preparation was not completed",
            retry_safe=True,
            duration_ms=cls._elapsed_ms(started),
        )


class ResetPreparationVerifier:
    def __init__(
        self,
        executor: ResetPrepareExecutor,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._executor = executor
        self._clock = clock

    def verify(self, action: Action, result: ExecutionResult) -> VerificationResult:
        if result.action_id != action.action_id or result.executor != "native":
            return self._failure("RESET_RESULT_BINDING_MISMATCH")
        if not result.success:
            return self._failure(result.error_code or "EXECUTION_FAILED")
        if not self._executor._valid_action(action):  # noqa: SLF001
            return self._failure("INVALID_RESET_PREPARE_CONTRACT")
        receipt = result.observed.get("preparation_receipt")
        if (
            not isinstance(receipt, str)
            or re.fullmatch(r"[0-9a-f]{64}", receipt) is None
            or result.observed.get("automatic_commit") is not False
            or result.observed.get("os_mutation_count") != 0
        ):
            return self._failure("RESET_PREPARATION_RECEIPT_INVALID")
        try:
            matched = self._executor._store.matches(  # noqa: SLF001
                action,
                receipt,
                now=self._clock(),
            )
        except Exception:
            matched = False
        if not matched:
            return self._failure("RESET_PREPARATION_NOT_FOUND")
        observed: dict[str, JsonValue] = {
            "preparation_receipt": receipt,
            "automatic_commit": False,
            "os_mutation_count": 0,
        }
        return VerificationResult(
            success=True,
            observed=observed,
            reason="RESET_PREPARED_NO_COMMIT",
        )

    @staticmethod
    def _failure(reason: str) -> VerificationResult:
        return VerificationResult(success=False, reason=reason, recoverable=False)
