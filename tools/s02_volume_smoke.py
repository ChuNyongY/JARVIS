from __future__ import annotations

import json
import secrets

from javis_core import (
    Action,
    ActionStatus,
    ApprovalTokenService,
    ExecutionBroker,
    InMemoryAuditSink,
    Orchestrator,
    Plan,
    PolicyContext,
    PolicyEngine,
    Postcondition,
    PycawEndpointVolumeBackend,
    VolumeMatchesVerifier,
    WindowsVolumeExecutor,
    default_registry,
    validate_plan_contracts,
)

_REQUESTED_PERCENT = 30


def main() -> int:
    backend: PycawEndpointVolumeBackend | None = None
    original: float | None = None
    restored = False
    requested_verified = False
    error_code: str | None = "S02_VOLUME_SMOKE_FAILED"
    try:
        backend = PycawEndpointVolumeBackend()
        original = backend.read_percent()
        registry = default_registry()
        action = Action(
            capability_id="audio.volume.set",
            arguments={"percent": _REQUESTED_PERCENT},
            purpose="S02 exact requested volume smoke",
            target_identity="default-audio-render-endpoint",
            expected_postconditions=(Postcondition(predicate="volume.matches"),),
        )
        plan = Plan(
            goal="Verify S02 exact volume request and independent readback",
            steps=(action,),
        )
        validate_plan_contracts(plan, registry)
        outcome = Orchestrator(
            policy=PolicyEngine(registry),
            executor=ExecutionBroker(registry, {"native": WindowsVolumeExecutor(backend)}),
            verifier=VolumeMatchesVerifier(backend),
            audit=InMemoryAuditSink(),
            approvals=ApprovalTokenService(secrets.token_bytes(32)),
        ).run(
            plan,
            lambda _action: PolicyContext(
                addressed_confidence=1.0,
                intent_confidence=1.0,
                target_confidence=1.0,
                target_matches_plan=True,
            ),
        )
        requested_verified = (
            outcome.status is ActionStatus.SUCCEEDED
            and abs(backend.read_percent() - _REQUESTED_PERCENT) <= 1.0
        )
        error_code = None if requested_verified else "S02_VOLUME_PIPELINE_FAILED"
    except Exception:
        error_code = "S02_VOLUME_SMOKE_FAILED"
    finally:
        if backend is not None and original is not None:
            try:
                backend.set_percent(original)
                restored = abs(backend.read_percent() - original) <= 0.25
            except Exception:
                restored = False
        if not restored:
            error_code = "S02_VOLUME_RESTORE_FAILED"

    success = requested_verified and restored
    print(
        json.dumps(
            {
                "scenario_id": "S02",
                "success": success,
                "evidence_level": "ACTUAL_REVERSIBLE",
                "requested_value_verified": requested_verified,
                "original_state_restored": restored,
                "device_details_emitted": False,
                "error_code": None if success else error_code,
            },
            separators=(",", ":"),
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
