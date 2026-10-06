import hashlib

from javis_core import (
    Action,
    CancellationToken,
    NotepadProfile,
    NotepadReplaceTextExecutor,
    NotepadTextHashPredicate,
    Postcondition,
    UiaElementSnapshot,
    UiaObserver,
)


class ObserverBackend:
    def find(self, selector, *, max_results):  # type: ignore[no-untyped-def]
        return (
            UiaElementSnapshot(
                process_id=selector.process_id,
                runtime_id=(1, 2, 3),
                automation_id=selector.automation_id or "",
                control_type="Edit",
                name="Editor",
                class_name="RichEdit",
                enabled=True,
                visible=True,
            ),
        )


class TextBackend:
    def __init__(self, *, fail: bool = False, corrupt: bool = False) -> None:
        self.fail = fail
        self.corrupt = corrupt
        self.text = ""
        self.calls = 0

    def replace_text(self, selector, expected_runtime_id, text):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.fail:
            raise RuntimeError("injected post-call ambiguity")
        self.text = text

    def text_sha256(self, selector, expected_runtime_id):  # type: ignore[no-untyped-def]
        value = self.text + ("corrupt" if self.corrupt else "")
        return hashlib.sha256(value.encode()).hexdigest()


def action(text: str = "안녕하세요") -> Action:
    return Action(
        capability_id="notepad.text.replace",
        arguments={"process_id": 4242, "text": text},
        purpose="replace draft",
        target_identity="process:4242:notepad-editor",
        expected_postconditions=(Postcondition(predicate="notepad.text.sha256"),),
    )


def executor(backend: TextBackend) -> NotepadReplaceTextExecutor:
    return NotepadReplaceTextExecutor(
        UiaObserver(ObserverBackend()),
        backend,
        lambda pid, app_id: pid == 4242 and app_id == "notepad",
        NotepadProfile(automation_id="Editor"),
    )


def test_replace_text_is_hash_verified_without_returning_content() -> None:
    backend = TextBackend()
    result = executor(backend).execute(action())

    assert result.success
    assert result.observed["text_sha256"] == hashlib.sha256("안녕하세요".encode()).hexdigest()
    assert "text" not in result.observed


def test_modern_notepad_profile_uses_exact_document_control() -> None:
    selector = NotepadProfile(control_type="Document").selector(123)

    assert selector.process_id == 123
    assert selector.automation_id is None
    assert selector.control_type == "Document"


def test_post_call_failure_and_hash_mismatch_are_not_retry_safe() -> None:
    for backend in (TextBackend(fail=True), TextBackend(corrupt=True)):
        result = executor(backend).execute(action())
        assert not result.success
        assert not result.retry_safe


def test_unauthorized_target_is_rejected_before_mutation() -> None:
    backend = TextBackend()
    denied = action().model_copy(update={"target_identity": "process:9999:notepad-editor"})
    result = executor(backend).execute(denied)
    assert result.error_code == "TARGET_IDENTITY_MISMATCH"
    assert backend.calls == 0


def test_cancel_before_mutation_is_respected() -> None:
    backend = TextBackend()
    cancellation = CancellationToken()
    cancellation.cancel()
    result = executor(backend).execute(action(), cancellation)
    assert result.error_code == "STOP_REQUESTED"
    assert backend.calls == 0


def test_independent_hash_predicate_rechecks_editor() -> None:
    backend = TextBackend()
    result = executor(backend).execute(action())
    predicate = NotepadTextHashPredicate(
        UiaObserver(ObserverBackend()),
        backend,
        lambda pid, app_id: pid == 4242 and app_id == "notepad",
        NotepadProfile(automation_id="Editor"),
    )
    assert predicate(Postcondition(predicate="notepad.text.sha256"), result)
    backend.text = "changed"
    assert not predicate(Postcondition(predicate="notepad.text.sha256"), result)
