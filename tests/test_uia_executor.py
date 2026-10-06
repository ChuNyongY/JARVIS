from javis_core import (
    Action,
    CancellationToken,
    UiaElementSnapshot,
    UiaInspectExecutor,
    UiaObserver,
)


class Backend:
    def __init__(self, matches: tuple[UiaElementSnapshot, ...]) -> None:
        self.matches = matches

    def find(self, selector, *, max_results):  # type: ignore[no-untyped-def]
        return self.matches


def action(**updates: object) -> Action:
    values: dict[str, object] = {
        "capability_id": "ui.element.inspect",
        "arguments": {
            "process_id": 4242,
            "automation_id": "Editor",
            "control_type": "Edit",
        },
        "purpose": "inspect editor",
        "target_identity": "process:4242",
    }
    values.update(updates)
    return Action(**values)  # type: ignore[arg-type]


def snapshot() -> UiaElementSnapshot:
    return UiaElementSnapshot(
        process_id=4242,
        runtime_id=(1, 2),
        automation_id="Editor",
        control_type="Edit",
        name="Text editor",
        class_name="RichEditD2DPT",
        enabled=True,
        visible=True,
    )


def test_inspects_only_authorized_process_and_returns_structural_data() -> None:
    executor = UiaInspectExecutor(UiaObserver(Backend((snapshot(),))), lambda pid: pid == 4242)

    result = executor.execute(action())

    assert result.success
    assert result.observed["process_id"] == 4242
    assert result.observed["automation_id"] == "Editor"
    assert "name" not in result.observed


def test_control_type_can_identify_element_without_automation_id() -> None:
    item = snapshot().model_copy(
        update={
            "automation_id": "",
            "control_type": "Window",
            "class_name": "Chrome_WidgetWin_1",
        }
    )
    executor = UiaInspectExecutor(UiaObserver(Backend((item,))), lambda pid: pid == 4242)
    inspection = action(
        arguments={
            "process_id": 4242,
            "control_type": "Window",
            "class_name": "Chrome_WidgetWin_1",
        },
    )

    result = executor.execute(inspection)

    assert result.success
    assert result.observed["control_type"] == "Window"


def test_unapproved_process_is_rejected_before_observation() -> None:
    executor = UiaInspectExecutor(UiaObserver(Backend((snapshot(),))), lambda pid: False)
    result = executor.execute(action())
    assert result.error_code == "PROCESS_NOT_AUTHORIZED"


def test_target_and_arguments_are_strictly_bound() -> None:
    executor = UiaInspectExecutor(UiaObserver(Backend((snapshot(),))), lambda pid: True)
    mismatched = action(target_identity="process:9999")
    extra = action(
        arguments={"process_id": 4242, "automation_id": "Editor", "regex": ".*"}
    )

    assert executor.execute(mismatched).error_code == "TARGET_IDENTITY_MISMATCH"
    assert executor.execute(extra).error_code == "INVALID_ARGUMENTS"


def test_ambiguous_observation_fails_without_side_effect() -> None:
    executor = UiaInspectExecutor(
        UiaObserver(Backend((snapshot(), snapshot()))),
        lambda pid: True,
    )
    result = executor.execute(action())
    assert result.error_code == "UIA_OBSERVATION_FAILED"
    assert result.retry_safe


def test_stop_is_checked_before_observation() -> None:
    cancellation = CancellationToken()
    cancellation.cancel()
    executor = UiaInspectExecutor(UiaObserver(Backend((snapshot(),))), lambda pid: True)
    result = executor.execute(action(), cancellation)
    assert result.error_code == "STOP_REQUESTED"
    assert not result.retry_safe
