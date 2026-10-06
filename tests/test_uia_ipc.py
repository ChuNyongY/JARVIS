import hashlib
import subprocess

import pytest

from javis_core import UiaElementSnapshot, UiaSelector
from javis_core.uia_ipc import (
    SubprocessNotepadTextBackend,
    SubprocessUiaBackend,
    UiaFindRequest,
    UiaTextHashRequest,
    UiaWorkerError,
    UiaWorkerResponse,
    UiaWorkerRunner,
    parse_worker_request,
)


class FakeRunner:
    def __init__(self, response: UiaWorkerResponse) -> None:
        self.response = response
        self.requests: list[object] = []

    def run(self, request: object) -> UiaWorkerResponse:
        self.requests.append(request)
        return self.response


def selector() -> UiaSelector:
    return UiaSelector(process_id=123, automation_id="Editor", control_type="Edit")


def snapshot() -> UiaElementSnapshot:
    return UiaElementSnapshot(
        process_id=123,
        runtime_id=(1, 2),
        automation_id="Editor",
        control_type="Edit",
        name="",
        class_name="RichEdit",
        enabled=True,
        visible=True,
    )


def test_subprocess_uia_backend_returns_structural_snapshots() -> None:
    runner = FakeRunner(
        UiaWorkerResponse(success=True, operation="find", snapshots=(snapshot(),))
    )

    result = SubprocessUiaBackend(runner).find(selector(), max_results=2)

    assert result == (snapshot(),)
    assert isinstance(runner.requests[0], UiaFindRequest)


def test_notepad_backend_never_returns_text() -> None:
    expected = hashlib.sha256(b"private").hexdigest()
    runner = FakeRunner(
        UiaWorkerResponse(success=True, operation="text_hash", text_sha256=expected)
    )

    result = SubprocessNotepadTextBackend(runner).text_sha256(selector(), (1, 2))

    assert result == expected
    assert isinstance(runner.requests[0], UiaTextHashRequest)


def test_runner_timeout_is_bounded_and_redacted() -> None:
    def timeout(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise subprocess.TimeoutExpired("private-command", 1)

    runner = UiaWorkerRunner(timeout_seconds=1, process_runner=timeout)

    with pytest.raises(UiaWorkerError) as captured:
        runner.run(UiaFindRequest(selector=selector(), max_results=2))

    assert "private-command" not in str(captured.value)


def test_worker_request_rejects_duplicate_keys() -> None:
    with pytest.raises(UiaWorkerError, match="duplicate"):
        parse_worker_request(b'{"operation":"find","operation":"find"}')
