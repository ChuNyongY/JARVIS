import hashlib
import json
from pathlib import Path

import pytest

from javis_core import AppRegistryError, AppRegistryLoader


class PublisherVerifier:
    def __init__(self, accepted: bool) -> None:
        self.accepted = accepted
        self.calls: list[tuple[Path, str]] = []

    def verify(self, executable: Path, expected_subject: str) -> bool:
        self.calls.append((executable, expected_subject))
        return self.accepted


def registry(path: Path, apps: list[dict[str, object]]) -> Path:
    path.write_text(json.dumps({"schema_version": "1.0", "apps": apps}), encoding="utf-8")
    return path


def app(executable: Path, **updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "app_id": "safe-app",
        "executable": str(executable.resolve()),
        "sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
    }
    value.update(updates)
    return value


def test_loads_absolute_hash_matched_app(tmp_path: Path) -> None:
    executable = tmp_path / "safe.exe"
    executable.write_bytes(b"fixture")

    loaded = AppRegistryLoader().load(registry(tmp_path / "apps.json", [app(executable)]))

    assert loaded["safe-app"].executable == executable.resolve()
    assert loaded["safe-app"].sha256 == hashlib.sha256(b"fixture").hexdigest()


def test_direct_launch_flag_is_strict_and_preserved(tmp_path: Path) -> None:
    executable = tmp_path / "safe.exe"
    executable.write_bytes(b"fixture")
    loaded = AppRegistryLoader().load(
        registry(tmp_path / "apps.json", [app(executable, direct_launch=False)])
    )
    assert not loaded["safe-app"].direct_launch
    with pytest.raises(AppRegistryError, match="invalid"):
        AppRegistryLoader().load(
            registry(tmp_path / "bad.json", [app(executable, direct_launch="false")])
        )


def test_single_instance_handoff_is_strict_and_preserved(tmp_path: Path) -> None:
    executable = tmp_path / "safe.exe"
    executable.write_bytes(b"fixture")
    loaded = AppRegistryLoader().load(
        registry(
            tmp_path / "apps.json",
            [
                app(
                    executable,
                    single_instance_handoff=True,
                    window_class="FixtureWindow",
                )
            ],
        )
    )
    assert loaded["safe-app"].single_instance_handoff
    with pytest.raises(AppRegistryError, match="invalid"):
        AppRegistryLoader().load(
            registry(
                tmp_path / "bad.json",
                [
                    app(
                        executable,
                        single_instance_handoff="true",
                        window_class="FixtureWindow",
                    )
                ],
            )
        )


def test_only_fixed_new_window_argument_is_accepted(tmp_path: Path) -> None:
    executable = tmp_path / "safe.exe"
    executable.write_bytes(b"fixture")
    loaded = AppRegistryLoader().load(
        registry(
            tmp_path / "apps.json",
            [
                app(
                    executable,
                    single_instance_handoff=True,
                    window_class="FixtureWindow",
                    launch_argument="--new-window",
                )
            ],
        )
    )
    assert loaded["safe-app"].launch_argument == "--new-window"
    with pytest.raises(AppRegistryError, match="invalid"):
        AppRegistryLoader().load(
            registry(
                tmp_path / "bad-argument.json",
                [
                    app(
                        executable,
                        single_instance_handoff=True,
                        window_class="FixtureWindow",
                        launch_argument="--arbitrary",
                    )
                ],
            )
        )


def test_single_instance_handoff_rejects_conflicting_modes(tmp_path: Path) -> None:
    launcher = tmp_path / "launcher.exe"
    runtime = tmp_path / "runtime.exe"
    launcher.write_bytes(b"launcher")
    runtime.write_bytes(b"runtime")
    conflicting = app(
        launcher,
        single_instance_handoff=True,
        runtime_executable=str(runtime.resolve()),
        runtime_sha256=hashlib.sha256(b"runtime").hexdigest(),
    )
    with pytest.raises(AppRegistryError, match="invalid"):
        AppRegistryLoader().load(registry(tmp_path / "bad.json", [conflicting]))

    disabled = app(
        launcher,
        direct_launch=False,
        single_instance_handoff=True,
    )
    with pytest.raises(AppRegistryError, match="invalid"):
        AppRegistryLoader().load(registry(tmp_path / "disabled.json", [disabled]))

    document_mode_without_handoff = app(
        launcher,
        document_launch_mode="browser-new-window",
    )
    with pytest.raises(AppRegistryError, match="invalid"):
        AppRegistryLoader().load(
            registry(tmp_path / "document-mode.json", [document_mode_without_handoff])
        )


