from uuid import uuid4

import pytest

from javis_core import (
    ApprovalChannel,
    ApprovalRequest,
    ApprovalResponse,
    ConsentResult,
    LocalApprovalPresenter,
    OsBackedStrongAuthenticator,
    RiskLevel,
)


class Dialog:
    def __init__(self, result: bool = True, *, fail: bool = False) -> None:
        self.result = result
        self.fail = fail
        self.views = []

    def show(self, view, timeout_seconds: int) -> bool:  # type: ignore[no-untyped-def]
        if self.fail:
            raise RuntimeError("dialog unavailable")
        self.views.append((view, timeout_seconds))
        return self.result


class Verifier:
    def __init__(self, result: ConsentResult, *, fail: bool = False) -> None:
        self.result = result
        self.fail = fail
        self.messages: list[str] = []

    def request_verification(self, message: str) -> ConsentResult:
        if self.fail:
            raise RuntimeError("OS verifier unavailable")
        self.messages.append(message)
        return self.result


def request(*, strong: bool = True) -> ApprovalRequest:
    return ApprovalRequest(
        task_id=uuid4(),
        action_id=uuid4(),
        capability_id="system.reset.commit",
        arguments={"scope": "local"},
        arguments_hash="a" * 64,
        target_identity="local-machine",
        risk=RiskLevel.R4,
        purpose="reset machine",
        strong_auth_required=strong,
    )


def response(approval: ApprovalRequest, *, approved: bool = True) -> ApprovalResponse:
    return ApprovalResponse(
        request_id=approval.request_id,
        approved=approved,
        channel=ApprovalChannel.LOCAL_UI,
    )


def test_local_presenter_displays_exact_action_and_binds_response() -> None:
    backend = Dialog()
    approval = request()

    result = LocalApprovalPresenter(backend, timeout_seconds=30).request(approval)

    assert result.approved
    assert result.request_id == approval.request_id
    assert result.channel is ApprovalChannel.LOCAL_UI
    view, timeout = backend.views[0]
    assert timeout == 30
    assert view.capability == approval.capability_id
    assert view.target == approval.target_identity
    assert '"scope": "local"' in view.arguments_json
    assert "Windows" in view.strong_auth_notice


def test_dialog_failure_denies() -> None:
    assert not LocalApprovalPresenter(Dialog(fail=True)).request(request()).approved


def test_invalid_timeout_is_rejected() -> None:
    with pytest.raises(ValueError, match="positive"):
        LocalApprovalPresenter(Dialog(), timeout_seconds=0)


@pytest.mark.parametrize(
    ("consent", "expected"),
    [
        (ConsentResult.VERIFIED, True),
        (ConsentResult.CANCELED, False),
        (ConsentResult.UNAVAILABLE, False),
        (ConsentResult.FAILED, False),
    ],
)
def test_os_authenticator_accepts_only_verified(
    consent: ConsentResult,
    expected: bool,
) -> None:
    approval = request()
    verifier = Verifier(consent)

    assert OsBackedStrongAuthenticator(verifier).verify(
        approval,
        response(approval),
    ) is expected
    assert str(approval.request_id) in verifier.messages[0]
    assert approval.arguments_hash in verifier.messages[0]


def test_os_authenticator_rejects_unbound_or_failed_requests() -> None:
    approval = request()
    verifier = Verifier(ConsentResult.VERIFIED)
    wrong = response(approval).model_copy(update={"request_id": uuid4()})

    assert not OsBackedStrongAuthenticator(verifier).verify(approval, wrong)
    assert verifier.messages == []
    assert not OsBackedStrongAuthenticator(Verifier(ConsentResult.VERIFIED, fail=True)).verify(
        approval,
        response(approval),
    )
