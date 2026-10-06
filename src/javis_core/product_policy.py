from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from .agent_session import PolicyContextProvider
from .models import Action, PolicyContext


def build_windows_policy_context_provider(
    allowed_roots: Iterable[Path],
    storage_roots: Mapping[str, Path],
    browser_allowed_hosts: Iterable[str] = (),
    memory_store_ids: Iterable[str] = (),
) -> PolicyContextProvider:
    """Build a content-free target matcher; voice gate confidence is applied later."""
    roots = _validated_roots(allowed_roots)
    storage_mapping = _validated_storage_roots(storage_roots, roots)
    storage_ids = frozenset(storage_mapping)
    browser_hosts = frozenset(browser_allowed_hosts)
    memory_ids = frozenset(memory_store_ids)
    if not storage_ids or any(not value for value in storage_ids):
        raise ValueError("storage root identifiers must not be empty")

    def provide(action: Action) -> PolicyContext:
        target_matches = _target_matches(
            action, roots, storage_mapping, storage_ids, browser_hosts, memory_ids
        )
        return PolicyContext(
            addressed_confidence=1.0,
            intent_confidence=1.0,
            target_confidence=1.0 if target_matches else 0.0,
            target_matches_plan=target_matches,
        )

    return provide


def _target_matches(
    action: Action,
    roots: tuple[Path, ...],
    storage_roots: Mapping[str, Path],
    storage_ids: frozenset[str],
    browser_hosts: frozenset[str],
    memory_store_ids: frozenset[str],
) -> bool:
    target = action.target_identity
    capability = action.capability_id
    if capability == "system.info.read":
        return target in {None, "system"}
    if capability == "process.launch":
        app_id = action.arguments.get("app_id")
        return isinstance(app_id, str) and target == app_id
    if capability == "settings.open":
        return target == "settings"
    if capability == "explorer.folder.open":
        root_id = action.arguments.get("root_id")
        relative_path = action.arguments.get("relative_path")
        if not isinstance(root_id, str) or not isinstance(relative_path, str):
            return False
        from .explorer_folder import explorer_folder_target_identity

        return target == explorer_folder_target_identity(
            storage_roots, root_id, relative_path
        )
    if capability in {"audio.volume.read", "audio.volume.set"}:
        return target == "default-render-endpoint"
    if capability in {"storage.status.read", "cleanup.suggest"}:
        root_id = action.arguments.get("root_id")
        return (
            isinstance(root_id, str)
            and root_id in storage_ids
            and target == f"storage:{root_id}"
        )
    if capability == "memory.search":
        return target in {f"memory-store:{item}" for item in memory_store_ids}
    if capability == "browser.navigate":
        browser_id = action.arguments.get("browser_id")
        raw_url = action.arguments.get("url")
        if (
            browser_id not in {"chrome", "edge"}
            or not isinstance(raw_url, str)
            or not browser_hosts
        ):
            return False
        from .browser_navigation import _validate_url

        parsed = _validate_url(raw_url, browser_hosts)
        return parsed is not None and target == f"browser:{browser_id}:{parsed.hostname}"
    argument = {
        "file.list": "path",
        "file.move": "source",
        "file.delete.trash": "path",
    }.get(capability)
    if argument is None:
        return False
    raw_path = action.arguments.get(argument)
    if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
        return False
    try:
        path = Path(raw_path).resolve(strict=True)
    except OSError:
        return False
    return target == str(path) and any(path.is_relative_to(root) for root in roots)


def _validated_roots(paths: Iterable[Path]) -> tuple[Path, ...]:
    roots: list[Path] = []
    for path in paths:
        if not path.is_absolute() or path.is_symlink():
            raise ValueError("policy roots must be absolute non-symlink directories")
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise ValueError("policy root is unavailable") from error
        if not resolved.is_dir() or resolved in roots:
            raise ValueError("policy roots must be unique directories")
        roots.append(resolved)
    if not roots:
        raise ValueError("at least one policy root is required")
    return tuple(roots)


def _validated_storage_roots(
    storage_roots: Mapping[str, Path], allowed_roots: tuple[Path, ...]
) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for root_id, path in storage_roots.items():
        if not root_id or root_id in result:
            raise ValueError("storage root identifiers must be unique and nonempty")
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise ValueError("storage root is unavailable") from error
        if not resolved.is_dir() or resolved not in allowed_roots:
            raise ValueError("storage roots must be allowed directories")
        result[root_id] = resolved
    if not result:
        raise ValueError("storage root identifiers must not be empty")
    return result
