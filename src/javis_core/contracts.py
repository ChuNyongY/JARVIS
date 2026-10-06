from __future__ import annotations

import re
from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

from pydantic import JsonValue

from .models import Action, Plan
from .registry import CapabilityRegistry

ArgumentType = Literal["string", "boolean", "integer"]


class PlanContractError(ValueError):
    pass


class ArgumentContract:
    def __init__(
        self,
        required: Mapping[str, ArgumentType] | None = None,
        optional: Mapping[str, ArgumentType] | None = None,
        *,
        target_required: bool = False,
        string_choices: Mapping[str, tuple[str, ...]] | None = None,
        integer_ranges: Mapping[str, tuple[int, int]] | None = None,
        allowed_postconditions: tuple[str, ...] | None = None,
    ) -> None:
        self.required = MappingProxyType(dict(required or {}))
        self.optional = MappingProxyType(dict(optional or {}))
        overlap = set(self.required) & set(self.optional)
        if overlap:
            raise ValueError("argument keys cannot be both required and optional")
        choices = {key: tuple(values) for key, values in (string_choices or {}).items()}
        declared = {**self.required, **self.optional}
        if any(
            key not in declared
            or declared[key] != "string"
            or not values
            or len(values) != len(set(values))
            or any(not value for value in values)
            for key, values in choices.items()
        ):
            raise ValueError("string choices must be unique values for declared string fields")
        self.string_choices = MappingProxyType(choices)
        ranges = dict(integer_ranges or {})
        if any(
            key not in declared
            or declared[key] != "integer"
            or type(bounds[0]) is not int
            or type(bounds[1]) is not int
            or bounds[0] > bounds[1]
            for key, bounds in ranges.items()
        ):
            raise ValueError("integer ranges must bound declared integer fields")
        self.integer_ranges = MappingProxyType(ranges)
        self.target_required = target_required
        if allowed_postconditions is not None and (
            not allowed_postconditions
            or len(allowed_postconditions) != len(set(allowed_postconditions))
            or any(not value for value in allowed_postconditions)
        ):
            raise ValueError("allowed postconditions must be unique nonempty names")
        self.allowed_postconditions = allowed_postconditions

    def validate(self, action: Action) -> None:
        keys = set(action.arguments)
        missing = set(self.required) - keys
        extra = keys - set(self.required) - set(self.optional)
        if missing:
            raise PlanContractError(
                f"{action.capability_id} missing required arguments: {sorted(missing)}"
            )
        if extra:
            raise PlanContractError(
                f"{action.capability_id} contains forbidden arguments: {sorted(extra)}"
            )
        for key, expected in {**self.required, **self.optional}.items():
            if key in action.arguments:
                self._validate_type(action.capability_id, key, action.arguments[key], expected)
                choices = self.string_choices.get(key)
                if choices is not None and action.arguments[key] not in choices:
                    raise PlanContractError(
                        f"{action.capability_id}.{key} is not an allowed value"
                    )
                bounds = self.integer_ranges.get(key)
                if bounds is not None:
                    value = action.arguments[key]
                    if type(value) is not int or not bounds[0] <= value <= bounds[1]:
                        raise PlanContractError(
                            f"{action.capability_id}.{key} is outside the allowed range"
                        )
        if self.target_required and not action.target_identity:
            raise PlanContractError(f"{action.capability_id} requires target_identity")
        if self.allowed_postconditions is not None and any(
            postcondition.predicate not in self.allowed_postconditions
            for postcondition in action.expected_postconditions
        ):
            raise PlanContractError(
                f"{action.capability_id} contains a forbidden postcondition"
            )

    @staticmethod
    def _validate_type(
        capability_id: str,
        key: str,
        value: JsonValue,
        expected: ArgumentType,
    ) -> None:
        valid = (
            (expected == "string" and isinstance(value, str) and bool(value))
            or (expected == "boolean" and type(value) is bool)
            or (expected == "integer" and type(value) is int)
        )
        if not valid:
            raise PlanContractError(
                f"{capability_id}.{key} must be a nonempty {expected}"
            )


