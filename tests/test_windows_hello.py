import hashlib
import json
import subprocess
from collections.abc import Mapping
from pathlib import Path

from javis_core import (
    ConsentResult,
    HelloHelperRequest,
    HelperRunResult,
    WindowsHelloHelperVerifier,
    load_windows_hello_verifier,
)


class Runner:
    def __init__(self, result: ConsentResult = ConsentResult.VERIFIED) -> None:
        self.result = result
        self.mode = "valid"
        self.calls: list[tuple[Path, bytes, Mapping[str, str]]] = []

    def run(
        self,
        executable: Path,
        arguments: tuple[str, ...],
        payload: bytes,
        *,
        timeout_seconds: float,
        max_stdout_bytes: int,
        environment: Mapping[str, str],
    ) -> HelperRunResult:
        self.calls.append((executable, payload, environment))
        request = HelloHelperRequest.model_validate_json(payload)
        challenge = request.challenge
        if self.mode == "wrong_challenge":
            challenge = "00000000-0000-0000-0000-000000000000"  # type: ignore[assignment]
        response = json.dumps(
            {
                "schema_version": "1.0",
                "challenge": str(challenge),
                "result": self.result.value,
            }
        ).encode()
        if self.mode == "malformed":
            response = b"not-json"
        return HelperRunResult(
            returncode=1 if self.mode == "nonzero" else 0,
            stdout=response,
            timed_out=self.mode == "timeout",
        )


def verifier(tmp_path: Path, runner: Runner) -> tuple[WindowsHelloHelperVerifier, Path]:
    executable = tmp_path / "hello-helper.exe"
    executable.write_bytes(b"signed helper fixture")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    return WindowsHelloHelperVerifier(executable.resolve(), digest, runner=runner), executable


def test_verified_response_requires_matching_challenge(tmp_path: Path) -> None:
    runner = Runner()
    hello, _executable = verifier(tmp_path, runner)

    assert hello.request_verification("Approve bound action") is ConsentResult.VERIFIED
    assert len(runner.calls) == 1
    payload = HelloHelperRequest.model_validate_json(runner.calls[0][1])
    assert payload.message == "Approve bound action"


def test_canceled_response_is_preserved(tmp_path: Path) -> None:
    hello, _executable = verifier(tmp_path, Runner(ConsentResult.CANCELED))
    assert hello.request_verification("Approve") is ConsentResult.CANCELED


def test_changed_helper_is_rejected_before_launch(tmp_path: Path) -> None:
    runner = Runner()
    hello, executable = verifier(tmp_path, runner)
    executable.write_bytes(b"replacement")

    assert hello.request_verification("Approve") is ConsentResult.FAILED
    assert runner.calls == []


def test_wrong_challenge_malformed_nonzero_and_timeout_fail(tmp_path: Path) -> None:
    for index, mode in enumerate(("wrong_challenge", "malformed", "nonzero", "timeout")):
        runner = Runner()
        runner.mode = mode
        case = tmp_path / str(index)
        case.mkdir()
        hello, _executable = verifier(case, runner)
        assert hello.request_verification("Approve") is ConsentResult.FAILED


def test_oversized_message_is_rejected_without_launch(tmp_path: Path) -> None:
    runner = Runner()
    hello, _executable = verifier(tmp_path, runner)
    assert hello.request_verification("x" * 1_025) is ConsentResult.FAILED
    assert runner.calls == []


def test_maximum_korean_message_fits_bounded_request(tmp_path: Path) -> None:
    runner = Runner()
    hello, _executable = verifier(tmp_path, runner)
    assert hello.request_verification("가" * 1_024) is ConsentResult.VERIFIED
    assert len(runner.calls[0][1]) < 8_192


def test_manifest_loader_resolves_only_inside_trusted_root(tmp_path: Path) -> None:
    runner = Runner()
    host = tmp_path / "host.exe"
    host.write_bytes(b"host")
    executable = tmp_path / "native" / "hello.dll"
    executable.parent.mkdir()
    executable.write_bytes(b"helper")
    manifest = tmp_path / "hello.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.1",
                "host_executable": str(host.resolve()),
                "host_sha256": hashlib.sha256(b"host").hexdigest(),
                "entrypoint": "native/hello.dll",
                "files": [
                    {
                        "path": "native/hello.dll",
                        "sha256": hashlib.sha256(b"helper").hexdigest(),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    loaded = load_windows_hello_verifier(manifest.resolve(), tmp_path.resolve(), runner=runner)
    assert loaded.request_verification("Approve") is ConsentResult.VERIFIED


def test_manifest_loader_rejects_traversal_and_bad_hash(tmp_path: Path) -> None:
    host = tmp_path / "host.exe"
    host.write_bytes(b"host")
    outside = tmp_path.parent / "outside-helper.dll"
    outside.write_bytes(b"outside")
    manifest = tmp_path / "hello.json"
    for executable, digest in (("../outside-helper.dll", "0" * 64), ("helper.dll", "BAD")):
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": "1.1",
                    "host_executable": str(host.resolve()),
                    "host_sha256": hashlib.sha256(b"host").hexdigest(),
                    "entrypoint": executable,
                    "files": [{"path": executable, "sha256": digest}],
                }
            ),
            encoding="utf-8",
        )
        try:
            load_windows_hello_verifier(manifest.resolve(), tmp_path.resolve())
        except ValueError:
            pass
        else:
            raise AssertionError("untrusted Hello manifest was accepted")


def test_published_helper_rejects_malformed_input_without_output() -> None:
    executable = (
        Path(__file__).parents[1]
        / "native"
        / "WindowsHelloHelper"
        / "publish-fdd"
        / "Javis.WindowsHelloHelper.dll"
    )
    if not executable.is_file():
        return
    host = Path(r"C:\Program Files\dotnet\dotnet.exe")
    completed = subprocess.run(  # noqa: S603 - repository-built, hash-pinned helper
        [str(host), str(executable)],
        input=b"{}\n",
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert completed.returncode != 0
    assert completed.stdout == b""
    assert completed.stderr == b""
