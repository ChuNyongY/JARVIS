from __future__ import annotations

import json
import unicodedata
from collections.abc import Callable
from importlib import import_module
from typing import Protocol, cast

from .voice import AudioFrame
from .vosk_bundle import LoadedVoskBundle


class VoskRecognizerLike(Protocol):
    def AcceptWaveform(self, pcm: bytes) -> bool: ...

    def Result(self) -> str: ...

    def PartialResult(self) -> str: ...

    def FinalResult(self) -> str: ...


RecognizerFactory = Callable[[], VoskRecognizerLike]
_TARGET_TOKEN = "자비스"


class VoskKoreanWakeWordDetector:
    """Experimental offline detector; it never exposes recognized text."""

    def __init__(
        self,
        recognizer_factory: RecognizerFactory,
        *,
        window_samples: int = 51_200,
        overlap_samples: int = 12_800,
        max_result_characters: int = 16_384,
    ) -> None:
        if (
            window_samples < 16_000
            or not 0 <= overlap_samples < window_samples
            or max_result_characters < 32
        ):
            raise ValueError("invalid Vosk wake-word limits")
        self._factory = recognizer_factory
        self._window_samples = window_samples
        self._overlap_bytes = overlap_samples * 2
        self._max_result_characters = max_result_characters
        self._recognizer: VoskRecognizerLike | None = self._factory()
        self._samples = 0
        self._overlap = bytearray()

    @classmethod
    def from_local_bundle(cls, bundle: LoadedVoskBundle) -> VoskKoreanWakeWordDetector:
        if not bundle.verify():
            raise ValueError("Vosk bundle integrity verification failed")
        try:
            module = import_module("vosk")
            module.SetLogLevel(-1)
            model = module.Model(str(bundle.root))
        except (ImportError, AttributeError, OSError, RuntimeError, TypeError) as error:
            raise RuntimeError("local Vosk model initialization failed") from error

        def factory() -> VoskRecognizerLike:
            return cast(VoskRecognizerLike, module.KaldiRecognizer(model, 16_000))

        return cls(factory)

    def score(self, frame: AudioFrame) -> float:
        if frame.sample_rate != 16_000 or frame.channels != 1 or len(frame.pcm_s16le) % 2:
            raise ValueError("Vosk wake-word detector requires mono 16 kHz signed-16-bit PCM")
        if self._recognizer is None and not self._restart(b""):
            return 0.0
        recognizer = self._recognizer
        if recognizer is None:
            return 0.0
        pcm = frame.pcm_s16le
        self._remember_overlap(pcm)
        self._samples += len(pcm) // 2
        try:
            endpoint = recognizer.AcceptWaveform(pcm)
            text = self._read_text(
                recognizer.Result() if endpoint else recognizer.PartialResult(),
                "text" if endpoint else "partial",
            )
            if _contains_target(text):
                self.reset()
                return 1.0
            if self._samples < self._window_samples:
                return 0.0
            final_text = self._read_text(recognizer.FinalResult(), "text")
            replay = bytes(self._overlap)
            if _contains_target(final_text):
                self.reset()
                return 1.0
            self._restart(replay)
            return 0.0
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError, json.JSONDecodeError):
            self._restart(b"")
            return 0.0

    def reset(self) -> None:
        if not self._restart(b""):
            raise RuntimeError("Vosk recognizer reset failed")

    def _read_text(self, payload: str, field: str) -> str:
        if not isinstance(payload, str) or len(payload) > self._max_result_characters:
            raise ValueError("invalid Vosk result envelope")
        document = json.loads(
            payload,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
        if not isinstance(document, dict):
            raise ValueError("invalid Vosk result envelope")
        value = document.get(field, "")
        if not isinstance(value, str):
            raise ValueError("invalid Vosk result text")
        return value

    def _remember_overlap(self, pcm: bytes) -> None:
        if not self._overlap_bytes:
            return
        self._overlap.extend(pcm)
        excess = len(self._overlap) - self._overlap_bytes
        if excess > 0:
            del self._overlap[:excess]

    def _restart(self, replay: bytes) -> bool:
        try:
            recognizer = self._factory()
            if replay:
                recognizer.AcceptWaveform(replay)
        except Exception:
            self._recognizer = None
            self._samples = 0
            self._overlap.clear()
            return False
        self._recognizer = recognizer
        self._samples = len(replay) // 2
        self._overlap = bytearray(replay[-self._overlap_bytes :])
        return True


def _contains_target(text: str) -> bool:
    normalized = unicodedata.normalize("NFKC", text)
    return _TARGET_TOKEN in normalized.split()


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate Vosk result key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"non-finite Vosk result number: {value}")
