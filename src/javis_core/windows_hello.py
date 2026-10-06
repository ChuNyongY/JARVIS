from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID, uuid4

from pydantic import Field, ValidationError

from .file_move import file_sha256
from .local_approval import ConsentResult
from .models import FrozenModel


class HelloHelperRequest(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    challenge: UUID
    message: str = Field(min_length=1, max_length=1_024)


class HelloHelperResponse(FrozenModel):
    schema_version: Literal["1.0"] = "1.0"
    challenge: UUID
    result: ConsentResult


class HelloHelperManifest(FrozenModel):
    schema_version: Literal["1.1"] = "1.1"
    host_executable: str = Field(min_length=1, max_length=512)
    host_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    entrypoint: str = Field(min_length=1, max_length=512)
    files: tuple[HelloHelperArtifact, ...] = Field(min_length=1, max_length=16)


class HelloHelperArtifact(FrozenModel):
    path: str = Field(min_length=1, max_length=512)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class HelperRunResult(FrozenModel):
    returncode: int
    stdout: bytes = Field(repr=False)
    timed_out: bool = False


class HelloHelperRunner(Protocol):
    def run(
        self,
        executable: Path,
        arguments: tuple[str, ...],
        payload: bytes,
        *,
        timeout_seconds: float,
        max_stdout_bytes: int,
        environment: Mapping[str, str],
    ) -> HelperRunResult: ...


class BoundedHelloHelperRunner:
    """Run a helper without shell expansion and cap bytes read from stdout."""

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
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        process = subprocess.Popen(  # noqa: S603 - exact pre-hashed executable
            [str(executable), *arguments],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
            env=dict(environment),
            startupinfo=startupinfo,
        )
        if process.stdin is None or process.stdout is None:
            process.kill()
            process.wait()
            raise OSError("helper pipes unavailable")
        stdout = process.stdout
        output: list[bytes] = []
        read_error: list[BaseException] = []

        def read_stdout() -> None:
            try:
                output.append(stdout.read(max_stdout_bytes + 1))
            except BaseException as error:
                read_error.append(error)

        try:
            process.stdin.write(payload)
            process.stdin.close()
            reader = threading.Thread(target=read_stdout, daemon=True)
            reader.start()
            reader.join(timeout_seconds)
            if reader.is_alive():
                process.kill()
                process.wait()
                reader.join(1)
                return HelperRunResult(returncode=process.returncode, stdout=b"", timed_out=True)
            raw = output[0] if output else b""
            if len(raw) > max_stdout_bytes:
                process.kill()
                process.wait()
                return HelperRunResult(returncode=process.returncode, stdout=raw)
            try:
                returncode = process.wait(timeout=max(0.1, timeout_seconds))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                return HelperRunResult(returncode=process.returncode, stdout=raw, timed_out=True)
            if read_error:
                raise OSError("could not read helper response") from read_error[0]
            return HelperRunResult(returncode=returncode, stdout=raw)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            stdout.close()


class WindowsHelloHelperVerifier:
    """Fail-closed IPC client for a separately built HWND-owning Hello helper."""

    def __init__(
        self,
        executable: Path,
        sha256: str,
        *,
        runner: HelloHelperRunner | None = None,
        timeout_seconds: float = 30.0,
        max_stdout_bytes: int = 4_096,
        arguments: tuple[str, ...] = (),
        pinned_files: tuple[tuple[Path, str], ...] = (),
        exact_directory: Path | None = None,
    ) -> None:
        if not executable.is_absolute() or executable.is_symlink():
            raise ValueError("Hello helper must be an absolute non-symlink path")
        if len(sha256) != 64 or any(character not in "0123456789abcdef" for character in sha256):
            raise ValueError("invalid Hello helper SHA-256")
        if timeout_seconds <= 0 or max_stdout_bytes < 128:
            raise ValueError("invalid Hello helper limits")
        try:
            self._executable = executable.resolve(strict=True)
        except OSError as error:
            raise ValueError("Hello helper is unavailable") from error
        if not self._executable.is_file():
            raise ValueError("Hello helper must be a file")
        self._sha256 = sha256
        self._runner = runner or BoundedHelloHelperRunner()
        self._timeout = timeout_seconds
        self._max_stdout_bytes = max_stdout_bytes
        self._arguments = arguments
        self._pinned_files = pinned_files
        self._exact_directory = exact_directory

    def request_verification(self, message: str) -> ConsentResult:
        if not message or len(message) > 1_024 or not self._identity_matches():
            return ConsentResult.FAILED
        request = HelloHelperRequest(challenge=uuid4(), message=message)
        payload = request.model_dump_json().encode("utf-8") + b"\n"
        if len(payload) > 8_192:
            return ConsentResult.FAILED
        try:
            result = self._runner.run(
                self._executable,
                self._arguments,
                payload,
                timeout_seconds=self._timeout,
                max_stdout_bytes=self._max_stdout_bytes,
                environment=self._minimal_environment(),
            )
        except (OSError, ValueError):
            return ConsentResult.FAILED
        if (
            result.timed_out
            or result.returncode != 0
            or not result.stdout
            or len(result.stdout) > self._max_stdout_bytes
        ):
            return ConsentResult.FAILED
        try:
            response = HelloHelperResponse.model_validate_json(result.stdout)
        except (ValidationError, ValueError):
            return ConsentResult.FAILED
        if response.challenge != request.challenge:
            return ConsentResult.FAILED
        return response.result

    def _identity_matches(self) -> bool:
        try:
            if not (
                not self._executable.is_symlink()
                and self._executable.resolve(strict=True) == self._executable
                and file_sha256(self._executable) == self._sha256
            ):
                return False
            for path, digest in self._pinned_files:
                if path.is_symlink() or path.resolve(strict=True) != path:
                    return False
                if not path.is_file() or file_sha256(path) != digest:
                    return False
            if self._exact_directory is not None:
                expected = {path.name for path, _digest in self._pinned_files}
                actual = {path.name for path in self._exact_directory.iterdir()}
                if actual != expected:
                    return False
            return True
        except OSError:
            return False

    @staticmethod
    def _minimal_environment() -> dict[str, str]:
        return {
            key: os.environ[key]
            for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")
            if key in os.environ
        }


def load_windows_hello_verifier(
    manifest_path: Path,
    trusted_root: Path,
    *,
    runner: HelloHelperRunner | None = None,
    timeout_seconds: float = 30.0,
) -> WindowsHelloHelperVerifier:
    """Load a hash-pinned helper only from inside an explicit trusted root."""
    if not manifest_path.is_absolute() or not trusted_root.is_absolute():
        raise ValueError("Hello manifest and trusted root must be absolute")
    if manifest_path.is_symlink() or trusted_root.is_symlink():
        raise ValueError("Hello manifest and trusted root cannot be symlinks")
    try:
        root = trusted_root.resolve(strict=True)
        manifest_file = manifest_path.resolve(strict=True)
    except OSError as error:
        raise ValueError("Hello manifest or trusted root is unavailable") from error
    if not root.is_dir() or not manifest_file.is_file() or not manifest_file.is_relative_to(root):
        raise ValueError("Hello manifest must be a file inside the trusted root")
    try:
        raw = manifest_file.read_bytes()
    except OSError as error:
        raise ValueError("Hello manifest cannot be read") from error
    if not raw or len(raw) > 8_192:
        raise ValueError("invalid Hello manifest size")
    try:
        manifest = HelloHelperManifest.model_validate_json(raw)
    except (ValidationError, ValueError) as error:
        raise ValueError("invalid Hello manifest") from error
    host = Path(manifest.host_executable)
    if not host.is_absolute() or host.is_symlink():
        raise ValueError("Hello host must be an absolute non-symlink path")
    try:
        host = host.resolve(strict=True)
    except OSError as error:
        raise ValueError("Hello host is unavailable") from error

    resolved_files: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for artifact in manifest.files:
        relative = Path(artifact.path)
        if relative.is_absolute() or ".." in relative.parts or "." in relative.parts:
            raise ValueError("Hello artifact path must be normalized and relative")
        try:
            path = (root / relative).resolve(strict=True)
        except OSError as error:
            raise ValueError("Hello artifact is unavailable") from error
        if not path.is_relative_to(root) or not path.is_file() or path in seen:
            raise ValueError("invalid or duplicate Hello artifact")
        seen.add(path)
        resolved_files.append((path, artifact.sha256))

    entrypoint_relative = Path(manifest.entrypoint)
    if entrypoint_relative.is_absolute() or ".." in entrypoint_relative.parts:
        raise ValueError("Hello entrypoint must be normalized and relative")
    try:
        entrypoint = (root / entrypoint_relative).resolve(strict=True)
    except OSError as error:
        raise ValueError("Hello entrypoint is unavailable") from error
    outside_entrypoint_directory = any(
        path.parent != entrypoint.parent for path, _digest in resolved_files
    )
    if entrypoint not in seen or outside_entrypoint_directory:
        raise ValueError("Hello artifacts must share the entrypoint directory")
    if entrypoint.parent.is_symlink():
        raise ValueError("Hello artifact directory cannot be a symlink")
    return WindowsHelloHelperVerifier(
        host,
        manifest.host_sha256,
        runner=runner,
        timeout_seconds=timeout_seconds,
        arguments=(str(entrypoint),),
        pinned_files=tuple(resolved_files),
        exact_directory=entrypoint.parent,
    )
