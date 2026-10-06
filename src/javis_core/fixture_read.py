from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns

from .cancellation import CancellationToken
from .models import Action, VerificationResult
from .runtime_models import ExecutionResult

_FIXTURE_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_ALLOWED_SUFFIXES = {".htm", ".html", ".md", ".txt"}


class FixtureReadError(RuntimeError):
    """Stable fixture boundary failure without source text or path details."""


@dataclass(frozen=True)
class FixtureSource:
    relative_path: str
    content: bytes
    sha256: str


@dataclass(frozen=True)
class FixtureBundle:
    fixture_id: str
    sources: tuple[FixtureSource, ...]
    source_set_digest: str
    total_bytes: int


class FixtureSourceCatalog:
    def __init__(
        self,
        root: Path,
        fixtures: dict[str, tuple[str, ...]],
        *,
        max_source_bytes: int = 1_048_576,
        max_total_bytes: int = 4_194_304,
    ) -> None:
        if (
            not root.is_absolute()
            or not root.is_dir()
            or root.is_symlink()
            or not fixtures
            or not 1 <= max_source_bytes <= max_total_bytes <= 16_777_216
        ):
            raise ValueError("invalid fixture catalog configuration")
        normalized: dict[str, tuple[str, ...]] = {}
        for fixture_id, paths in fixtures.items():
            if _FIXTURE_ID.fullmatch(fixture_id) is None or not paths or len(paths) > 32:
                raise ValueError("invalid fixture declaration")
            if len(paths) != len(set(paths)):
                raise ValueError("fixture paths must be unique")
            normalized[fixture_id] = tuple(paths)
        self._root = root.resolve(strict=True)
        self._fixtures = normalized
        self._max_source_bytes = max_source_bytes
        self._max_total_bytes = max_total_bytes

    def read(self, fixture_id: str) -> FixtureBundle:
        paths = self._fixtures.get(fixture_id)
        if paths is None:
            raise FixtureReadError("fixture is not registered")
        sources: list[FixtureSource] = []
        total = 0
        for relative in paths:
            candidate = self._resolve_file(relative)
            try:
                size = candidate.stat().st_size
                if not 0 < size <= self._max_source_bytes:
                    raise FixtureReadError("fixture source size is invalid")
                content = candidate.read_bytes()
            except FixtureReadError:
                raise
            except OSError:
                raise FixtureReadError("fixture source could not be read") from None
            if len(content) != size:
                raise FixtureReadError("fixture source changed during read")
            total += len(content)
            if total > self._max_total_bytes:
                raise FixtureReadError("fixture set exceeds size limit")
            sources.append(
                FixtureSource(
                    relative_path=relative,
                    content=content,
                    sha256=hashlib.sha256(content).hexdigest(),
                )
            )
        binding = [
            {"relative_path": source.relative_path, "sha256": source.sha256}
            for source in sources
        ]
        digest = hashlib.sha256(
            json.dumps(
                binding,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        return FixtureBundle(
            fixture_id=fixture_id,
            sources=tuple(sources),
            source_set_digest=digest,
            total_bytes=total,
        )

    def _resolve_file(self, relative: str) -> Path:
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts or path.suffix.lower() not in _ALLOWED_SUFFIXES:
            raise FixtureReadError("fixture source is not allowed")
        candidate = self._root.joinpath(path)
        try:
            if candidate.is_symlink() or not candidate.is_file():
                raise FixtureReadError("fixture source is not a regular file")
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self._root)
        except FixtureReadError:
            raise
        except (OSError, ValueError):
            raise FixtureReadError("fixture source is outside the allowed root") from None
        return resolved


class FixtureReadExecutor:
    name = "native"

    def __init__(self, catalog: FixtureSourceCatalog) -> None:
        self._catalog = catalog

    def execute(
        self,
        action: Action,
        cancellation: CancellationToken | None = None,
    ) -> ExecutionResult:
        started = perf_counter_ns()
        fixture_id = self._validated_fixture_id(action)
        if fixture_id is None:
            return self._failure(action, started, "INVALID_ARGUMENTS")
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED")
        try:
            bundle = self._catalog.read(fixture_id)
        except Exception:
            return self._failure(action, started, "FIXTURE_READ_FAILED")
        if cancellation is not None and cancellation.is_cancelled:
            return self._failure(action, started, "STOP_REQUESTED")
        return ExecutionResult(
            action_id=action.action_id,
            executor="native",
            success=True,
            observed=self._observation(bundle),
            duration_ms=self._elapsed_ms(started),
        )

    @staticmethod
    def _validated_fixture_id(action: Action) -> str | None:
        fixture_id = action.arguments.get("fixture_id")
        if (
            action.capability_id != "browser.fixture.read"
            or not isinstance(fixture_id, str)
            or _FIXTURE_ID.fullmatch(fixture_id) is None
            or set(action.arguments) != {"fixture_id"}
            or action.target_identity != f"browser-fixture:{fixture_id}"
            or len(action.expected_postconditions) != 1
        ):
            return None
        postcondition = action.expected_postconditions[0]
        if postcondition.predicate != "fixture.sources.bound" or postcondition.arguments:
            return None
        return fixture_id

    @staticmethod
    def _observation(bundle: FixtureBundle) -> dict[str, object]:
        return {
            "fixture_id": bundle.fixture_id,
            "source_count": len(bundle.sources),
            "source_set_digest": bundle.source_set_digest,
            "total_bytes": bundle.total_bytes,
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
            error_message="Fixture read was not completed",
            retry_safe=True,
            duration_ms=cls._elapsed_ms(started),
        )


class FixtureReadVerifier:
    def __init__(self, catalog: FixtureSourceCatalog) -> None:
        self._catalog = catalog

    def verify(self, action: Action, result: ExecutionResult) -> VerificationResult:
        if result.action_id != action.action_id:
            return self._failure("ACTION_ID_MISMATCH")
        if result.executor != "native":
            return self._failure("EXECUTOR_MISMATCH")
        if not result.success:
            return self._failure(
                result.error_code or "EXECUTION_FAILED",
                recoverable=result.retry_safe,
            )
        fixture_id = FixtureReadExecutor._validated_fixture_id(action)  # noqa: SLF001
        if fixture_id is None:
            return self._failure("INVALID_FIXTURE_CONTRACT")
        try:
            expected = FixtureReadExecutor._observation(self._catalog.read(fixture_id))  # noqa: SLF001
        except Exception:
            return self._failure("FIXTURE_VERIFY_FAILED")
        if result.observed != expected:
            return self._failure("FIXTURE_VERIFY_MISMATCH")
        return VerificationResult(
            success=True,
            observed=expected,
            reason="FIXTURE_SOURCES_BOUND",
        )

    @staticmethod
    def _failure(reason: str, *, recoverable: bool = False) -> VerificationResult:
        return VerificationResult(success=False, reason=reason, recoverable=recoverable)