def default_argument_contracts() -> Mapping[str, ArgumentContract]:
    return MappingProxyType(
        {
            "system.info.read": ArgumentContract(
                allowed_postconditions=(
                    "execution.success",
                    "observed.contains",
                    "observed.equals",
                )
            ),
            "process.launch": ArgumentContract(
                {"app_id": "string"},
                target_required=True,
                allowed_postconditions=("process.exists", "window.identity.matches"),
            ),
            "explorer.folder.open": ArgumentContract(
                {"root_id": "string", "relative_path": "string"},
                target_required=True,
                allowed_postconditions=("explorer.folder.verified",),
            ),
            "settings.open": ArgumentContract(
                {"page_id": "string"},
                target_required=True,
                string_choices={
                    "page_id": ("display", "sound", "network", "bluetooth")
                },
                allowed_postconditions=("process.exists",),
            ),
            "audio.volume.set": ArgumentContract(
                {"percent": "integer"},
                target_required=True,
                integer_ranges={"percent": (0, 100)},
                allowed_postconditions=("volume.matches",),
            ),
            "audio.volume.read": ArgumentContract(
                target_required=True,
                allowed_postconditions=("volume.observed",),
            ),
            "file.list": ArgumentContract(
                {"path": "string"},
                target_required=True,
                allowed_postconditions=(
                    "execution.success",
                    "observed.contains",
                    "observed.equals",
                ),
            ),
            "storage.status.read": ArgumentContract(
                {"root_id": "string"},
                target_required=True,
                allowed_postconditions=("storage.status.valid",),
            ),
            "cleanup.suggest": ArgumentContract(
                {"root_id": "string"},
                target_required=True,
                allowed_postconditions=("cleanup.suggestion.safe",),
            ),
            "file.open": ArgumentContract(
                {"path": "string", "sha256": "string", "viewer_app_id": "string"},
                target_required=True,
                allowed_postconditions=("file.opened",),
            ),
            "memory.search": ArgumentContract(
                {"query": "string"},
                {"k": "integer"},
                target_required=True,
                integer_ranges={"k": (1, 10)},
                allowed_postconditions=("memory.search.matches",),
            ),
            "browser.fixture.read": ArgumentContract(
                {"fixture_id": "string"},
                target_required=True,
                allowed_postconditions=("fixture.sources.bound",),
            ),
            "browser.navigate": ArgumentContract(
                {"browser_id": "string", "url": "string"},
                target_required=True,
                string_choices={"browser_id": ("chrome", "edge")},
                allowed_postconditions=("browser.navigation.verified",),
            ),
            "document.write": ArgumentContract(
                {
                    "path": "string",
                    "title": "string",
                    "body": "string",
                    "source_set_digest": "string",
                },
                target_required=True,
                allowed_postconditions=("document.content.sha256",),
            ),
            "package.verify": ArgumentContract(
                {"path": "string", "sha256": "string", "publisher": "string"},
                target_required=True,
                allowed_postconditions=("package.identity.verified",),
            ),
            "package.install": ArgumentContract(
                {"package_sha256": "string", "publisher_sha256": "string"},
                target_required=True,
                allowed_postconditions=("snapshot.restore.verified",),
            ),
            "ui.element.inspect": ArgumentContract(
                {"process_id": "integer", "automation_id": "string"},
                {"control_type": "string", "name": "string"},
                target_required=True,
                allowed_postconditions=(
                    "observed.contains",
                    "observed.equals",
                ),
            ),
            "notepad.text.replace": ArgumentContract(
                {"process_id": "integer", "text": "string"},
                target_required=True,
                allowed_postconditions=("notepad.text.sha256",),
            ),
            "file.move": ArgumentContract(
                {"source": "string", "destination": "string"},
                target_required=True,
                allowed_postconditions=("file.moved",),
            ),
            "file.delete.trash": ArgumentContract(
                {"path": "string"},
                target_required=True,
                allowed_postconditions=("file.absent",),
            ),
            "file.delete.permanent": ArgumentContract(
                {"path": "string"},
                target_required=True,
                allowed_postconditions=("snapshot.restore.verified",),
            ),
            "message.draft": ArgumentContract(
                {"recipient": "string", "body": "string"},
                target_required=True,
                allowed_postconditions=("message.drafted",),
            ),
            "message.send": ArgumentContract(
                {"recipient": "string", "body": "string"},
                target_required=True,
                allowed_postconditions=("message.sent",),
            ),
            "system.shutdown": ArgumentContract(
                optional={"delay_seconds": "integer"},
                target_required=True,
                allowed_postconditions=("snapshot.restore.verified",),
            ),
            "system.reset.commit": ArgumentContract(
                {"keep_files": "boolean"},
                target_required=True,
                allowed_postconditions=("snapshot.restore.verified",),
            ),
            "system.reset.prepare": ArgumentContract(
                {"keep_files": "boolean"},
                target_required=True,
                allowed_postconditions=("reset.prepared",),
            ),
        }
    )


