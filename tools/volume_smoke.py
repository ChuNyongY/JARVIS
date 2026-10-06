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


def main() -> int:
    backend: PycawEndpointVolumeBackend | None = None
    original: float | None = None
    restored = False
    success = False
    error_code: str | None = "VOLUME_SMOKE_FAILED"
    try:
        backend = PycawEndpointVolumeBackend()
        original = backend.read_percent()
        requested = max(0, min(100, round(original)))
        registry = default_registry()
        action = Action(
            capability_id="audio.volume.set",
            arguments={"percent": requested},
            purpose="same-state Core Audio smoke",
            target_identity="default-audio-render-endpoint",
            expected_postconditions=(Postcondition(predicate="volume.matches"),),
        )
        plan = Plan(goal="Verify bounded volume control and independent readback", steps=(action,))
        validate_plan_contracts(plan, registry)
        outcome = Orchestrator(
            policy=PolicyEngine(registry),
            executor=ExecutionBroker(
                registry,
                {"native": WindowsVolumeExecutor(backend)},
            ),
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
        success = outcome.status is ActionStatus.SUCCEEDED
        error_code = None if success else "VOLUME_PIPELINE_FAILED"
    except Exception:
        success = False
        error_code = "VOLUME_SMOKE_FAILED"
    finally:
        if backend is not None and original is not None:
            try:
                backend.set_percent(original)
                restored = abs(backend.read_percent() - original) <= 0.25
            except Exception:
                restored = False
        if not restored:
            success = False
            error_code = "VOLUME_RESTORE_FAILED"

    print(
        json.dumps(
            {
                "success": success,
                "error_code": error_code,
                "original_state_restored": restored,
                "device_details_emitted": False,
            },
            separators=(",", ":"),
        )
    )
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
