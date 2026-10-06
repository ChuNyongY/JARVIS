from __future__ import annotations

from importlib import import_module
from typing import Protocol

from pydantic import Field, model_validator

from .models import FrozenModel


class UiaObservationError(RuntimeError):
    pass


class UiaSelector(FrozenModel):
    process_id: int = Field(ge=1)
    automation_id: str | None = Field(default=None, min_length=1, max_length=256)
    control_type: str | None = Field(default=None, min_length=1, max_length=128)
    name: str | None = Field(default=None, min_length=1, max_length=512)
    class_name: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def require_element_identity(self) -> UiaSelector:
        identities = (self.automation_id, self.control_type, self.name, self.class_name)
        if all(identity is None for identity in identities):
            raise ValueError("UIA selector requires an exact element identity property")
        return self


class UiaElementSnapshot(FrozenModel):
    process_id: int = Field(ge=1)
    runtime_id: tuple[int, ...] = Field(min_length=1, max_length=64)
    automation_id: str
    control_type: str
    name: str
    class_name: str
    enabled: bool
    visible: bool


class UiaBackend(Protocol):
    def find(
        self,
        selector: UiaSelector,
        *,
        max_results: int,
    ) -> tuple[UiaElementSnapshot, ...]: ...


class UiaObserver:
    """Resolve one exact UIA element and independently re-check its identity."""

    def __init__(self, backend: UiaBackend, *, max_results: int = 2) -> None:
        if max_results < 2 or max_results > 100:
            raise ValueError("max_results must be between 2 and 100")
        self._backend = backend
        self._max_results = max_results

    def find_unique(self, selector: UiaSelector) -> UiaElementSnapshot:
        try:
            matches = self._backend.find(selector, max_results=self._max_results)
        except Exception as error:
            raise UiaObservationError("UIA backend failed") from error
        if not matches:
            raise UiaObservationError("UIA element not found")
        if len(matches) != 1:
            raise UiaObservationError("UIA selector is ambiguous")
        match = matches[0]
        if not self._matches(selector, match):
            raise UiaObservationError("UIA backend returned a mismatched element")
        return match

    @staticmethod
    def _matches(selector: UiaSelector, match: UiaElementSnapshot) -> bool:
        return (
            match.process_id == selector.process_id
            and (
                selector.automation_id is None
                or match.automation_id == selector.automation_id
            )
            and (selector.control_type is None or match.control_type == selector.control_type)
            and (selector.name is None or match.name == selector.name)
            and (selector.class_name is None or match.class_name == selector.class_name)
        )


class PywinautoUiaBackend:
    """Optional read-only pywinauto UIA lookup using exact criteria only."""

    def find(
        self,
        selector: UiaSelector,
        *,
        max_results: int,
    ) -> tuple[UiaElementSnapshot, ...]:
        try:
            find_elements = import_module("pywinauto.findwindows").find_elements
        except ImportError as error:
            raise RuntimeError("pywinauto optional dependency is not installed") from error
        elements = find_elements(
            process=selector.process_id,
            auto_id=selector.automation_id,
            control_type=selector.control_type,
            title=selector.name,
            class_name=selector.class_name,
            top_level_only=False,
            visible_only=False,
            enabled_only=False,
            backend="uia",
        )
        snapshots: list[UiaElementSnapshot] = []
        for element in elements[:max_results]:
            snapshots.append(
                UiaElementSnapshot(
                    process_id=int(element.process_id),
                    runtime_id=tuple(int(value) for value in element.runtime_id),
                    automation_id=str(element.automation_id or ""),
                    control_type=str(element.control_type or ""),
                    name=str(element.name or ""),
                    class_name=str(element.class_name or ""),
                    enabled=bool(element.enabled),
                    visible=bool(element.visible),
                )
            )
        return tuple(snapshots)
