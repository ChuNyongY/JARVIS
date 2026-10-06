from __future__ import annotations

from collections.abc import Callable

from .intent import GateResult, GateRoute
from .models import Action, PolicyContext

PolicyContextProvider = Callable[[Action], PolicyContext]


def gate_scoped_policy_context_provider(
    provider: PolicyContextProvider,
    gate: GateResult | None,
) -> PolicyContextProvider:
    """Cap policy confidence at the accepted PLAN gate without retaining text."""
    if gate is None or gate.route is not GateRoute.PLAN or not gate.accepted:
        raise ValueError("accepted PLAN gate is required for policy evaluation")
    confidence = gate.confidence

    def scoped(action: Action) -> PolicyContext:
        context = provider(action)
        return context.model_copy(
            update={
                "addressed_confidence": min(
                    context.addressed_confidence,
                    confidence,
                ),
                "intent_confidence": min(context.intent_confidence, confidence),
                "target_confidence": min(context.target_confidence, confidence),
            }
        )

    return scoped
