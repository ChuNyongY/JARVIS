from __future__ import annotations

import io
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from javis_core.user_validation import (
    ConsoleConsentProvider,
    UserValidationError,
    UserValidationSink,
    UserValidationWorkflow,
    ValidationMeasurement,
    ValidationRunner,
    ValidationStatus,
    ValidationStep,
)


class Consent:
    def __init__(self, accepted: set[ValidationStep]) -> None:
        self.accepted = accepted
        self.calls: list[ValidationStep] = []

    def request(self, step: ValidationStep) -> UUID | None:
        self.calls.append(step)
        return uuid4() if step in self.accepted else None


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 10, 3, tzinfo=UTC)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(milliseconds=1)
        return current


class TtyInput(io.StringIO):
    def isatty(self) -> bool:
        return True


def runners(calls: list[ValidationStep]) -> dict[ValidationStep, ValidationRunner]:
    def callback(
        step: ValidationStep,
        measurement: ValidationMeasurement,
    ) -> Callable[[], ValidationMeasurement]:
        def run() -> ValidationMeasurement:
            calls.append(step)
            return measurement

        return run

    return {
        ValidationStep.MICROPHONE: callback(
            ValidationStep.MICROPHONE,
            ValidationMeasurement(passed=True, code="VOICE_MATCHED"),
        ),
        ValidationStep.TTS_SPEAKER: callback(
            ValidationStep.TTS_SPEAKER,
            ValidationMeasurement(passed=True, code="TTS_ACCEPTED", rating=5),
        ),
        ValidationStep.WINDOWS_HELLO: callback(
            ValidationStep.WINDOWS_HELLO,
            ValidationMeasurement(passed=True, code="HELLO_VERIFIED"),
        ),
        ValidationStep.STOP_LATENCY: callback(
            ValidationStep.STOP_LATENCY,
            ValidationMeasurement(passed=True, code="STOP_DETECTED", latency_ms=24),
        ),
    }


def test_unconsented_steps_are_skipped_without_calling_devices() -> None:
    calls: list[ValidationStep] = []
    consent = Consent(set())

    report = UserValidationWorkflow(consent, runners(calls), clock=Clock()).run()

    assert not report.complete
    assert calls == []
    assert consent.calls == list(ValidationStep)
    assert all(result.status is ValidationStatus.SKIPPED for result in report.results)
    assert all(not result.consented for result in report.results)


def test_each_fresh_consent_invokes_exactly_one_runner_and_records_metrics() -> None:
    calls: list[ValidationStep] = []
    consent = Consent(set(ValidationStep))

    report = UserValidationWorkflow(consent, runners(calls), clock=Clock()).run()

    assert report.complete
    assert calls == list(ValidationStep)
    assert len({result.consent_id for result in report.results}) == 4
    assert report.results[1].rating == 5
    assert report.results[3].latency_ms == 24
    assert "transcript" not in report.model_dump_json().casefold()
    assert "audio" not in report.model_dump_json().casefold()


def test_runner_exception_is_redacted_and_does_not_stop_later_steps() -> None:
    calls: list[ValidationStep] = []
    configured = runners(calls)

    def fail() -> ValidationMeasurement:
        raise RuntimeError("private device and transcript detail")

    configured[ValidationStep.MICROPHONE] = fail
    report = UserValidationWorkflow(
        Consent(set(ValidationStep)), configured, clock=Clock()
    ).run()

    assert report.results[0].status is ValidationStatus.ERROR
    assert report.results[0].code == "VALIDATION_CALLBACK_FAILED"
    assert calls == list(ValidationStep)[1:]
    assert "private" not in report.model_dump_json()


def test_console_consent_defaults_to_skip_when_noninteractive() -> None:
    provider = ConsoleConsentProvider(
        input_stream=io.StringIO("동의 abcdef\n"),
        output_stream=io.StringIO(),
        token_factory=lambda: "abcdef",
    )

    assert provider.request(ValidationStep.MICROPHONE) is None


def test_console_consent_requires_exact_fresh_phrase() -> None:
    output = io.StringIO()
    accepted = ConsoleConsentProvider(
        input_stream=TtyInput("동의 abcdef\n"),
        output_stream=output,
        token_factory=lambda: "abcdef",
    )

    assert accepted.request(ValidationStep.MICROPHONE) is not None
    assert "동의 abcdef" in output.getvalue()
    assert "Hey Jarvis, 시스템 정보 알려줘" in output.getvalue()
    with pytest.raises(UserValidationError, match="fresh"):
        accepted.request(ValidationStep.TTS_SPEAKER)


@pytest.mark.parametrize(
    ("step", "expected_guidance"),
    [
        (ValidationStep.MICROPHONE, "Hey Jarvis, 시스템 정보 알려줘"),
        (ValidationStep.TTS_SPEAKER, "1~5점"),
        (ValidationStep.WINDOWS_HELLO, "Windows가 표시하는 인증 창"),
        (ValidationStep.STOP_LATENCY, "Hey Jarvis, 중지"),
    ],
)
def test_console_consent_explains_the_exact_device_action(
    step: ValidationStep,
    expected_guidance: str,
) -> None:
    output = io.StringIO()
    provider = ConsoleConsentProvider(
        input_stream=TtyInput("동의 abcdef\n"),
        output_stream=output,
        token_factory=lambda: "abcdef",
    )

    assert provider.request(step) is not None
    assert expected_guidance in output.getvalue()


def test_sink_appends_bounded_jsonl_without_private_payload(tmp_path: Path) -> None:
    calls: list[ValidationStep] = []
    report = UserValidationWorkflow(
        Consent(set(ValidationStep)), runners(calls), clock=Clock()
    ).run()
    hardened: list[Path] = []
    destination = (tmp_path / "state" / "user-validation.jsonl").resolve()
    sink = UserValidationSink(
        destination,
        tmp_path.resolve(),
        hardener=lambda path: hardened.append(path),
    )

    sink.append(report)
    sink.append(report)

    lines = destination.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["session_id"] == str(report.session_id)
    assert "transcript" not in destination.read_text(encoding="utf-8").casefold()
    assert destination in hardened
    assert destination.with_suffix(".jsonl.lock") in hardened


def test_sink_rejects_escape_symlink_and_size_exhaustion(tmp_path: Path) -> None:
    outside = (tmp_path.parent / "outside-validation.jsonl").resolve()
    with pytest.raises(ValueError, match="escaped"):
        UserValidationSink(outside, tmp_path.resolve(), hardener=lambda _path: None)

    link = tmp_path / "link.jsonl"
    target = tmp_path / "target.jsonl"
    target.write_text("", encoding="utf-8")
    try:
        link.symlink_to(target)
    except OSError:
        pass
    else:
        with pytest.raises(ValueError, match="symlink"):
            UserValidationSink(link, tmp_path.resolve(), hardener=lambda _path: None)

    calls: list[ValidationStep] = []
    report = UserValidationWorkflow(
        Consent(set(ValidationStep)), runners(calls), clock=Clock()
    ).run()
    sink = UserValidationSink(
        (tmp_path / "small.jsonl").resolve(),
        tmp_path.resolve(),
        max_bytes=1_024,
        hardener=lambda _path: None,
    )
    with pytest.raises(UserValidationError, match="size"):
        for _ in range(10):
            sink.append(report)