def validate_plan_contracts(
    plan: Plan,
    registry: CapabilityRegistry,
    contracts: Mapping[str, ArgumentContract] | None = None,
    *,
    max_steps: int = 20,
) -> None:
    if not 1 <= max_steps <= 100:
        raise ValueError("max_steps must be between 1 and 100")
    if len(plan.steps) > max_steps:
        raise PlanContractError("plan exceeds the maximum number of steps")
    rules = contracts or default_argument_contracts()
    for action in plan.steps:
        if registry.get(action.capability_id) is None:
            raise PlanContractError(f"unknown capability: {action.capability_id}")
        contract = rules.get(action.capability_id)
        if contract is None:
            raise PlanContractError(f"missing argument contract: {action.capability_id}")
        _validate_postconditions(action)
        contract.validate(action)


def _validate_postconditions(action: Action) -> None:
    if not action.expected_postconditions:
        raise PlanContractError(f"{action.capability_id} requires at least one postcondition")
    for postcondition in action.expected_postconditions:
        keys = set(postcondition.arguments)
        if postcondition.predicate == "execution.success":
            if keys:
                raise PlanContractError("execution.success does not accept arguments")
        elif postcondition.predicate == "observed.contains":
            values = postcondition.arguments.get("values")
            if keys != {"values"} or not isinstance(values, dict):
                raise PlanContractError("observed.contains requires one values object")
        elif postcondition.predicate == "observed.equals":
            if keys != {"value"}:
                raise PlanContractError("observed.equals requires one value argument")
        elif postcondition.predicate == "process.exists":
            if keys:
                raise PlanContractError("process.exists does not accept arguments")
        elif postcondition.predicate == "window.identity.matches":
            if keys:
                raise PlanContractError("window.identity.matches does not accept arguments")
        elif postcondition.predicate == "file.moved":
            if keys:
                raise PlanContractError("file.moved does not accept arguments")
        elif postcondition.predicate == "file.absent":
            if keys:
                raise PlanContractError("file.absent does not accept arguments")
        elif postcondition.predicate == "file.opened":
            if keys:
                raise PlanContractError("file.opened does not accept arguments")
        elif postcondition.predicate == "notepad.text.sha256":
            if keys:
                raise PlanContractError("notepad.text.sha256 does not accept arguments")
        elif postcondition.predicate == "document.content.sha256":
            digest = postcondition.arguments.get("sha256")
            if (
                keys != {"sha256"}
                or not isinstance(digest, str)
                or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            ):
                raise PlanContractError(
                    "document.content.sha256 requires one lowercase sha256 argument"
                )
        elif postcondition.predicate == "volume.matches":
            if keys:
                raise PlanContractError("volume.matches does not accept arguments")
        elif postcondition.predicate == "volume.observed":
            if keys:
                raise PlanContractError("volume.observed does not accept arguments")
        elif postcondition.predicate in {
            "message.drafted",
            "message.sent",
            "storage.status.valid",
            "cleanup.suggestion.safe",
            "memory.search.matches",
            "fixture.sources.bound",
            "browser.navigation.verified",
            "explorer.folder.verified",
            "package.identity.verified",
            "reset.prepared",
            "snapshot.restore.verified",
        }:
            if keys:
                raise PlanContractError(
                    f"{postcondition.predicate} does not accept arguments"
                )
        else:
            raise PlanContractError(
                f"unknown postcondition: {postcondition.predicate}"
            )
