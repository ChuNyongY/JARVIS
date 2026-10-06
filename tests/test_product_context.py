from __future__ import annotations

from datetime import UTC, datetime

import pytest

from javis_core.foreground_context import ForegroundContextError, ForegroundSnapshot
from javis_core.product_context import PrivacyMinimizedPlannerContextProvider


class Collector:
    def __init__(self, snapshot: ForegroundSnapshot) -> None:
        self.snapshot = snapshot
        self.calls = 0

    def capture(self) -> ForegroundSnapshot:
        self.calls += 1
        return self.snapshot


def snapshot(*, app_id: str | None) -> ForegroundSnapshot:
    return ForegroundSnapshot(
        hwnd=123,
        pid=456,
        app_id=app_id,
        identity_verified=app_id is not None,
        captured_at=datetime.now(UTC),
    )


def test_verified_context_exposes_only_registered_app_id() -> None:
    collector = Collector(snapshot(app_id="notepad"))
    provider = PrivacyMinimizedPlannerContextProvider(collector)

    context = provider()

    assert context == {
        "foreground_app_id": "notepad",
        "foreground_identity_verified": True,
    }
    assert collector.calls == 1
    assert "pid" not in context
    assert "hwnd" not in context


def test_unverified_context_exposes_no_process_or_window_identity() -> None:
    provider = PrivacyMinimizedPlannerContextProvider(Collector(snapshot(app_id=None)))

    assert provider() == {
        "foreground_app_id": None,
        "foreground_identity_verified": False,
    }


def test_context_collection_failure_is_not_converted_to_trusted_data() -> None:
    class FailingCollector:
        def capture(self) -> ForegroundSnapshot:
            raise ForegroundContextError("private window title")

    provider = PrivacyMinimizedPlannerContextProvider(FailingCollector())

    with pytest.raises(ForegroundContextError):
        provider()
