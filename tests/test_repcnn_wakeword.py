from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from javis_core import AudioFrame, RepCnnWakeWordDetector


@dataclass(frozen=True)
class Node:
    name: str
    shape: tuple[object, ...]
    type: str = "tensor(float)"


class Session:
    def __init__(self, score: object | None = None) -> None:
        self.output = score if score is not None else np.array([0.8], dtype=np.float32)
        self.calls: list[tuple[list[str] | None, dict[str, object]]] = []
        self.input = Node("waveform", ("batch", "time"))
        self.output_node = Node("score", ("batch",))

    def get_inputs(self) -> tuple[Node, ...]:
        return (self.input,)

    def get_outputs(self) -> tuple[Node, ...]:
        return (self.output_node,)

    def run(
        self,
        output_names: list[str] | None,
        input_feed: dict[str, object],
    ) -> tuple[object, ...]:
        self.calls.append((output_names, input_feed))
        return (self.output,)


def frame(value: int = 0, *, samples: int = 1_280) -> AudioFrame:
    raw = np.full(samples, value, dtype=np.int16).tobytes()
    return AudioFrame(pcm_s16le=raw)


def test_repcnn_waits_for_full_window_then_uses_hop() -> None:
    session = Session()
    detector = RepCnnWakeWordDetector(session)

    for _ in range(37):
        assert detector.score(frame()) == 0.0
    assert detector.score(frame()) == pytest.approx(0.8)
    assert len(session.calls) == 1

    for _ in range(4):
        assert detector.score(frame()) == 0.0
    assert detector.score(frame()) == pytest.approx(0.8)
    assert len(session.calls) == 2


def test_repcnn_keeps_latest_three_second_window_and_resets() -> None:
    session = Session()
    detector = RepCnnWakeWordDetector(session)

    for _ in range(37):
        detector.score(frame(10))
    detector.score(frame(20))

    waveform = session.calls[0][1]["waveform"]
    assert isinstance(waveform, np.ndarray)
    assert waveform.shape == (1, 48_000)
    assert waveform.dtype == np.float32
    assert waveform[0, -1] == pytest.approx(20 / 32768)

    detector.reset()
    assert detector.score(frame()) == 0.0
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "output",
    [
        np.array([float("nan")], dtype=np.float32),
        np.array([0.8], dtype=np.float64),
        np.array([[0.8]], dtype=np.float32),
        np.array([], dtype=np.float32),
    ],
)
def test_repcnn_invalid_output_fails_closed(output: object) -> None:
    detector = RepCnnWakeWordDetector(Session(output))
    for _ in range(37):
        detector.score(frame())
    assert detector.score(frame()) == 0.0


def test_repcnn_rejects_incompatible_audio_and_model_contract() -> None:
    detector = RepCnnWakeWordDetector(Session())
    with pytest.raises(ValueError, match="mono 16 kHz"):
        detector.score(AudioFrame(pcm_s16le=b"\0\0", sample_rate=8_000))
    with pytest.raises(ValueError, match="signed 16-bit"):
        detector.score(AudioFrame(pcm_s16le=b"\0\0\0"))

    session = Session()
    session.input = Node("waveform", (1, 32_000))
    with pytest.raises(ValueError, match="fixed input length"):
        RepCnnWakeWordDetector(session)

    session.input = Node("waveform", (2, 48_000))
    with pytest.raises(ValueError, match="batch size"):
        RepCnnWakeWordDetector(session)


def test_repcnn_does_not_hide_runtime_inference_failure() -> None:
    session = Session()

    def fail(
        output_names: list[str] | None,
        input_feed: dict[str, object],
    ) -> tuple[object, ...]:
        raise RuntimeError("backend detail must be handled by caller")

    session.run = fail  # type: ignore[method-assign]
    detector = RepCnnWakeWordDetector(session)
    for _ in range(37):
        detector.score(frame())
    with pytest.raises(RuntimeError, match="backend detail"):
        detector.score(frame())


def test_repcnn_local_loader_rejects_unpinned_or_oversized_model(tmp_path: Path) -> None:
    model = tmp_path / "candidate.onnx"
    model.write_bytes(b"not-a-model")

    with pytest.raises(ValueError, match="integrity"):
        RepCnnWakeWordDetector.from_local_model(model, "0" * 64)
    with pytest.raises(ValueError, match="size"):
        RepCnnWakeWordDetector.from_local_model(
            model,
            "0" * 64,
            max_model_bytes=2,
        )
