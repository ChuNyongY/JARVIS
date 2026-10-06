from __future__ import annotations

import hashlib

import pytest

from javis_core import (
    Action,
    CancellationToken,
    InMemoryMessageSink,
    MessageExecutor,
    MessageReceiptVerifier,
    Postcondition,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def message_action(capability: str = "message.send") -> Action:
    recipient = "철수"
    predicate = "message.sent" if capability == "message.send" else "message.drafted"
    return Action(
        capability_id=capability,
        arguments={"recipient": recipient, "body": "8시에 게임하자"},
        purpose="prepare exact fixture message",
        target_identity=f"recipient-sha256:{digest(recipient)}",
        expected_postconditions=(Postcondition(predicate=predicate),),
    )


@pytest.mark.parametrize("capability", ["message.draft", "message.send"])
def test_mock_sink_executes_and_verifies_without_retaining_content(capability: str) -> None:
    sink = InMemoryMessageSink()
    action = message_action(capability)
    result = MessageExecutor(sink).execute(action)

    verified = MessageReceiptVerifier(sink).verify(action, result)

    assert result.success
    assert verified.success
    assert result.observed["delivery_mode"] == "mock"
    assert sink.record_count == 1
    raw = result.model_dump_json() + verified.model_dump_json()
    assert "철수" not in raw
    assert "게임하자" not in raw


def test_wrong_target_and_control_characters_are_rejected_before_sink() -> None:
    sink = InMemoryMessageSink()
    wrong_target = message_action().model_copy(
        update={"target_identity": "recipient-sha256:" + "0" * 64}
    )
    newline_recipient = message_action().model_copy(
        update={"arguments": {"recipient": "철수\n몰래", "body": "hello"}}
    )

    for action in (wrong_target, newline_recipient):
        result = MessageExecutor(sink).execute(action)
        assert not result.success
        assert result.retry_safe
    assert sink.record_count == 0


class FailingSink:
    external_effect = True

    def draft(self, recipient: str, body: str) -> str:
        raise RuntimeError("private draft error")

    def send(self, recipient: str, body: str) -> str:
        raise RuntimeError("private send error")

    def verify(
        self,
        kind: str,
        receipt_id: str,
        recipient_sha256: str,
        body_sha256: str,
    ) -> bool:
        return False


def test_external_sink_requires_explicit_opt_in_and_send_failure_is_not_retryable() -> None:
    sink = FailingSink()
    with pytest.raises(ValueError, match="opt-in"):
        MessageExecutor(sink)  # type: ignore[arg-type]

    result = MessageExecutor(sink, allow_external=True).execute(  # type: ignore[arg-type]
        message_action()
    )

    assert result.error_code == "MESSAGE_SEND_STATE_UNKNOWN"
    assert not result.retry_safe
    assert "private send error" not in result.model_dump_json()


def test_stop_after_mock_send_is_not_retryable() -> None:
    token = CancellationToken()

    class CancellingSink(InMemoryMessageSink):
        def send(self, recipient: str, body: str) -> str:
            receipt = super().send(recipient, body)
            token.cancel()
            return receipt

    result = MessageExecutor(CancellingSink()).execute(message_action(), token)

    assert result.error_code == "STOP_REQUESTED_AFTER_INPUT"
    assert not result.retry_safe


def test_forged_receipt_binding_is_rejected() -> None:
    sink = InMemoryMessageSink()
    action = message_action()
    result = MessageExecutor(sink).execute(action)
    forged = result.model_copy(
        update={"observed": {**result.observed, "body_sha256": "0" * 64}}
    )

    verified = MessageReceiptVerifier(sink).verify(action, forged)

    assert not verified.success
    assert verified.reason == "MESSAGE_VERIFY_BINDING_FAILED"
    assert not verified.recoverable


def test_mock_sink_capacity_is_bounded_and_content_free() -> None:
    sink = InMemoryMessageSink(max_records=1)
    executor = MessageExecutor(sink)
    assert executor.execute(message_action("message.draft")).success

    second = executor.execute(message_action("message.draft"))

    assert second.error_code == "MESSAGE_DRAFT_FAILED"
    assert second.retry_safe
    assert sink.record_count == 1
