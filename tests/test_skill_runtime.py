from datetime import datetime
from pathlib import Path

import pytest

from javis_core import (
    Action,
    LocalSkillCatalog,
    Plan,
    SkillAwarePlanner,
    SkillOutcome,
    SkillProposal,
)
from javis_core.registry import default_registry
from javis_core.skill_runtime import SkillRuntimeError


def catalog(tmp_path: Path) -> LocalSkillCatalog:
    return LocalSkillCatalog((tmp_path / "skills.sqlite3").resolve(), default_registry())


def proposal(**updates: object) -> SkillProposal:
    values: dict[str, object] = {
        "key": "volume-control",
        "title": "볼륨 조절",
        "description": "기본 출력 장치 볼륨을 읽거나 설정한다.",
        "triggers": ("볼륨", "소리"),
        "procedure": ("현재 볼륨을 읽는다.", "요청값을 확인한다.", "결과를 다시 읽는다."),
        "allowed_capabilities": ("audio.volume.read", "audio.volume.set"),
    }
    values.update(updates)
    return SkillProposal(**values)  # type: ignore[arg-type]


def test_draft_cannot_be_found_or_used_until_approved(tmp_path: Path) -> None:
    store = catalog(tmp_path)
    draft = store.propose(proposal())
    assert store.search("볼륨") == ()
    with pytest.raises(SkillRuntimeError, match="only verified"):
        store.record_use(draft, SkillOutcome.SUCCEEDED, "VERIFIED")
    verified = store.approve(draft.skill_id)
    assert store.search("소리")[0].skill_id == verified.skill_id
    use = store.record_use(verified, SkillOutcome.SUCCEEDED, "VOLUME_MATCHED")
    assert use.skill_version == 1


def test_revision_is_a_new_draft_and_retires_old_only_after_approval(tmp_path: Path) -> None:
    store = catalog(tmp_path)
    first = store.approve(store.propose(proposal()).skill_id)
    revision = store.propose_revision(
        first.skill_id,
        proposal(description="기본 출력 장치 볼륨을 안전하게 조절하고 읽어 확인한다."),
    )
    assert store.search("볼륨")[0].skill_id == first.skill_id
    approved = store.approve(revision.skill_id)
    assert store.require(first.skill_id).status.value == "RETIRED"
    assert store.search("볼륨")[0].skill_id == approved.skill_id
    assert approved.version == 2


def test_unknown_capability_cannot_enter_a_skill(tmp_path: Path) -> None:
    with pytest.raises(SkillRuntimeError, match="unregistered"):
        catalog(tmp_path).propose(proposal(allowed_capabilities=("powershell.raw",)))


class Planner:
    def __init__(self, capability_id: str) -> None:
        self.capability_id = capability_id
        self.context: dict[str, object] | None = None

    def plan(
        self,
        goal: str,
        *,
        trusted_context: dict[str, object] | None = None,
        untrusted_context: str | None = None,
    ) -> Plan:
        self.context = trusted_context
        return Plan(goal=goal, steps=(Action(capability_id=self.capability_id, purpose="test"),))


def test_skill_is_retrieved_then_binds_the_planner_to_its_allowlist(tmp_path: Path) -> None:
    store = catalog(tmp_path)
    store.approve(store.propose(proposal()).skill_id)
    planner = Planner("audio.volume.read")
    plan = SkillAwarePlanner(planner, store).plan("볼륨 알려 줘")
    assert plan.steps[0].capability_id == "audio.volume.read"
    assert planner.context is not None and "verified_skill" in planner.context

    with pytest.raises(SkillRuntimeError, match="outside the selected skill"):
        SkillAwarePlanner(Planner("file.delete.trash"), store).plan("볼륨 알려 줘")


def test_skill_time_must_be_timezone_aware(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        catalog(tmp_path).propose(proposal(), now=datetime(2026, 10, 10))
