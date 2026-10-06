from __future__ import annotations

from pathlib import Path

import pytest

from javis_core.file_move import InMemoryMoveJournal
from javis_core.memory import SemanticMemoryRecord
from javis_core.models import Action, Postcondition
from javis_core.process_launch import RegisteredApp
from javis_core.product_host import LoadedProductHostConfig, ProductHostConfig
from javis_core.product_windows import build_windows_product_capabilities


class Volume:
    def __init__(self) -> None:
        self.value = 50.0

    def read_percent(self) -> float:
        return self.value

    def set_percent(self, percent: float) -> None:
        self.value = percent


def configuration(tmp_path: Path) -> LoadedProductHostConfig:
    artifacts = []
    for name in ("apps.json", "hello.json", "wake.json", "stt.json"):
        path = tmp_path / name
        path.write_text("{}", encoding="utf-8")
        artifacts.append(path)
    return LoadedProductHostConfig(
        settings=ProductHostConfig(
            app_registry="config/apps.json",
            hello_manifest="config/hello.json",
            wakeword_manifest="models/wake.json",
            stt_manifest="models/stt.json",
            browser_allowed_hosts=("github.com",),
            nim_model="z-ai/glm-5.3",
            wake_threshold=0.75,
            vad_rms_threshold=500,
            end_silence_frames=5,
            max_capture_frames=1500,
            microphone_frame_ms=80,
            microphone_queue_capacity=32,
            hud_poll_interval_ms=250,
        ),
        app_registry=artifacts[0],
        hello_manifest=artifacts[1],
        wakeword_manifest=artifacts[2],
        stt_manifest=artifacts[3],
    )


def apps(tmp_path: Path) -> dict[str, RegisteredApp]:
    executable = tmp_path / "app.exe"
    executable.write_bytes(b"fixture")
    application = RegisteredApp.from_path("safe-app", executable)
    chrome = RegisteredApp.from_path("chrome", executable).model_copy(
        update={"single_instance_handoff": True, "window_class": "BrowserWindow"}
    )
    settings = RegisteredApp.from_path("settings", executable).model_copy(
        update={"direct_launch": False}
    )
    explorer = RegisteredApp.from_path("explorer", executable).model_copy(
        update={"single_instance_handoff": True, "window_class": "CabinetWClass"}
    )
    return {
        "safe-app": application,
        "settings": settings,
        "chrome": chrome,
        "explorer": explorer,
    }


def test_windows_capabilities_are_exactly_executable_and_verified(tmp_path: Path) -> None:
    stack = build_windows_product_capabilities(
        configuration(tmp_path),
        tmp_path,
        app_loader=lambda _path: apps(tmp_path),
        volume_backend=Volume(),
        move_journal=InMemoryMoveJournal(),
    )

    assert {item.capability_id for item in stack.registry.list()} == {
        "system.info.read",
        "file.list",
        "process.launch",
        "explorer.folder.open",
        "settings.open",
        "audio.volume.read",
        "audio.volume.set",
        "storage.status.read",
        "cleanup.suggest",
        "file.move",
        "file.delete.trash",
        "browser.navigate",
        "memory.search",
    }


def test_windows_memory_search_route_executes_and_independently_verifies(
    tmp_path: Path,
) -> None:
    class MemoryBackend:
        def __init__(self) -> None:
            self.calls = 0

        def search(
            self, query: str, *, k: int = 10
        ) -> tuple[SemanticMemoryRecord, ...]:
            assert (query, k) == ("정보처리기사", 3)
            self.calls += 1
            return ()

    memory = MemoryBackend()
    stack = build_windows_product_capabilities(
        configuration(tmp_path),
        tmp_path,
        app_loader=lambda _path: apps(tmp_path),
        volume_backend=Volume(),
        move_journal=InMemoryMoveJournal(),
        memory_backend=memory,
    )
    action = Action(
        capability_id="memory.search",
        arguments={"query": "정보처리기사", "k": 3},
        purpose="search local memory",
        target_identity="memory-store:personal",
        expected_postconditions=(Postcondition(predicate="memory.search.matches"),),
    )

    result = stack.executor.execute(action)
    verified = stack.verifier.verify(action, result)

    assert result.success
    assert result.observed["match_count"] == 0
    assert verified.success
    assert memory.calls == 2


def test_windows_capability_build_has_no_app_or_volume_mutation(tmp_path: Path) -> None:
    volume = Volume()
    loaded = 0

    def load(_path: Path) -> dict[str, RegisteredApp]:
        nonlocal loaded
        loaded += 1
        return apps(tmp_path)

    build_windows_product_capabilities(
        configuration(tmp_path),
        tmp_path,
        app_loader=load,
        volume_backend=volume,
        move_journal=InMemoryMoveJournal(),
    )

    assert loaded == 1
    assert volume.value == 50.0
    assert not (tmp_path / "state").exists()


def test_windows_capability_build_rejects_missing_settings(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="required registered"):
        build_windows_product_capabilities(
            configuration(tmp_path),
            tmp_path,
            app_loader=lambda _path: {},
            volume_backend=Volume(),
            move_journal=InMemoryMoveJournal(),
        )


def test_windows_capability_build_rejects_untrusted_roots(tmp_path: Path) -> None:
    relative = Path("relative")
    with pytest.raises(ValueError, match="absolute"):
        build_windows_product_capabilities(
            configuration(tmp_path),
            tmp_path,
            allowed_roots=(relative,),
            app_loader=lambda _path: apps(tmp_path),
            volume_backend=Volume(),
            move_journal=InMemoryMoveJournal(),
        )