def test_duplicate_app_id_is_rejected(tmp_path: Path) -> None:
    executable = tmp_path / "safe.exe"
    executable.write_bytes(b"fixture")

    with pytest.raises(AppRegistryError, match="invalid"):
        AppRegistryLoader().load(
            registry(tmp_path / "apps.json", [app(executable), app(executable)])
        )


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("executable", "relative.exe", "absolute"),
        ("sha256", "0" * 64, "hash mismatch"),
    ],
)
def test_invalid_executable_identity_is_rejected(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    executable = tmp_path / "safe.exe"
    executable.write_bytes(b"fixture")
    registration = app(executable)
    registration[field] = value

    with pytest.raises(AppRegistryError, match=message):
        AppRegistryLoader().load(registry(tmp_path / "apps.json", [registration]))


def test_size_and_count_limits_are_enforced(tmp_path: Path) -> None:
    oversized = tmp_path / "large.json"
    oversized.write_bytes(b"{} " * 10)
    with pytest.raises(AppRegistryError, match="size"):
        AppRegistryLoader(max_bytes=2).load(oversized)

    executable = tmp_path / "safe.exe"
    executable.write_bytes(b"fixture")
    with pytest.raises(AppRegistryError, match="app limit"):
        AppRegistryLoader(max_apps=1).load(
            registry(
                tmp_path / "many.json",
                [app(executable), app(executable, app_id="second")],
            )
        )


def test_publisher_is_verified_and_can_be_required(tmp_path: Path) -> None:
    executable = tmp_path / "safe.exe"
    executable.write_bytes(b"fixture")
    verifier = PublisherVerifier(True)
    path = registry(
        tmp_path / "signed.json",
        [app(executable, publisher="CN=Trusted Publisher")],
    )

    loaded = AppRegistryLoader(publisher_verifier=verifier, require_publisher=True).load(path)

    assert loaded["safe-app"].publisher == "CN=Trusted Publisher"
    assert verifier.calls == [(executable.resolve(), "CN=Trusted Publisher")]


def test_missing_or_unverified_publisher_is_rejected(tmp_path: Path) -> None:
    executable = tmp_path / "safe.exe"
    executable.write_bytes(b"fixture")
    unsigned = registry(tmp_path / "unsigned.json", [app(executable)])
    with pytest.raises(AppRegistryError, match="required"):
        AppRegistryLoader(require_publisher=True).load(unsigned)

    rejected = registry(
        tmp_path / "rejected.json",
        [app(executable, publisher="CN=Unexpected")],
    )
    with pytest.raises(AppRegistryError, match="verification"):
        AppRegistryLoader(publisher_verifier=PublisherVerifier(False)).load(rejected)


def test_distinct_runtime_identity_and_publishers_are_verified(tmp_path: Path) -> None:
    launcher = tmp_path / "launcher.exe"
    runtime = tmp_path / "runtime.exe"
    launcher.write_bytes(b"launcher")
    runtime.write_bytes(b"runtime")
    verifier = PublisherVerifier(True)
    registration = app(
        launcher,
        publisher="CN=Launcher",
        runtime_executable=str(runtime.resolve()),
        runtime_sha256=hashlib.sha256(b"runtime").hexdigest(),
        runtime_publisher="CN=Runtime",
    )

    loaded = AppRegistryLoader(publisher_verifier=verifier, require_publisher=True).load(
        registry(tmp_path / "runtime.json", [registration])
    )["safe-app"]

    assert loaded.runtime_path == runtime.resolve()
    assert loaded.matches_runtime_executable(runtime)
    assert verifier.calls == [
        (launcher.resolve(), "CN=Launcher"),
        (runtime.resolve(), "CN=Runtime"),
    ]


def test_runtime_identity_must_be_complete_and_publisher_can_be_required(
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "launcher.exe"
    runtime = tmp_path / "runtime.exe"
    launcher.write_bytes(b"launcher")
    runtime.write_bytes(b"runtime")

    incomplete = app(launcher, runtime_executable=str(runtime.resolve()))
    with pytest.raises(AppRegistryError, match="invalid"):
        AppRegistryLoader().load(registry(tmp_path / "incomplete.json", [incomplete]))

    unsigned_runtime = app(
        launcher,
        publisher="CN=Launcher",
        runtime_executable=str(runtime.resolve()),
        runtime_sha256=hashlib.sha256(b"runtime").hexdigest(),
    )
    with pytest.raises(AppRegistryError, match="runtime executable publisher is required"):
        AppRegistryLoader(
            publisher_verifier=PublisherVerifier(True),
            require_publisher=True,
        ).load(registry(tmp_path / "unsigned-runtime.json", [unsigned_runtime]))
