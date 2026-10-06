import pytest

from javis_core import (
    Action,
    Plan,
    PlanContractError,
    Postcondition,
    default_registry,
    validate_plan_contracts,
)


def validate(action: Action) -> None:
    if not action.expected_postconditions:
        action = action.model_copy(
            update={
                "expected_postconditions": (
                    Postcondition(predicate="execution.success"),
                )
            }
        )
    validate_plan_contracts(Plan(goal="contract test", steps=(action,)), default_registry())


def test_valid_process_launch_contract() -> None:
    validate(
        Action(
            capability_id="process.launch",
            arguments={"app_id": "notepad"},
            purpose="launch app",
            expected_postconditions=(
                Postcondition(predicate="process.exists"),
                Postcondition(predicate="window.identity.matches"),
            ),
            target_identity="notepad",
        )
    )


def test_browser_navigation_contract_is_exact() -> None:
    validate(
        Action(
            capability_id="browser.navigate",
            arguments={"browser_id": "chrome", "url": "https://github.com/openai/codex"},
            purpose="open allowlisted site",
            expected_postconditions=(
                Postcondition(predicate="browser.navigation.verified"),
            ),
            target_identity="browser:chrome:github.com",
        )
    )
    with pytest.raises(PlanContractError, match="allowed value"):
        validate(
            Action(
                capability_id="browser.navigate",
                arguments={"browser_id": "firefox", "url": "https://github.com"},
                purpose="reject unregistered browser",
                target_identity="browser:firefox:github.com",
            )
        )


def test_settings_open_accepts_only_page_id_and_target() -> None:
    validate(
        Action(
            capability_id="settings.open",
            arguments={"page_id": "display"},
            purpose="open display settings",
            expected_postconditions=(Postcondition(predicate="process.exists"),),
            target_identity="settings",
        )
    )
    with pytest.raises(PlanContractError, match="allowed value"):
        validate(
            Action(
                capability_id="settings.open",
                arguments={"page_id": "privacy"},
                purpose="reject unknown Settings page",
                target_identity="settings",
            )
        )
    with pytest.raises(PlanContractError, match="forbidden"):
        validate(
            Action(
                capability_id="settings.open",
                arguments={"page_id": "display", "uri": "ms-settings:privacy"},
                purpose="reject dynamic URI",
                target_identity="settings",
            )
        )


def test_argument_contract_rejects_invalid_choice_declarations() -> None:
    from javis_core import ArgumentContract

    with pytest.raises(ValueError, match="string choices"):
        ArgumentContract({"page_id": "string"}, string_choices={"other": ("x",)})
    with pytest.raises(ValueError, match="string choices"):
        ArgumentContract({"page_id": "string"}, string_choices={"page_id": ("x", "x")})


@pytest.mark.parametrize("forbidden", ["command", "script", "args", "working_directory"])
def test_process_launch_rejects_injected_execution_fields(forbidden: str) -> None:
    action = Action(
        capability_id="process.launch",
        arguments={"app_id": "notepad", forbidden: "malicious"},
        purpose="must reject",
        target_identity="notepad",
    )
    with pytest.raises(PlanContractError, match="forbidden"):
        validate(action)


def test_unknown_capability_is_rejected_at_plan_boundary() -> None:
    action = Action(capability_id="powershell.raw", purpose="must reject")
    with pytest.raises(PlanContractError, match="unknown capability"):
        validate(action)


def test_target_identity_is_required_for_side_effect_target() -> None:
    action = Action(
        capability_id="file.delete.trash",
        arguments={"path": "C:/fixture.txt"},
        purpose="missing target identity",
    )
    with pytest.raises(PlanContractError, match="target_identity"):
        validate(action)


def test_boolean_is_not_accepted_as_integer() -> None:
    action = Action(
        capability_id="system.shutdown",
        arguments={"delay_seconds": True},
        purpose="type confusion",
        target_identity="local-machine",
    )
    with pytest.raises(PlanContractError, match="integer"):
        validate(action)


@pytest.mark.parametrize("percent", [-1, 101])
def test_volume_percent_is_bounded_at_plan_boundary(percent: int) -> None:
    action = Action(
        capability_id="audio.volume.set",
        arguments={"percent": percent},
        purpose="reject out-of-range volume",
        target_identity="default-audio-render-endpoint",
        expected_postconditions=(Postcondition(predicate="volume.matches"),),
    )

    with pytest.raises(PlanContractError, match="allowed range"):
        validate_plan_contracts(
            Plan(goal="reject invalid volume", steps=(action,)),
            default_registry(),
        )


def test_volume_contract_accepts_bounded_integer_and_exact_predicate() -> None:
    validate_plan_contracts(
        Plan(
            goal="set volume",
            steps=(
                Action(
                    capability_id="audio.volume.set",
                    arguments={"percent": 30},
                    purpose="set requested volume",
                    target_identity="default-audio-render-endpoint",
                    expected_postconditions=(Postcondition(predicate="volume.matches"),),
                ),
            ),
        ),
        default_registry(),
    )


def test_file_open_contract_requires_hash_viewer_target_and_exact_predicate() -> None:
    validate_plan_contracts(
        Plan(
            goal="open PDF",
            steps=(
                Action(
                    capability_id="file.open",
                    arguments={
                        "path": "C:/fixture.pdf",
                        "sha256": "0" * 64,
                        "viewer_app_id": "edge",
                    },
                    purpose="open exact PDF",
                    target_identity="file-sha256:" + "0" * 64,
                    expected_postconditions=(Postcondition(predicate="file.opened"),),
                ),
            ),
        ),
        default_registry(),
    )


def test_plan_step_count_is_bounded() -> None:
    steps = tuple(
        Action(
            capability_id="system.info.read",
            purpose=f"step {index}",
            expected_postconditions=(Postcondition(predicate="execution.success"),),
        )
        for index in range(3)
    )
    with pytest.raises(PlanContractError, match="maximum"):
        validate_plan_contracts(
            Plan(goal="too many", steps=steps),
            default_registry(),
            max_steps=2,
        )


def test_unknown_postcondition_is_rejected_at_plan_boundary() -> None:
    action = Action(
        capability_id="system.info.read",
        purpose="invented verification",
        expected_postconditions=(Postcondition(predicate="system.info.available"),),
    )
    with pytest.raises(PlanContractError, match="unknown postcondition"):
        validate_plan_contracts(
            Plan(goal="reject invented predicate", steps=(action,)),
            default_registry(),
        )


def test_action_without_postcondition_is_rejected() -> None:
    action = Action(capability_id="system.info.read", purpose="missing verifier")
    with pytest.raises(PlanContractError, match="at least one"):
        validate_plan_contracts(
            Plan(goal="reject missing verifier", steps=(action,)),
            default_registry(),
        )
