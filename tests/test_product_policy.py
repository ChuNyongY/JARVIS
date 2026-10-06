from __future__ import annotations

from pathlib import Path

import pytest

from javis_core.models import Action
from javis_core.product_policy import build_windows_policy_context_provider


@pytest.mark.parametrize(
    ("capability", "arguments", "target"),
    [
        ("system.info.read", {}, "system"),
        ("process.launch", {"app_id": "notepad"}, "notepad"),
        ("settings.open", {"page_id": "sound"}, "settings"),
        ("audio.volume.read", {}, "default-render-endpoint"),
        ("audio.volume.set", {"percent": 30}, "default-render-endpoint"),
        ("storage.status.read", {"root_id": "workspace"}, "storage:workspace"),
        ("cleanup.suggest", {"root_id": "workspace"}, "storage:workspace"),
        (
            "browser.navigate",
            {"browser_id": "edge", "url": "https://github.com/openai/codex"},
            "browser:edge:github.com",
        ),
    ],
)
def test_registered_target_relationships_match(
    tmp_path: Path,
    capability: str,
    arguments: dict[str, object],
    target: str,
) -> None:
    provider = build_windows_policy_context_provider(
        (tmp_path,),
        {"workspace": tmp_path},
        ("github.com",),
    )

    context = provider(
        Action(
            capability_id=capability,
            arguments=arguments,
            purpose="target match",
            target_identity=target,
        )
    )

    assert context.target_matches_plan
    assert context.target_confidence == 1.0


@pytest.mark.parametrize("capability", ["file.list", "file.move", "file.delete.trash"])
def test_file_target_must_be_exact_existing_path_inside_root(
    tmp_path: Path,
    capability: str,
) -> None:
    source = tmp_path / "source.txt"
    source.write_text("fixture", encoding="utf-8")
    key = "source" if capability == "file.move" else "path"
    arguments = {key: str(source)}
    if capability == "file.move":
        arguments["destination"] = str(tmp_path / "destination.txt")
    provider = build_windows_policy_context_provider(
        (tmp_path,),
        {"workspace": tmp_path},
    )

    matched = provider(
        Action(
            capability_id=capability,
            arguments=arguments,
            purpose="target match",
            target_identity=str(source),
        )
    )
    mismatched = provider(
        Action(
            capability_id=capability,
            arguments=arguments,
            purpose="target mismatch",
            target_identity=str(tmp_path / "different.txt"),
        )
    )

    assert matched.target_matches_plan
    assert not mismatched.target_matches_plan
    assert mismatched.target_confidence == 0.0


def test_unknown_capability_and_unregistered_storage_root_fail_closed(
    tmp_path: Path,
) -> None:
    provider = build_windows_policy_context_provider(
        (tmp_path,),
        {"workspace": tmp_path},
    )

    unknown = provider(Action(capability_id="unknown", purpose="reject"))
    storage = provider(
        Action(
            capability_id="storage.status.read",
            arguments={"root_id": "other"},
            purpose="reject",
            target_identity="storage:other",
        )
    )

    assert not unknown.target_matches_plan
    assert not storage.target_matches_plan


def test_browser_policy_rejects_unlisted_host_and_target_mismatch(tmp_path: Path) -> None:
    provider = build_windows_policy_context_provider(
        (tmp_path,), {"workspace": tmp_path}, ("github.com",)
    )
    unlisted = provider(
        Action(
            capability_id="browser.navigate",
            arguments={"browser_id": "chrome", "url": "https://evil.example"},
            purpose="reject",
            target_identity="browser:chrome:evil.example",
        )
    )
    mismatch = provider(
        Action(
            capability_id="browser.navigate",
            arguments={"browser_id": "chrome", "url": "https://github.com"},
            purpose="reject",
            target_identity="browser:edge:github.com",
        )
    )

    assert not unlisted.target_matches_plan
    assert not mismatch.target_matches_plan


def test_memory_search_requires_a_registered_store_target(tmp_path: Path) -> None:
    provider = build_windows_policy_context_provider(
        (tmp_path,),
        {"workspace": tmp_path},
        memory_store_ids=("personal",),
    )
    action = Action(
        capability_id="memory.search",
        arguments={"query": "정보처리기사", "k": 3},
        purpose="search local memory",
        target_identity="memory-store:personal",
    )

    assert provider(action).target_matches_plan
    assert not provider(
        action.model_copy(update={"target_identity": "memory-store:other"})
    ).target_matches_plan


def test_explorer_folder_policy_binds_root_relative_path_and_digest(tmp_path: Path) -> None:
    from javis_core.explorer_folder import explorer_folder_target_identity

    folder = tmp_path / "folder"
    folder.mkdir()
    target = explorer_folder_target_identity({"workspace": tmp_path}, "workspace", "folder")
    assert target is not None
    provider = build_windows_policy_context_provider(
        (tmp_path,), {"workspace": tmp_path}
    )

    matched = provider(
        Action(
            capability_id="explorer.folder.open",
            arguments={"root_id": "workspace", "relative_path": "folder"},
            purpose="open trusted folder",
            target_identity=target,
        )
    )
    escaped = provider(
        Action(
            capability_id="explorer.folder.open",
            arguments={"root_id": "workspace", "relative_path": "..\\outside"},
            purpose="reject escape",
            target_identity=target,
        )
    )

    assert matched.target_matches_plan
    assert not escaped.target_matches_plan
