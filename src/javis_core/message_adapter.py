from __future__ import annotations

import hashlib
import re
import threading
from time import perf_counter_ns
from typing import Literal, Protocol
from uuid import uuid4

from .cancellation import CancellationToken
from .models import Action, VerificationResult
from .runtime_models import ExecutionResult

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.:-]{2,128}$")
MessageKind = Literal["draft", "send"]


class MessageSink(Protocol):
    external_effect: bool

    def draft(self, recipient: str, body: str) -> str: ...

    def send(self, recipient: str, body: str) -> str: ...

    def verify(
        self,
        kind: MessageKind,
        receipt_id: str,
        recipient_sha256: str,
        body_sha256: str,
    ) -> bool: ...


class InMemoryMessageSink:
    """Bounded mock sink that retains hashes but never message content."""

    external_effect = False

    def __init__(self, *, max_records: int = 1000) -> None:
        if not 1 <= max_records <= 10_000:
            raise ValueError("max_records must be between 1 and 10000")
        self._max_records = max_records
        self._records: dict[str, tuple[MessageKind, str, str]] = {}
        self._lock = threading.Lock()

    def draft(self, recipient: str, body: str) -> str:
        return self._record("draft", recipient, body)

    def send(self, recipient: str, body: str) -> str:
        return self._record("send", recipient, body)

    def verify(
        self,
        kind: MessageKind,
        receipt_id: str,
        recipient_sha256: str,
        body_sha256: str,
    ) -> bool:
        with self._lock:
            return self._records.get(receipt_id) == (kind, recipient_sha256, body_sha256)

    def _record(self, kind: MessageKind, recipient: str, body: str) -> str:
        record = (kind, _digest(recipient), _digest(body))
        with self._lock:
            if len(self._records) >= self._max_records:
                raise RuntimeError("message sink capacity reached")
            receipt_id = uuid4().hex
            self._records[receipt_id] = record
        return receipt_id

    @property
    def record_count(self) -> int:
        with self._lock:
            return len(self._records)


class MessageExecutor:
    name = "native"

    def __init__(self, sink: MessageSink, *, allow_external: bool = False) -> None:
        if type(sink.external_effect) is not bool:
            raise ValueError("message sink external_effect must be boolean")
        if sink.external_effect and not allow_external:
            raise ValueError("external message sink requires explicit opt-in")
        self._sink = sink

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        validated = self._validate(action)
        if isinstance(validated, str):
            return self._failure(action, started, validated, retry_safe=True)
        kind, recipient, body, recipient_hash, body_hash = validated
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED", retry_safe=True)

        try:
            receipt_id = (
                self._sink.draft(recipient, body)
                if kind == "draft"
                else self._sink.send(recipient, body)
            )
        except Exception:
            return self._failure(
                action,
                started,
                "MESSAGE_SEND_STATE_UNKNOWN" if kind == "send" else "MESSAGE_DRAFT_FAILED",
                retry_safe=kind == "draft" and not self._sink.external_effect,
            )
        if not isinstance(receipt_id, str) or not _SAFE_ID.fullmatch(receipt_id):
            return self._failure(
                action,
                started,
                "MESSAGE_SEND_STATE_UNKNOWN" if kind == "send" else "MESSAGE_DRAFT_FAILED",
                retry_safe=kind == "draft" and not self._sink.external_effect,
            )
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(
                action,
                started,
                "STOP_REQUESTED_AFTER_INPUT",
                retry_safe=kind == "draft" and not self._sink.external_effect,
            )
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=True,
            observed={
                "body_sha256": body_hash,
                "delivery_mode": "external" if self._sink.external_effect else "mock",
                "kind": kind,
                "receipt_id": receipt_id,
                "recipient_sha256": recipient_hash,
            },
            duration_ms=self._elapsed_ms(started),
        )

    @staticmethod
    def _validate(action: Action) -> tuple[MessageKind, str, str, str, str] | str:
        kinds: dict[str, MessageKind] = {
            "message.draft": "draft",
            "message.send": "send",
        }
        kind = kinds.get(action.capability_id)
        if kind is None:
            return "UNSUPPORTED_CAPABILITY"
        if set(action.arguments) != {"recipient", "body"}:
            return "INVALID_ARGUMENTS"
        recipient = action.arguments.get("recipient")
        body = action.arguments.get("body")
        if (
            not isinstance(recipient, str)
            or not isinstance(body, str)
            or not 1 <= len(recipient) <= 256
            or not 1 <= len(body) <= 4000
            or any(character in recipient for character in ("\x00", "\r", "\n"))
            or "\x00" in body
        ):
            return "INVALID_ARGUMENTS"
        recipient_hash = _digest(recipient)
        body_hash = _digest(body)
        if action.target_identity != f"recipient-sha256:{recipient_hash}":
            return "TARGET_IDENTITY_MISMATCH"
        expected_predicate = "message.drafted" if kind == "draft" else "message.sent"
        if len(action.expected_postconditions) != 1:
            return "INVALID_POSTCONDITION"
        postcondition = action.expected_postconditions[0]
        if postcondition.predicate != expected_predicate or postcondition.arguments:
            return "INVALID_POSTCONDITION"
        return kind, recipient, body, recipient_hash, body_hash

    @staticmethod
    def _elapsed_ms(started: int) -> int:
        return max(0, (perf_counter_ns() - started) // 1_000_000)

    @classmethod
    def _failure(
        cls,
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
            error_message="Message action was not completed",
            retry_safe=retry_safe,
            duration_ms=cls._elapsed_ms(started),
        )


class MessageReceiptVerifier:
    def __init__(self, sink: MessageSink) -> None:
        self._sink = sink

    def verify(self, action: Action, result: ExecutionResult) -> VerificationResult:
        if result.action_id != action.action_id:
            return self._failure("ACTION_ID_MISMATCH")
        if result.executor != "native":
            return self._failure("EXECUTOR_MISMATCH")
        if not result.success:
            return self._failure(result.error_code or "EXECUTION_FAILED")
        validated = MessageExecutor._validate(action)
        if isinstance(validated, str):
            return self._failure("MESSAGE_VERIFY_CONTRACT_FAILED")
        kind, _recipient, _body, recipient_hash, body_hash = validated
        receipt_id = result.observed.get("receipt_id")
        delivery_mode = "external" if self._sink.external_effect else "mock"
        if (
            not isinstance(receipt_id, str)
            or result.observed.get("kind") != kind
            or result.observed.get("recipient_sha256") != recipient_hash
            or result.observed.get("body_sha256") != body_hash
            or result.observed.get("delivery_mode") != delivery_mode
        ):
            return self._failure("MESSAGE_VERIFY_BINDING_FAILED")
        try:
            verified = self._sink.verify(kind, receipt_id, recipient_hash, body_hash)
        except Exception:
            verified = False
        if not verified:
            return self._failure("MESSAGE_RECEIPT_NOT_VERIFIED")
        return VerificationResult(
            success=True,
            observed={
                "body_sha256": body_hash,
                "delivery_mode": delivery_mode,
                "kind": kind,
                "recipient_sha256": recipient_hash,
            },
            reason="MESSAGE_RECEIPT_VERIFIED",
        )

    @staticmethod
    def _failure(reason: str) -> VerificationResult:
        return VerificationResult(success=False, reason=reason, recoverable=False)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
