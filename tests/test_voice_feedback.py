from uuid import uuid4

import pytest

from javis_core import ActionStatus, CancellationToken, TaskOutcome
from javis_core.voice_feedback import (
    FeedbackCode,
    VoiceFeedbackPresenter,
    feedback_code_for_outcome,
)


class FakeSpeechSink:
    def __init__(self) -> None:
        self.calls: list[tuple[str, CancellationToken | None]] = []

    def speak(
        self,
        text: str,
        *,
        rate: int = 0,
        volume: int = 100,
        cancellation: CancellationToken | None = None,
    ) -> None:
        assert rate == 0
        assert volume == 100
        self.calls.append((text, cancellation))


@pytest.mark.parametrize(
    ("status", "reason", "expected"),
    [
        (ActionStatus.SUCCEEDED, "ALL_ACTIONS_VERIFIED", FeedbackCode.TASK_SUCCEEDED),
        (ActionStatus.ABORTED, "GLOBAL_STOP", FeedbackCode.CANCELLED),
        (ActionStatus.ABORTED, "CANCELLED_BY_USER", FeedbackCode.CANCELLED),
        (ActionStatus.ABORTED, "RED_APPROVAL_REQUIRED", FeedbackCode.APPROVAL_REQUIRED),
        (ActionStatus.ABORTED, "STRONG_AUTH_REQUIRED", FeedbackCode.APPROVAL_REQUIRED),
        (ActionStatus.ABORTED, "PRIVATE_FAILURE_DETAIL", FeedbackCode.TASK_ABORTED),
    ],
)
def test_outcome_maps_to_bounded_feedback(
    status: ActionStatus, reason: str, expected: FeedbackCode
) -> None:
    outcome = TaskOutcome(
        task_id=uuid4(), status=status, completed_actions=0, reason=reason
    )
    assert feedback_code_for_outcome(outcome) is expected


def test_presenter_speaks_only_fixed_phrase_and_forwards_stop_token() -> None:
    sink = FakeSpeechSink()
    token = CancellationToken()
    outcome = TaskOutcome(
        task_id=uuid4(),
        status=ActionStatus.ABORTED,
        completed_actions=0,
        reason="PRIVATE_MODEL_FAILURE",
    )

    code = VoiceFeedbackPresenter(sink).present_outcome(outcome, cancellation=token)

    assert code is FeedbackCode.TASK_ABORTED
    assert sink.calls == [("안전을 위해 작업을 중단했습니다.", token)]
    assert "PRIVATE_MODEL_FAILURE" not in sink.calls[0][0]
