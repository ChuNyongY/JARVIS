from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from threading import Lock

from .app_registry import AppRegistryLoader
from .browser_navigation import BrowserNavigateExecutor, BrowserNavigationVerifier
from .explorer_folder import ExplorerFolderOpenExecutor, ExplorerFolderVerifier
from .file_move import FileMovedPredicate, FileMoveExecutor, MoveJournal
from .file_trash import FileAbsentPredicate, FileTrashExecutor, TrashBackend
from .memory import SemanticMemoryRecord, SqliteMemoryRepository
from .memory_search import (
    MemorySearchBackend,
    MemorySearchExecutor,
    MemorySearchVerifier,
)
from .move_journal import DurableMoveJournal
from .native_readonly import NativeReadOnlyExecutor
from .process_launch import ProcessLaunchExecutor, RegisteredApp
from .process_verifier import ProcessExistsPredicate
from .product_composition import ProductCapabilityStack, build_product_capability_stack
from .product_host import LoadedProductHostConfig
from .settings_adapter import SettingsOpenExecutor
from .settings_verifier import SettingsPageVerifier
from .storage_monitor import StorageMonitorExecutor, StorageMonitorVerifier
from .verifier import PredicateVerifier
from .volume_adapter import (
    PycawEndpointVolumeBackend,
    VolumeBackend,
    VolumeMatchesVerifier,
    WindowsVolumeExecutor,
)

AppLoader = Callable[[Path], Mapping[str, RegisteredApp]]


class _LazySqliteMemorySearchBackend:
    """Delay local DB creation until an authorized memory search actually runs."""

    def __init__(self, project_root: Path) -> None:
        self._state_directory = project_root / "state"
        self._repository: SqliteMemoryRepository | None = None
        self._lock = Lock()

    def search(self, query: str, *, k: int = 10) -> tuple[SemanticMemoryRecord, ...]:
        with self._lock:
            if self._repository is None:
                state = self._state_directory
                if state.is_symlink() or (state.exists() and not state.is_dir()):
                    raise ValueError("memory state directory is invalid")
                state.mkdir(parents=True, exist_ok=True)
                self._repository = SqliteMemoryRepository(state / "memory.sqlite3")
            return self._repository.search(query, k=k)


def build_windows_product_capabilities(
    configuration: LoadedProductHostConfig,
    project_root: Path,
    *,
    allowed_roots: Iterable[Path] | None = None,
    storage_roots: Mapping[str, Path] | None = None,
    app_loader: AppLoader | None = None,
    volume_backend: VolumeBackend | None = None,
    move_journal: MoveJournal | None = None,
    trash_backend: TrashBackend | None = None,
    memory_backend: MemorySearchBackend | None = None,
) -> ProductCapabilityStack:
    """Build the host-safe Windows routes without launching apps or changing state."""
    root = _trusted_root(project_root)
    roots = _trusted_allowed_roots(allowed_roots or (root,))
    selected_storage_roots = dict(storage_roots or {"workspace": root})
    load_apps = app_loader or AppRegistryLoader(require_publisher=True).load
    apps = dict(load_apps(configuration.app_registry))
    settings = apps.get("settings")
    explorer_app = apps.get("explorer")
    browsers = {
        app_id: apps[app_id]
        for app_id in ("chrome", "edge")
        if app_id in apps
    }
    if not apps or settings is None or explorer_app is None or not browsers:
        raise ValueError("required registered Windows applications are unavailable")

    audio = volume_backend or PycawEndpointVolumeBackend()
    journal = move_journal or DurableMoveJournal(root / "state" / "move-journal.jsonl")
    read_only = NativeReadOnlyExecutor(roots)
    process = ProcessLaunchExecutor(apps)
    settings_executor = SettingsOpenExecutor(settings)
    volume = WindowsVolumeExecutor(audio)
    storage = StorageMonitorExecutor(selected_storage_roots)
    move = FileMoveExecutor(roots, journal)
    trash = FileTrashExecutor(roots, backend=trash_backend)
    browser = BrowserNavigateExecutor(
        browsers,
        configuration.settings.browser_allowed_hosts,
    )
    explorer = ExplorerFolderOpenExecutor(selected_storage_roots, explorer_app)
    memory = MemorySearchExecutor(
        memory_backend or _LazySqliteMemorySearchBackend(root),
        store_id="personal",
    )

    process_verifier = PredicateVerifier(
        {"process.exists": ProcessExistsPredicate(apps)}
    )
    volume_verifier = VolumeMatchesVerifier(audio)
    storage_verifier = StorageMonitorVerifier(storage)
    move_verifier = PredicateVerifier({"file.moved": FileMovedPredicate()})
    trash_verifier = PredicateVerifier({"file.absent": FileAbsentPredicate()})
    generic_verifier = PredicateVerifier()

    return build_product_capability_stack(
        {
            "system.info.read": read_only,
            "file.list": read_only,
            "process.launch": process,
            "explorer.folder.open": explorer,
            "settings.open": settings_executor,
            "audio.volume.read": volume,
            "audio.volume.set": volume,
            "storage.status.read": storage,
            "cleanup.suggest": storage,
            "file.move": move,
            "file.delete.trash": trash,
            "browser.navigate": browser,
            "memory.search": memory,
        },
        {
            "system.info.read": generic_verifier,
            "file.list": generic_verifier,
            "process.launch": process_verifier,
            "explorer.folder.open": ExplorerFolderVerifier(explorer),
            "settings.open": SettingsPageVerifier(settings),
            "audio.volume.read": volume_verifier,
            "audio.volume.set": volume_verifier,
            "storage.status.read": storage_verifier,
            "cleanup.suggest": storage_verifier,
            "file.move": move_verifier,
            "file.delete.trash": trash_verifier,
            "browser.navigate": BrowserNavigationVerifier(browser),
            "memory.search": MemorySearchVerifier(memory),
        },
    )


def _trusted_root(path: Path) -> Path:
    if not path.is_absolute() or path.is_symlink():
        raise ValueError("project root must be an absolute non-symlink directory")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ValueError("project root is unavailable") from error
    if not resolved.is_dir():
        raise ValueError("project root must be a directory")
    return resolved


def _trusted_allowed_roots(paths: Iterable[Path]) -> tuple[Path, ...]:
    roots: list[Path] = []
    for path in paths:
        if not path.is_absolute() or path.is_symlink():
            raise ValueError("allowed roots must be absolute non-symlink directories")
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise ValueError("allowed root is unavailable") from error
        if not resolved.is_dir() or resolved in roots:
            raise ValueError("allowed roots must be unique directories")
        roots.append(resolved)
    if not roots:
        raise ValueError("at least one allowed root is required")
    return tuple(roots)
