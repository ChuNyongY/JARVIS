from __future__ import annotations

from .models import Action, Decision, PolicyContext, PolicyDecision, RiskLevel
from .registry import CapabilityRegistry


class PolicyEngine:
    def __init__(self, registry: CapabilityRegistry) -> None:
        self._registry = registry

    def evaluate(self, action: Action, context: PolicyContext) -> PolicyDecision:
        capability = self._registry.get(action.capability_id)
        if capability is None:
            return self._deny("UNKNOWN_CAPABILITY")
        if context.suspected_prompt_injection:
            return self._deny("PROMPT_INJECTION_SUSPECTED")
        if not context.target_matches_plan:
            return self._deny("TARGET_MISMATCH")

        gate_confidence = min(
            context.addressed_confidence,
            context.intent_confidence,
            context.target_confidence,
        )
        if gate_confidence < 0.75:
            return self._deny("LOW_GATE_CONFIDENCE")

        risk = capability.risk_floor
        reasons = [f"CAPABILITY_FLOOR_{risk.name}"]
        if context.requested_risk is not None:
            risk = max(risk, context.requested_risk)
        if gate_confidence < 0.90:
            risk = max(risk, RiskLevel.R2)
            reasons.append("UNCERTAIN_GATE")

        elevations = (
            (context.privilege_escalation, "PRIVILEGE_ESCALATION"),
            (context.external_transmission, "EXTERNAL_TRANSMISSION"),
            (context.sensitive_data, "SENSITIVE_DATA"),
            (capability.external_effect, "EXTERNAL_EFFECT_CAPABILITY"),
            (capability.sensitive, "SENSITIVE_CAPABILITY"),
        )
        for active, reason in elevations:
            if active:
                risk = max(risk, RiskLevel.R3)
                reasons.append(reason)

        if risk <= RiskLevel.R1:
            decision = Decision.GREEN
        elif risk == RiskLevel.R2:
            decision = Decision.YELLOW
        else:
            decision = Decision.RED

        return PolicyDecision(
            decision=decision,
            risk=risk,
            reasons=tuple(dict.fromkeys(reasons)),
            requires_strong_auth=risk == RiskLevel.R4,
        )

    @staticmethod
    def _deny(reason: str) -> PolicyDecision:
        return PolicyDecision(
            decision=Decision.DENY,
            risk=RiskLevel.R4,
            reasons=(reason,),
            requires_strong_auth=False,
        )

