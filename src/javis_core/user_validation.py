from __future__ import annotations

import json
import os
import secrets
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol, TextIO
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from .file_lock import InterprocessFileLock
from .models import FrozenModel
from .windows_acl import WindowsUserOnlyAcl


class UserValidationError(RuntimeError):
    pass


class ValidationStep(StrEnum):
    MICROPHONE = "MICROPHONE"
    TTS_SPEAKER = "TTS_SPEAKER"
    WINDOWS_HELLO = "WINDOWS_HELLO"
    STOP_LATENCY = "STOP_LATENCY"


class ValidationStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


class ValidationMeasurement(FrozenModel):
    passed: bool
    code: str = Field(pattern=r"^[A-Z0-9_]{2,64}$")
    rating: int | None = Field(default=None, ge=1, le=5)
    latency_ms: int | None = Field(default=None, ge=0, le=120_000)


class ValidationStepResult(FrozenModel):
    step: ValidationStep
    status: ValidationStatus
    code: str = Field(pattern=r"^[A-Z0-9_]{2,64}$")
    consented: bool
    consent_id: UUID | None = None
    started_at: datetime
    finished_at: datetime
    rating: int | None = Field(default=None, ge=1, le=5)
    latency_ms: int | None = Field(default=None, ge=0, le=120_000)

    @model_validator(mode="after")
    def result_is_consistent(self) -> ValidationStepResult:
        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None:
            raise ValueError("validation timestamps must be timezone-aware")
        if self.finished_at < self.started_at:
            raise ValueError("validation finish cannot precede start")
        if self.consented != (self.consent_id is not None):
            raise ValueError("validation consent fields are inconsistent")
        if not self.consented and self.status is not ValidationStatus.SKIPPED:
            raise ValueError("unconsented validation must be skipped")
        if self.status is ValidationStatus.SKIPPED and self.consented:
            raise ValueError("consented validation cannot be skipped")
        if self.step is not ValidationStep.TTS_SPEAKER and self.rating is not None:
            raise ValueError("rating is only valid for TTS speaker validation")
        if self.step is not ValidationStep.STOP_LATENCY and self.latency_ms is not None:
            raise ValueError("latency is only valid for STOP validation")
        return self


class UserValidationReport(FrozenModel):
    schema_version: str = Field(default="1.0", pattern=r"^1\.0$")
    session_id: UUID = Field(default_factory=uuid4)
    started_at: datetime
    finished_at: datetime
    complete: bool
    results: tuple[ValidationStepResult, ...] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def report_is_consistent(self) -> UserValidationReport:
        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None:
            raise ValueError("report timestamps must be timezone-aware")
        if self.finished_at < self.started_at:
            raise ValueError("report finish cannot precede start")
        steps = tuple(result.step for result in self.results)
        if steps != tuple(ValidationStep):
            raise ValueError("validation steps must be complete and ordered")
        expected_complete = all(
            result.status is ValidationStatus.PASS for result in self.results
        )
        if self.complete != expected_complete:
            raise ValueError("validation completion flag is inconsistent")
        return self


class ConsentProvider(Protocol):
    def request(self, step: ValidationStep) -> UUID | None: ...


class ValidationRunner(Protocol):
    def __call__(self) -> ValidationMeasurement: ...


