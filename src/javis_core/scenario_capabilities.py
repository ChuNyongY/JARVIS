from __future__ import annotations

from pydantic import Field, model_validator

from .integrated_scenarios import IntegratedScenarioManifest
from .models import FrozenModel
from .registry import CapabilityRegistry

LOCAL_CONTROL_CAPABILITIES = frozenset(
    {"intent.addressedness_gate", "intent.untrusted_content_gate"}
)


class ScenarioCapabilityCoverage(FrozenModel):
    required_count: int = Field(ge=1)
    registered_action_capabilities: tuple[str, ...]
    local_control_capabilities: tuple[str, ...]
    missing_capabilities: tuple[str, ...]
    catalog_complete: bool

    @model_validator(mode="after")
    def consistent_summary(self) -> ScenarioCapabilityCoverage:
        covered = set(self.registered_action_capabilities) | set(
            self.local_control_capabilities
        )
        if self.required_count != len(covered) + len(self.missing_capabilities):
            raise ValueError("scenario capability coverage count is inconsistent")
        if self.catalog_complete != (not self.missing_capabilities):
            raise ValueError("scenario capability coverage status is inconsistent")
        return self


def audit_scenario_capabilities(
    manifest: IntegratedScenarioManifest,
    registry: CapabilityRegistry,
) -> ScenarioCapabilityCoverage:
    required = {
        capability
        for scenario in manifest.scenarios
        for capability in scenario.required_capabilities
    }
    registered = {item.capability_id for item in registry.list()} & required
    controls = LOCAL_CONTROL_CAPABILITIES & required
    overlap = registered & controls
    if overlap:
        raise ValueError("local controls must not also be executable action capabilities")
    missing = required - registered - controls
    return ScenarioCapabilityCoverage(
        required_count=len(required),
        registered_action_capabilities=tuple(sorted(registered)),
        local_control_capabilities=tuple(sorted(controls)),
        missing_capabilities=tuple(sorted(missing)),
        catalog_complete=not missing,
    )
