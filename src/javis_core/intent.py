from __future__ import annotations

import unicodedata
from enum import StrEnum
from typing import Literal

from pydantic import Field

from .models import FrozenModel


class IntentClass(StrEnum):
    COMMAND = "COMMAND"
    QUESTION = "QUESTION"
    CHAT = "CHAT"
    APPROVAL = "APPROVAL"
    DENIAL = "DENIAL"
    CANCEL = "CANCEL"
    NON_TARGET = "NON_TARGET"


class GateRoute(StrEnum):
    PLAN = "PLAN"
    RESPOND = "RESPOND"
    APPROVAL = "APPROVAL"
    DENIAL = "DENIAL"
    CANCEL = "CANCEL"
    CLARIFY = "CLARIFY"
    IGNORE = "IGNORE"


InputSource = Literal["MIC", "TRUSTED_TEXT", "UNTRUSTED_CONTENT"]


class IntentEvidence(FrozenModel):
    transcript: str = Field(min_length=1)
    wake_confidence: float = Field(ge=0.0, le=1.0)
    addressed_confidence: float = Field(ge=0.0, le=1.0)
    intent_confidence: float = Field(ge=0.0, le=1.0)
    target_confidence: float = Field(ge=0.0, le=1.0)
    intent_class: IntentClass
    explicit_wake_word: bool = False
    quoted_command: bool = False
    negated_command: bool = False
    hypothetical: bool = False
    source: InputSource = "MIC"


class GateResult(FrozenModel):
    route: GateRoute
    accepted: bool
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str


class IntentGate:
    def evaluate(self, evidence: IntentEvidence, *, task_active: bool) -> GateResult:
        if evidence.source == "UNTRUSTED_CONTENT":
            return self._result(GateRoute.IGNORE, False, 0.0, "UNTRUSTED_SOURCE")
        if evidence.intent_class is IntentClass.CANCEL:
            if task_active and evidence.intent_confidence >= 0.75:
                return self._result(
                    GateRoute.CANCEL,
                    True,
                    evidence.intent_confidence,
                    "LOCAL_STOP_ACCEPTED",
                )
            return self._result(
                GateRoute.IGNORE,
                False,
                evidence.intent_confidence,
                "NO_ACTIVE_TASK_OR_LOW_CONFIDENCE",
            )
        if evidence.intent_class is IntentClass.NON_TARGET:
            return self._result(
                GateRoute.IGNORE,
                False,
                evidence.addressed_confidence,
                "NOT_ADDRESSED",
            )
        if evidence.intent_class is IntentClass.COMMAND and (
            evidence.quoted_command or evidence.negated_command or evidence.hypothetical
        ):
            return self._result(
                GateRoute.IGNORE,
                False,
                self._command_confidence(evidence),
                "NON_EXECUTABLE_SPEECH_ACT",
            )
        if evidence.intent_class in (IntentClass.APPROVAL, IntentClass.DENIAL):
            confidence = min(evidence.addressed_confidence, evidence.intent_confidence)
            if not task_active:
                return self._result(
                    GateRoute.IGNORE,
                    False,
                    confidence,
                    "NO_PENDING_APPROVAL",
                )
            if confidence < 0.90:
                return self._result(
                    GateRoute.CLARIFY,
                    False,
                    confidence,
                    "UNCERTAIN_APPROVAL_RESPONSE",
                )
            route = (
                GateRoute.APPROVAL
                if evidence.intent_class is IntentClass.APPROVAL
                else GateRoute.DENIAL
            )
            return self._result(route, True, confidence, "APPROVAL_RESPONSE_ACCEPTED")
        if evidence.intent_class is IntentClass.COMMAND:
            confidence = self._command_confidence(evidence)
            if confidence < 0.75:
                return self._result(
                    GateRoute.IGNORE,
                    False,
                    confidence,
                    "LOW_COMMAND_CONFIDENCE",
                )
            if confidence < 0.90:
                return self._result(
                    GateRoute.CLARIFY,
                    False,
                    confidence,
                    "UNCERTAIN_COMMAND",
                )
            return self._result(GateRoute.PLAN, True, confidence, "COMMAND_ACCEPTED")
        if evidence.intent_class in (IntentClass.QUESTION, IntentClass.CHAT):
            confidence = min(evidence.addressed_confidence, evidence.intent_confidence)
            if confidence >= 0.75:
                return self._result(GateRoute.RESPOND, True, confidence, "RESPONSE_ACCEPTED")
            return self._result(
                GateRoute.IGNORE,
                False,
                confidence,
                "LOW_RESPONSE_CONFIDENCE",
            )
        return self._result(GateRoute.IGNORE, False, 0.0, "UNSUPPORTED_INTENT")

    @staticmethod
    def _command_confidence(evidence: IntentEvidence) -> float:
        scores = [
            evidence.addressed_confidence,
            evidence.intent_confidence,
            evidence.target_confidence,
        ]
        if evidence.explicit_wake_word:
            scores.append(evidence.wake_confidence)
        return min(scores)

    @staticmethod
    def _result(
        route: GateRoute,
        accepted: bool,
        confidence: float,
        reason: str,
    ) -> GateResult:
        return GateResult(
            route=route,
            accepted=accepted,
            confidence=confidence,
            reason=reason,
        )


class LocalStopDetector:
    _PHRASES = frozenset({"멈춰", "취소", "중지", "자비스 스톱", "자비스 멈춰"})

    def matches(self, transcript: str) -> bool:
        return self.normalize(transcript) in self._PHRASES

    @staticmethod
    def normalize(transcript: str) -> str:
        normalized = unicodedata.normalize("NFKC", transcript).casefold()
        characters = (
            " " if unicodedata.category(character)[0] in {"P", "Z"} else character
            for character in normalized
        )
        return " ".join("".join(characters).split())

