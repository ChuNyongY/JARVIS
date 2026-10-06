from datetime import UTC, datetime, timedelta

from javis_core import (
    Action,
    CancellationToken,
    Postcondition,
    ResetPreparationStore,
    ResetPreparationVerifier,
    ResetPrepareExecutor,
)
from javis_core.runtime_models import ExecutionResult

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)


def action(*, keep_files: bool = True) -> Action:
    return Action(
        capability_id="system.reset.prepare",
        arguments={"keep_files": keep_files},
        purpose="prepare reset in disposable snapshot VM",
        target_identity="snapshot-vm:win11-lab",
        expected_postconditions=(Postcondition(predicate="reset.prepared"),),
    )


def test_prepares_volatile_receipt_without_commit_or_os_mutation() -> None:
    store = ResetPreparationStore()
    executor = ResetPrepareExecutor(store, vm_id="win11-lab", clock=lambda: NOW)
    requested = action()

    result = executor.execute(requested)
    verified = ResetPreparationVerifier(executor, clock=lambda: NOW).verify(requested, result)

    assert result.success and verified.success
    assert result.observed["automatic_commit"] is False
    assert result.observed["os_mutation_count"] == 0


def test_invalid_target_contract_and_stop_fail_without_receipt() -> None:
    executor = ResetPrepareExecutor(
        ResetPreparationStore(),
        vm_id="win11-lab",
        clock=lambda: NOW,
    )
    wrong_target = action().model_copy(update={"target_identity": "windows-reset:host"})
    wrong_post = action().model_copy(
        update={"expected_postconditions": (Postcondition(predicate="execution.success"),)}
    )
    assert executor.execute(wrong_target).error_code == "INVALID_ARGUMENTS"
    assert executor.execute(wrong_post).error_code == "INVALID_ARGUMENTS"
    token = CancellationToken()
    token.cancel()
    assert executor.execute(action(), token).error_code == "STOP_REQUESTED"


def test_verifier_rejects_forged_and_expired_receipts() -> None:
    store = ResetPreparationStore()
    executor = ResetPrepareExecutor(
        store,
        vm_id="win11-lab",
        ttl=timedelta(seconds=5),
        clock=lambda: NOW,
    )
    requested = action()
    genuine = executor.execute(requested)
    forged = ExecutionResult(
        action_id=requested.action_id,
        executor="native",
        success=True,
        observed={
            "preparation_receipt": "0" * 64,
            "expires_at": (NOW + timedelta(seconds=5)).isoformat(),
            "automatic_commit": False,
            "os_mutation_count": 0,
        },
        duration_ms=0,
    )
    verifier = ResetPreparationVerifier(executor, clock=lambda: NOW)
    assert verifier.verify(requested, forged).reason == "RESET_PREPARATION_NOT_FOUND"
    expired = ResetPreparationVerifier(
        executor,
        clock=lambda: NOW + timedelta(seconds=6),
    )
    assert expired.verify(requested, genuine).reason == "RESET_PREPARATION_NOT_FOUND"


def test_receipt_is_bound_to_exact_arguments() -> None:
    store = ResetPreparationStore()
    executor = ResetPrepareExecutor(store, vm_id="win11-lab", clock=lambda: NOW)
    requested = action(keep_files=True)
    result = executor.execute(requested)

    changed = action(keep_files=False).model_copy(update={"action_id": requested.action_id})

    assert ResetPreparationVerifier(executor, clock=lambda: NOW).verify(
        changed,
        result,
    ).reason == "RESET_PREPARATION_NOT_FOUND"
