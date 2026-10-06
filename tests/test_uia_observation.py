import pytest

from javis_core import (
    UiaElementSnapshot,
    UiaObservationError,
    UiaObserver,
    UiaSelector,
)


class Backend:
    def __init__(self, matches: tuple[UiaElementSnapshot, ...], *, fail: bool = False) -> None:
        self.matches = matches
        self.fail = fail
        self.max_results: int | None = None

    def find(
        self,
        selector: UiaSelector,
        *,
        max_results: int,
    ) -> tuple[UiaElementSnapshot, ...]:
        self.max_results = max_results
        if self.fail:
            raise RuntimeError("injected")
        return self.matches


def snapshot(**updates: object) -> UiaElementSnapshot:
    values: dict[str, object] = {
        "process_id": 4242,
        "runtime_id": (1, 2, 3),
        "automation_id": "Editor",
        "control_type": "Edit",
        "name": "Text editor",
        "class_name": "RichEditD2DPT",
        "enabled": True,
        "visible": True,
    }
    values.update(updates)
    return UiaElementSnapshot(**values)  # type: ignore[arg-type]


def selector() -> UiaSelector:
    return UiaSelector(
        process_id=4242,
        automation_id="Editor",
        control_type="Edit",
        name="Text editor",
    )


def test_unique_exact_match_is_returned() -> None:
    expected = snapshot()
    backend = Backend((expected,))

    assert UiaObserver(backend).find_unique(selector()) == expected
    assert backend.max_results == 2


@pytest.mark.parametrize("matches", [(), (snapshot(), snapshot(runtime_id=(4, 5, 6)))])
def test_missing_or_ambiguous_match_is_rejected(
    matches: tuple[UiaElementSnapshot, ...],
) -> None:
    with pytest.raises(UiaObservationError):
        UiaObserver(Backend(matches)).find_unique(selector())


def test_backend_result_is_independently_identity_checked() -> None:
    with pytest.raises(UiaObservationError, match="mismatched"):
        UiaObserver(Backend((snapshot(process_id=9999),))).find_unique(selector())


def test_backend_exception_is_fail_closed() -> None:
    with pytest.raises(UiaObservationError, match="backend"):
        UiaObserver(Backend((), fail=True)).find_unique(selector())


def test_selector_requires_stable_exact_property() -> None:
    with pytest.raises(ValueError, match="identity"):
        UiaSelector(process_id=4242)


def test_class_name_is_an_exact_identity_property() -> None:
    chrome = snapshot(automation_id="", control_type="Window", class_name="Chrome_WidgetWin_1")
    query = UiaSelector(
        process_id=4242,
        control_type="Window",
        class_name="Chrome_WidgetWin_1",
    )

    assert UiaObserver(Backend((chrome,))).find_unique(query) == chrome
    with pytest.raises(UiaObservationError, match="mismatched"):
        UiaObserver(Backend((chrome.model_copy(update={"class_name": "Other"}),))).find_unique(
            query
        )


def test_class_name_length_is_bounded() -> None:
    with pytest.raises(ValueError):
        UiaSelector(process_id=4242, class_name="x" * 257)