class ConsoleConsentProvider:
    """Require a new random phrase for every step; Enter and non-TTY mean skip."""

    def __init__(
        self,
        *,
        input_stream: TextIO | None = None,
        output_stream: TextIO | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._input = input_stream or sys.stdin
        self._output = output_stream or sys.stdout
        self._token_factory = token_factory or (lambda: secrets.token_hex(3))
        self._used_tokens: set[str] = set()

    def request(self, step: ValidationStep) -> UUID | None:
        if not self._input.isatty():
            return None
        token = self._token_factory()
        if (
            not token
            or len(token) > 32
            or token in self._used_tokens
            or any(character not in "0123456789abcdef" for character in token)
        ):
            raise UserValidationError("fresh consent token could not be created")
        self._used_tokens.add(token)
        phrase = f"동의 {token}"
        guidance = {
            ValidationStep.MICROPHONE: (
                "동의 직후 10초 안에 호출어는 영어식으로 "
                "'Hey Jarvis, 시스템 정보 알려줘'라고 끊지 말고 말하세요. "
            ),
            ValidationStep.TTS_SPEAKER: (
                "동의 직후 재생되는 고정 한국어 문장을 듣고 1~5점으로 "
                "평가하세요. "
            ),
            ValidationStep.WINDOWS_HELLO: (
                "동의 직후 Windows가 표시하는 인증 창에서 직접 인증하세요. "
            ),
            ValidationStep.STOP_LATENCY: (
                "동의 직후 10초 안에 호출어는 영어식으로 "
                "'Hey Jarvis, 중지'라고 끊지 말고 말하세요. "
            ),
        }[step]
        self._output.write(
            f"[{step.value}] {guidance}장치 검증을 실행하려면 '{phrase}'를 입력하세요. "
            "Enter는 건너뜁니다: "
        )
        self._output.flush()
        response = self._input.readline(128)
        if not response or response.strip() != phrase:
            return None
        return uuid4()


class UserValidationWorkflow:
    def __init__(
        self,
        consent: ConsentProvider,
        runners: Mapping[ValidationStep, ValidationRunner],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._consent = consent
        self._runners = dict(runners)
        if set(self._runners) != set(ValidationStep):
            raise ValueError("one validation runner is required for every step")
        self._clock = clock or (lambda: datetime.now(UTC))

    def run(self) -> UserValidationReport:
        report_started = self._now()
        results = tuple(self._run_step(step) for step in ValidationStep)
        report_finished = self._now()
        return UserValidationReport(
            started_at=report_started,
            finished_at=report_finished,
            complete=all(result.status is ValidationStatus.PASS for result in results),
            results=results,
        )

    def _run_step(self, step: ValidationStep) -> ValidationStepResult:
        started = self._now()
        try:
            consent_id = self._consent.request(step)
        except Exception:
            consent_id = None
        if consent_id is None:
            return ValidationStepResult(
                step=step,
                status=ValidationStatus.SKIPPED,
                code="CONSENT_NOT_GIVEN",
                consented=False,
                started_at=started,
                finished_at=self._now(),
            )
        try:
            measurement = self._runners[step]()
            return ValidationStepResult(
                step=step,
                status=(
                    ValidationStatus.PASS if measurement.passed else ValidationStatus.FAIL
                ),
                code=measurement.code,
                consented=True,
                consent_id=consent_id,
                started_at=started,
                finished_at=self._now(),
                rating=measurement.rating,
                latency_ms=measurement.latency_ms,
            )
        except Exception:
            return ValidationStepResult(
                step=step,
                status=ValidationStatus.ERROR,
                code="VALIDATION_CALLBACK_FAILED",
                consented=True,
                consent_id=consent_id,
                started_at=started,
                finished_at=self._now(),
            )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise UserValidationError("validation clock must return aware timestamps")
        return value.astimezone(UTC)


class UserValidationSink:
    """Append privacy-minimized reports to one protected JSONL file."""

    def __init__(
        self,
        path: Path,
        trusted_root: Path,
        *,
        max_bytes: int = 8 * 1024 * 1024,
        hardener: Callable[[Path], None] | None = None,
    ) -> None:
        if not 1_024 <= max_bytes <= 64 * 1024 * 1024:
            raise ValueError("validation result size limit is invalid")
        if not path.is_absolute() or not trusted_root.is_absolute():
            raise ValueError("validation paths must be absolute")
        if path.is_symlink() or trusted_root.is_symlink():
            raise ValueError("validation paths cannot be symlinks")
        root = trusted_root.resolve(strict=True)
        destination = path.resolve(strict=False)
        if not root.is_dir() or not destination.is_relative_to(root):
            raise ValueError("validation result path escaped its trusted root")
        if destination.exists() and not destination.is_file():
            raise ValueError("validation result path must be a file")
        self._path = destination
        self._max_bytes = max_bytes
        self._hardener = hardener or WindowsUserOnlyAcl().harden
        self._lock_path = destination.with_suffix(destination.suffix + ".lock")

    def append(self, report: UserValidationReport) -> None:
        payload = (
            json.dumps(
                report.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with InterprocessFileLock(self._lock_path, hardener=self._hardener):
            if self._path.is_symlink():
                raise UserValidationError("validation result symlink is not allowed")
            current = self._path.stat().st_size if self._path.exists() else 0
            if current + len(payload) > self._max_bytes:
                raise UserValidationError("validation result file exceeded size limit")
            try:
                with self._path.open("ab", buffering=0) as stream:
                    stream.write(payload)
                    os.fsync(stream.fileno())
                self._hardener(self._path)
            except OSError as error:
                raise UserValidationError("validation result could not be written") from error
