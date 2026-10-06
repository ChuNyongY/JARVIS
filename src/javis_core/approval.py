from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from .models import (
    Action,
    ApprovalClaims,
    Decision,
    Plan,
    PolicyDecision,
    canonical_arguments_hash,
)


class ApprovalError(ValueError):
    pass


class ApprovalTokenService:
    def __init__(
        self,
        secret: bytes,
        *,
        ttl: timedelta = timedelta(seconds=120),
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("approval secret must contain at least 32 bytes")
        if ttl <= timedelta(0):
            raise ValueError("approval TTL must be positive")
        self._secret = secret
        self._ttl = ttl
        self._now = now or (lambda: datetime.now(UTC))
        self._used_nonces: set[str] = set()
        self._assert_aware(self._now())

    def issue(self, plan: Plan, action: Action, decision: PolicyDecision) -> str:
        self._assert_action_in_plan(plan, action)
        if decision.decision is not Decision.RED:
            raise ApprovalError("approval tokens are issued only for RED actions")
        issued_at = self._now()
        self._assert_aware(issued_at)
        claims = ApprovalClaims(
            task_id=plan.task_id,
            action_id=action.action_id,
            capability_id=action.capability_id,
            arguments_hash=canonical_arguments_hash(action.arguments),
            target_identity=action.target_identity,
            risk=decision.risk,
            issued_at=issued_at,
            expires_at=issued_at + self._ttl,
        )
        payload = self._canonical_payload(claims)
        signature = hmac.new(self._secret, payload, hashlib.sha256).digest()
        return f"{self._encode(payload)}.{self._encode(signature)}"

    def verify(
        self,
        token: str,
        plan: Plan,
        action: Action,
        decision: PolicyDecision,
        *,
        consume: bool = True,
    ) -> ApprovalClaims:
        self._assert_action_in_plan(plan, action)
        try:
            payload_part, signature_part = token.split(".", maxsplit=1)
            payload = self._decode(payload_part)
            supplied_signature = self._decode(signature_part)
        except (ValueError, UnicodeError) as exc:
            raise ApprovalError("malformed approval token") from exc

        expected_signature = hmac.new(self._secret, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ApprovalError("invalid approval signature")
        try:
            claims = ApprovalClaims.model_validate_json(payload)
        except ValueError as exc:
            raise ApprovalError("invalid approval claims") from exc

        now = self._now()
        self._assert_aware(now)
        if now >= claims.expires_at:
            raise ApprovalError("approval token expired")
        expected = (
            claims.task_id == plan.task_id
            and claims.action_id == action.action_id
            and claims.capability_id == action.capability_id
            and claims.arguments_hash == canonical_arguments_hash(action.arguments)
            and claims.target_identity == action.target_identity
            and claims.risk == decision.risk
            and decision.decision is Decision.RED
        )
        if not expected:
            raise ApprovalError("approval does not match the action")

        nonce = str(claims.nonce)
        if nonce in self._used_nonces:
            raise ApprovalError("approval token already used")
        if consume:
            self._used_nonces.add(nonce)
        return claims

    @staticmethod
    def _assert_action_in_plan(plan: Plan, action: Action) -> None:
        if not any(step == action for step in plan.steps):
            raise ApprovalError("action is not an exact member of the plan")

    @staticmethod
    def _assert_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return a timezone-aware datetime")

    @staticmethod
    def _canonical_payload(claims: ApprovalClaims) -> bytes:
        return json.dumps(
            claims.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

    @staticmethod
    def _decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)
