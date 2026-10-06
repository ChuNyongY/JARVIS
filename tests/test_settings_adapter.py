from pathlib import Path

import pytest

from javis_core import Action, CancellationToken, RegisteredApp, SettingsOpenExecutor


class Backend:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[str] = []

    def launch(self, uri: str) -> None:
        self.calls.append(uri)
        if self.fail:
            raise OSError("private activation detail")


class Resolver:
    def __init__(
        self,
        before: frozenset[int] = frozenset(),
        resolved: int | None = 42,
        after: frozenset[int] | None = None,
    ) -> None:
        self.before = before
        self.resolved = resolved
        self.after = before if after is None else after
        self.snapshot_calls = 0
        self.resolve_calls: list[tuple[int, frozenset[int]]] = []

    def snapshot(self, app: RegisteredApp) -> frozenset[int]:
        self.snapshot_calls += 1
        return self.before if self.snapshot_calls == 1 else self.after

    def resolve(
        self,
        app: RegisteredApp,
        launcher_pid: int,
        previous_pids: frozenset[int],
    ) -> int | None:
        self.resolve_calls.append((launcher_pid, previous_pids))
        return self.resolved


def settings_app(tmp_path: Path, *, direct_launch: bool = False) -> RegisteredApp:
    executable = tmp_path / "SystemSettings.exe"
    executable.write_bytes(b"settings")
    return RegisteredApp.from_path("settings", executable).model_copy(
        update={"direct_launch": direct_launch}
    )


def action(**arguments: object) -> Action:
    return Action(
        capability_id="settings.open",
        arguments=arguments or {"page_id": "display"},
        purpose="open display settings",
        target_identity="settings",
    )


def test_allowlisted_page_activates_and_returns_runtime_pid(tmp_path: Path) -> None:
    backend = Backend()
    resolver = Resolver(resolved=73)
    result = SettingsOpenExecutor(
        settings_app(tmp_path), backend=backend, runtime_resolver=resolver
    ).execute(action())
    assert result.success
    assert result.observed == {"app_id": "settings", "page_id": "display", "pid": 73}
    assert backend.calls == ["ms-settings:display"]
    assert resolver.resolve_calls == [(0, frozenset())]


def test_single_existing_verified_runtime_can_be_reused(tmp_path: Path) -> None:
    resolver = Resolver(before=frozenset({91}), resolved=None, after=frozenset({91}))
    result = SettingsOpenExecutor(
        settings_app(tmp_path), backend=Backend(), runtime_resolver=resolver
    ).execute(action(page_id="sound"))
    assert result.success
    assert result.observed["pid"] == 91


def test_network_page_uses_documented_windows_uri(tmp_path: Path) -> None:
    backend = Backend()
    result = SettingsOpenExecutor(
        settings_app(tmp_path), backend=backend, runtime_resolver=Resolver(resolved=73)
    ).execute(action(page_id="network"))
    assert result.success
    assert backend.calls == ["ms-settings:network-status"]


@pytest.mark.parametrize(
    ("candidate", "code"),
    [
        (action(page_id="privacy"), "PAGE_NOT_ALLOWED"),
        (action(page_id="home"), "PAGE_NOT_ALLOWED"),
        (action(page_id="display", uri="ms-settings:privacy"), "INVALID_ARGUMENTS"),
        (
            action().model_copy(update={"target_identity": "other"}),
            "TARGET_IDENTITY_MISMATCH",
        ),
        (
            action().model_copy(update={"capability_id": "process.launch"}),
            "UNSUPPORTED_CAPABILITY",
        ),
    ],
)
def test_dynamic_or_mismatched_inputs_are_rejected(
    tmp_path: Path, candidate: Action, code: str
) -> None:
    backend = Backend()
    result = SettingsOpenExecutor(settings_app(tmp_path), backend=backend).execute(candidate)
    assert not result.success
    assert result.error_code == code
    assert backend.calls == []


def test_cancellation_ambiguity_and_backend_failure_are_closed(tmp_path: Path) -> None:
    app = settings_app(tmp_path)
    token = CancellationToken()
    token.cancel()
    assert SettingsOpenExecutor(app, backend=Backend()).execute(action(), token).error_code == (
        "STOP_REQUESTED"
    )
    ambiguous = SettingsOpenExecutor(
        app,
        backend=Backend(),
        runtime_resolver=Resolver(before=frozenset({1, 2})),
    ).execute(action())
    assert ambiguous.error_code == "RUNTIME_AMBIGUOUS"
    failed = SettingsOpenExecutor(
        app,
        backend=Backend(fail=True),
        runtime_resolver=Resolver(),
    ).execute(action())
    assert failed.error_code == "SETTINGS_ACTIVATION_FAILED"
    assert "private" not in (failed.error_message or "")


def test_missing_runtime_and_invalid_registration_are_rejected(tmp_path: Path) -> None:
    app = settings_app(tmp_path)
    result = SettingsOpenExecutor(
        app,
        backend=Backend(),
        runtime_resolver=Resolver(resolved=None),
    ).execute(action())
    assert result.error_code == "SETTINGS_RUNTIME_NOT_FOUND"
    with pytest.raises(ValueError, match="direct launch disabled"):
        SettingsOpenExecutor(settings_app(tmp_path, direct_launch=True))


def test_settings_page_allowlist_is_immutable() -> None:
    with pytest.raises(TypeError):
        SettingsOpenExecutor._PAGES["privacy"] = "ms-settings:privacy"  # type: ignore[index]
