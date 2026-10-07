from __future__ import annotations

import json
from collections.abc import Iterable

import pytest

from javis_core.voice import AudioFrame
from javis_core.vosk_wakeword import VoskKoreanWakeWordDetector


class Recognizer:
    def __init__(
        self,
        *,
        partials: Iterable[str] = (),
        results: Iterable[str] = (),
        finals: Iterable[str] = (),
        fail: bool = False,
    ) -> None:
        self._partials = iter(partials)
        self._results = iter(results)
        self._finals = iter(finals)
        self._fail = fail
        self.accepted: list[bytes] = []

    def AcceptWaveform(self, pcm: bytes) -> bool:
        if self._fail:
            raise RuntimeError("native failure")
        self.accepted.append(pcm)
        return False

    def Result(self) -> str:
        return next(self._results, json.dumps({"text": ""}))

    def PartialResult(self) -> str:
        return next(self._partials, json.dumps({"partial": ""}))

    def FinalResult(self) -> str:
        return next(self._finals, json.dumps({"text": ""}))


def frame(samples: int = 1_280) -> AudioFrame:
    return AudioFrame(pcm_s16le=b"\0\0" * samples)


def test_detects_only_exact_fixed_korean_token() -> None:
    recognizers = [
        Recognizer(partials=[json.dumps({"partial": "헤이 자비스"}, ensure_ascii=False)]),
        Recognizer(),
    ]
    detector = VoskKoreanWakeWordDetector(lambda: recognizers.pop(0))

    assert detector.score(frame()) == 1.0


def test_does_not_match_substrings_or_unmeasured_aliases() -> None:
    recognizer = Recognizer(
        partials=[
            json.dumps({"partial": "가짜비스"}, ensure_ascii=False),
            json.dumps({"partial": "저비스"}, ensure_ascii=False),
        ]
    )
    detector = VoskKoreanWakeWordDetector(lambda: recognizer)

    assert detector.score(frame()) == 0.0
    assert detector.score(frame()) == 0.0


def test_forced_finalization_detects_at_bounded_window() -> None:
    first = Recognizer(finals=[json.dumps({"text": "자비스"}, ensure_ascii=False)])
    second = Recognizer()
    recognizers = [first, second]
    detector = VoskKoreanWakeWordDetector(
        lambda: recognizers.pop(0), window_samples=16_000, overlap_samples=0
    )

    for _ in range(11):
        assert detector.score(frame()) == 0.0
    assert detector.score(frame(samples=1_920)) == 1.0


def test_window_restart_replays_only_bounded_overlap() -> None:
    first = Recognizer(finals=[json.dumps({"text": "다른 말"}, ensure_ascii=False)])
    second = Recognizer()
    recognizers = [first, second]
    detector = VoskKoreanWakeWordDetector(
        lambda: recognizers.pop(0), window_samples=16_000, overlap_samples=1_600
    )

    for _ in range(12):
        detector.score(frame())
    detector.score(frame(samples=640))

    assert [len(item) for item in second.accepted] == [3_200]


def test_malformed_json_and_native_failure_fail_closed() -> None:
    malformed = Recognizer(partials=["{"])
    replacement = Recognizer(fail=True)
    final = Recognizer()
    recognizers = [malformed, replacement, final]
    detector = VoskKoreanWakeWordDetector(lambda: recognizers.pop(0))

    assert detector.score(frame()) == 0.0
    assert detector.score(frame()) == 0.0


def test_duplicate_result_key_cannot_force_detection() -> None:
    duplicate = Recognizer(
        partials=['{"partial":"","partial":"자비스"}']
    )
    replacement = Recognizer()
    recognizers = [duplicate, replacement]
    detector = VoskKoreanWakeWordDetector(lambda: recognizers.pop(0))

    assert detector.score(frame()) == 0.0


@pytest.mark.parametrize(
    "sample_rate,channels,pcm",
    [(8_000, 1, b"\0\0"), (16_000, 2, b"\0\0"), (16_000, 1, b"\0\0\0")],
)
def test_rejects_incompatible_audio(sample_rate: int, channels: int, pcm: bytes) -> None:
    detector = VoskKoreanWakeWordDetector(lambda: Recognizer())

    with pytest.raises(ValueError, match="mono 16 kHz"):
        detector.score(
            AudioFrame(pcm_s16le=pcm, sample_rate=sample_rate, channels=channels)
        )
