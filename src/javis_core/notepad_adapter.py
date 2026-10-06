from __future__ import annotations

import hashlib
from collections.abc import Callable
from importlib import import_module
from time import perf_counter_ns
from typing import Any, Literal, Protocol

from pydantic import Field

from .cancellation import CancellationToken
from .models import Action, FrozenModel, Postcondition
from .runtime_models import ExecutionResult
from .uia_observation import UiaObservationError, UiaObserver, UiaSelector


class NotepadProfile(FrozenModel):
    automation_id: str | None = Field(default=None, min_length=1, max_length=256)
    control_type: Literal["Edit", "Document"] = "Edit"
    name: str | None = Field(default=None, min_length=1, max_length=512)

    def selector(self, process_id: int) -> UiaSelector:
        return UiaSelector(
            process_id=process_id,
            automation_id=self.automation_id,
            control_type=self.control_type,
            name=self.name,
        )


class NotepadTextBackend(Protocol):
    def replace_text(
        self,
        selector: UiaSelector,
        expected_runtime_id: tuple[int, ...],
        text: str,
    ) -> None: ...

    def text_sha256(
        self,
        selector: UiaSelector,
        expected_runtime_id: tuple[int, ...],
    ) -> str: ...


class PywinautoNotepadTextBackend:
    """Optional UIA ValuePattern backend that avoids logging document text."""

    def replace_text(
        self,
        selector: UiaSelector,
        expected_runtime_id: tuple[int, ...],
        text: str,
    ) -> None:
        wrapper = self._wrapper(selector, expected_runtime_id)
        wrapper.verify_actionable()
        wrapper.iface_value.SetValue(text)

    def text_sha256(
        self,
        selector: UiaSelector,
        expected_runtime_id: tuple[int, ...],
    ) -> str:
        value = self._wrapper(selector, expected_runtime_id).get_value()
        if not isinstance(value, str):
            raise RuntimeError("Notepad editor did not return text")
        return _text_hash(value)

    @staticmethod
    def _wrapper(selector: UiaSelector, expected_runtime_id: tuple[int, ...]) -> Any:
        try:
            EditWrapper = import_module("pywinauto.controls.uia_controls").EditWrapper
            find_elements = import_module("pywinauto.findwindows").find_elements
        except ImportError as error:
            raise RuntimeError("pywinauto optional dependency is not installed") from error
        elements = find_elements(
            process=selector.process_id,
            auto_id=selector.automation_id,
            control_type=selector.control_type,
            title=selector.name,
            top_level_only=False,
            visible_only=True,
            enabled_only=True,
            backend="uia",
        )
        if len(elements) != 1:
            raise RuntimeError("Notepad editor identity is ambiguous")
        element = elements[0]
        if tuple(int(value) for value in element.runtime_id) != expected_runtime_id:
            raise RuntimeError("Notepad editor runtime identity changed")
        return EditWrapper(element)


class NotepadReplaceTextExecutor:
    name = "uia"

    def __init__(
        self,
        observer: UiaObserver,
        backend: NotepadTextBackend,
        process_authorized: Callable[[int, str], bool],
        profile: NotepadProfile,
        *,
        max_characters: int = 100_000,
    ) -> None:
        if max_characters < 1:
            raise ValueError("max_characters must be positive")
        self._observer = observer
        self._backend = backend
        self._process_authorized = process_authorized
        self._profile = profile
        self._max_characters = max_characters

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        parsed = self._parse(action)
        if isinstance(parsed, str):
            return self._failure(action, started, parsed, retry_safe=True)
        process_id, text = parsed
        if action.target_identity != f"process:{process_id}:notepad-editor":
            return self._failure(action, started, "TARGET_IDENTITY_MISMATCH", retry_safe=True)
        if not self._process_authorized(process_id, "notepad"):
            return self._failure(action, started, "PROCESS_NOT_AUTHORIZED", retry_safe=True)
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED", retry_safe=False)
        selector = self._profile.selector(process_id)
        try:
            snapshot = self._observer.find_unique(selector)
        except UiaObservationError:
            return self._failure(action, started, "EDITOR_NOT_FOUND", retry_safe=True)
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED", retry_safe=False)

        expected_hash = _text_hash(text)
        try:
            self._backend.replace_text(selector, snapshot.runtime_id, text)
            observed_hash = self._backend.text_sha256(selector, snapshot.runtime_id)
        except Exception:
            return self._failure(action, started, "TEXT_REPLACE_AMBIGUOUS", retry_safe=False)
        if observed_hash != expected_hash:
            return self._failure(action, started, "TEXT_VERIFICATION_FAILED", retry_safe=False)
        return ExecutionResult(
            action_id=action.action_id,
            executor="uia",
            success=True,
            observed={
                "process_id": process_id,
                "runtime_id": list(snapshot.runtime_id),
                "text_sha256": expected_hash,
            },
            duration_ms=self._elapsed_ms(started),
        )

    def _parse(self, action: Action) -> tuple[int, str] | str:
        if action.capability_id != "notepad.text.replace":
            return "UNSUPPORTED_CAPABILITY"
        if set(action.arguments) != {"process_id", "text"}:
            return "INVALID_ARGUMENTS"
        process_id = action.arguments.get("process_id")
        text = action.arguments.get("text")
        if type(process_id) is not int or not isinstance(text, str) or not text:
            return "INVALID_ARGUMENTS"
        if len(text) > self._max_characters:
            return "TEXT_TOO_LARGE"
        return process_id, text

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
            executor="uia",
            success=False,
            error_code=error_code,
            error_message="Notepad text replacement was not verified",
            retry_safe=retry_safe,
            duration_ms=cls._elapsed_ms(started),
        )


class NotepadTextHashPredicate:
    """Independently re-read the fixed editor and compare only a content hash."""

    def __init__(
        self,
        observer: UiaObserver,
        backend: NotepadTextBackend,
        process_authorized: Callable[[int, str], bool],
        profile: NotepadProfile,
    ) -> None:
        self._observer = observer
        self._backend = backend
        self._process_authorized = process_authorized
        self._profile = profile

    def __call__(self, postcondition: Postcondition, result: ExecutionResult) -> bool:
        if postcondition.arguments:
            return False
        process_id = result.observed.get("process_id")
        expected_hash = result.observed.get("text_sha256")
        runtime_id = result.observed.get("runtime_id")
        if (
            type(process_id) is not int
            or not isinstance(expected_hash, str)
            or not isinstance(runtime_id, list)
            or not all(type(value) is int for value in runtime_id)
            or not self._process_authorized(process_id, "notepad")
        ):
            return False
        selector = self._profile.selector(process_id)
        try:
            snapshot = self._observer.find_unique(selector)
            if snapshot.runtime_id != tuple(runtime_id):
                return False
            return self._backend.text_sha256(selector, snapshot.runtime_id) == expected_hash
        except Exception:
            return False


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
