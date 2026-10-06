from __future__ import annotations

import json
from pathlib import Path

from pydantic import Field, TypeAdapter

from .intent import GateRoute, IntentEvidence, IntentGate
from .models import FrozenModel


class IntentScenario(FrozenModel):
    scenario_id: str = Field(min_length=1)
    evidence: IntentEvidence
    task_active: bool = False
    expected_route: GateRoute
    description: str = Field(min_length=1)


class ScenarioFailure(FrozenModel):
    scenario_id: str
    expected_route: GateRoute
    actual_route: GateRoute
    reason: str


class ScenarioReport(FrozenModel):
    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    accuracy: float | None
    failures: tuple[ScenarioFailure, ...]


def load_intent_scenarios(path: Path) -> tuple[IntentScenario, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        scenarios = tuple(TypeAdapter(list[IntentScenario]).validate_python(raw))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("invalid intent scenario file") from exc
    identifiers = [scenario.scenario_id for scenario in scenarios]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("scenario_id values must be unique")
    return scenarios


def run_intent_scenarios(
    scenarios: tuple[IntentScenario, ...],
    gate: IntentGate | None = None,
) -> ScenarioReport:
    evaluator = gate or IntentGate()
    failures: list[ScenarioFailure] = []
    for scenario in scenarios:
        result = evaluator.evaluate(scenario.evidence, task_active=scenario.task_active)
        if result.route is not scenario.expected_route:
            failures.append(
                ScenarioFailure(
                    scenario_id=scenario.scenario_id,
                    expected_route=scenario.expected_route,
                    actual_route=result.route,
                    reason=result.reason,
                )
            )
    total = len(scenarios)
    passed = total - len(failures)
    return ScenarioReport(
        total=total,
        passed=passed,
        accuracy=passed / total if total else None,
        failures=tuple(failures),
    )

