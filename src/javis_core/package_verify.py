from __future__ import annotations

import hashlib
import re
from pathlib import Path
from time import perf_counter_ns

from pydantic import JsonValue

from .app_registry import PublisherVerifier
from .cancellation import CancellationToken
from .executable_lease import ExecutableLease, default_executable_lease
from .models import Action, VerificationResult
from .process_launch import RegisteredApp
from .runtime_models import ExecutionResult

_SHA256 = re.compile(r"[0-9a-f]{64}")
_SUFFIXES = {".appx", ".exe", ".msi", ".msix"}


class PackageVerifyExecutor:
    name = "native"

    def __init__(
        self,
        allowed_roots: tuple[Path, ...],
        publisher_verifier: PublisherVerifier,
        *,
        lease: ExecutableLease | None = None,
        max_bytes: int = 4 * 1024 * 1024 * 1024,
    ) -> None:
        if not allowed_roots or max_bytes < 1:
            raise ValueError("invalid package verifier configuration")
        roots: list[Path] = []
        for root in allowed_roots:
            if not root.is_absolute() or not root.is_dir() or root.is_symlink():
                raise ValueError("package roots must be existing absolute directories")
            roots.append(root.resolve(strict=True))
        self._roots = tuple(roots)
        self._publisher_verifier = publisher_verifier
        self._lease = lease or default_executable_lease()
        self._max_bytes = max_bytes

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        request = self._validated_request(action)
        if request is None:
            return self._failure(action, started, "INVALID_ARGUMENTS")
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED")
        package, digest, publisher = request
        registration = self._registration(package, digest, publisher)
        try:
            with self._lease.hold(registration) as identity_matches:
                if not identity_matches:
                    return self._failure(action, started, "PACKAGE_HASH_MISMATCH")
                if cancellation is not None and cancellation.is_cancelled:
                    return self._failure(action, started, "STOP_REQUESTED")
                if not self._publisher_verifier.verify(package, publisher):
                    return self._failure(action, started, "PACKAGE_PUBLISHER_MISMATCH")
        except Exception:
            return self._failure(action, started, "PACKAGE_VERIFY_FAILED")
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=True,
            observed=self._observation(digest, publisher),
            duration_ms=self._elapsed_ms(started),
        )

    def _validated_request(self, action: Action) -> tuple[Path, str, str] | None:
        if (
            action.capability_id != "package.verify"
            or set(action.arguments) != {"path", "sha256", "publisher"}
            or len(action.expected_postconditions) != 1
        ):
            return None
        raw_path = action.arguments.get("path")
        digest = action.arguments.get("sha256")
        publisher = action.arguments.get("publisher")
        if (
            not isinstance(raw_path, str)
            or not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or not isinstance(publisher, str)
            or not publisher
            or len(publisher) > 512
        ):
            return None
        package = self._allowed_package(Path(raw_path))
        if package is None or action.target_identity != f"package-sha256:{digest}":
            return None
        postcondition = action.expected_postconditions[0]
        if postcondition.predicate != "package.identity.verified" or postcondition.arguments:
            return None
        return package, digest, publisher

    def _allowed_package(self, path: Path) -> Path | None:
        if not path.is_absolute() or path.is_symlink() or path.suffix.lower() not in _SUFFIXES:
            return None
        try:
            resolved = path.resolve(strict=True)
            if (
                not resolved.is_file()
                or not 0 < resolved.stat().st_size <= self._max_bytes
                or not any(resolved.is_relative_to(root) for root in self._roots)
            ):
                return None
        except OSError:
            return None
        return resolved

    @staticmethod
    def _registration(path: Path, digest: str, publisher: str) -> RegisteredApp:
        return RegisteredApp(
            app_id="verified-package",
            executable=path,
            sha256=digest,
            publisher=publisher,
        )

    @staticmethod
    def _observation(digest: str, publisher: str) -> dict[str, JsonValue]:
        return {
            "package_sha256": digest,
            "publisher_sha256": hashlib.sha256(publisher.encode()).hexdigest(),
            "identity_verified": True,
        }

    @staticmethod
    def _elapsed_ms(started: int) -> int:
        return max(0, (perf_counter_ns() - started) // 1_000_000)

    @classmethod
    def _failure(cls, action: Action, started: int, code: str) -> ExecutionResult:
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=False,
            error_code=code,
            error_message="Package verification was not completed",
            retry_safe=True,
            duration_ms=cls._elapsed_ms(started),
        )


class PackageIdentityVerifier:
    def __init__(self, executor: PackageVerifyExecutor) -> None:
        self._executor = executor

    def verify(self, action: Action, result: ExecutionResult) -> VerificationResult:
        if result.action_id != action.action_id or result.executor != "native":
            return self._failure("PACKAGE_RESULT_BINDING_MISMATCH")
        if not result.success:
            return self._failure(result.error_code or "EXECUTION_FAILED")
        request = self._executor._validated_request(action)  # noqa: SLF001
        if request is None:
            return self._failure("INVALID_PACKAGE_CONTRACT")
        package, digest, publisher = request
        registration = self._executor._registration(package, digest, publisher)  # noqa: SLF001
        try:
            with self._executor._lease.hold(registration) as matches:  # noqa: SLF001
                if not matches:
                    return self._failure("PACKAGE_VERIFY_HASH_MISMATCH")
                if not self._executor._publisher_verifier.verify(package, publisher):  # noqa: SLF001
                    return self._failure("PACKAGE_VERIFY_PUBLISHER_MISMATCH")
        except Exception:
            return self._failure("PACKAGE_VERIFY_FAILED")
        expected = self._executor._observation(digest, publisher)  # noqa: SLF001
        if result.observed != expected:
            return self._failure("PACKAGE_RECEIPT_MISMATCH")
        return VerificationResult(
            success=True,
            observed=expected,
            reason="PACKAGE_IDENTITY_VERIFIED",
        )

    @staticmethod
    def _failure(reason: str) -> VerificationResult:
        return VerificationResult(success=False, reason=reason, recoverable=False)
