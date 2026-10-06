from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType

from .models import Action, VerificationResult
from .runtime_models import ExecutionResult
from .verifier import Verifier

_CAPABILITY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class CapabilityVerifierRouter:
    """Route an exact capability ID to one verifier and fail closed."""

    def __init__(self, routes: Mapping[str, Verifier]) -> None:
        if not isinstance(routes, Mapping) or not routes:
            raise ValueError("verifier routes must be a nonempty mapping")
        validated: dict[str, Verifier] = {}
        for capability_id, verifier in routes.items():
            if (
                not isinstance(capability_id, str)
                or _CAPABILITY_ID.fullmatch(capability_id) is None
            ):
                raise ValueError("invalid verifier capability route")
            if not callable(getattr(verifier, "verify", None)):
                raise TypeError("verifier route does not implement verify")
            validated[capability_id] = verifier
        self._routes: Mapping[str, Verifier] = MappingProxyType(validated)

    def verify(self, action: Action, result: ExecutionResult) -> VerificationResult:
        verifier = self._routes.get(action.capability_id)
        if verifier is None:
            return self._failure("UNSUPPORTED_CAPABILITY")
        try:
            verification = verifier.verify(action, result)
        except Exception:
            return self._failure("VERIFIER_FAILED")
        if not isinstance(verification, VerificationResult):
            return self._failure("INVALID_VERIFIER_RESULT")
        return verification

    @staticmethod
    def _failure(reason: str) -> VerificationResult:
        return VerificationResult(
            success=False,
            observed={},
            reason=reason,
            recoverable=False,
        )
