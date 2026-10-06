from __future__ import annotations

import math
import queue
import sys
from array import array
from collections.abc import Buffer, Callable, Iterable, Mapping, Sequence
from contextlib import suppress
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .cancellation import CancellationToken
from .voice import AudioFrame

if TYPE_CHECKING:
    from .stt_bundle import LoadedSttBundle
    from .wakeword_bundle import LoadedWakeWordBundle


class WakeModel(Protocol):
    def predict(self, audio: object) -> Mapping[str, object]: ...

    def reset(self) -> None: ...


class OnnxNodeLike(Protocol):
    name: str
    shape: Sequence[object]
    type: str


class OnnxSessionLike(Protocol):
    def get_inputs(self) -> Sequence[OnnxNodeLike]: ...

    def get_outputs(self) -> Sequence[OnnxNodeLike]: ...

    def run(
        self,
        output_names: list[str] | None,
        input_feed: Mapping[str, object],
    ) -> Sequence[object]: ...


class WhisperSegment(Protocol):
    text: str


class WhisperModelLike(Protocol):
    def transcribe(
        self,
        audio: object,
        **options: object,
    ) -> tuple[Iterable[WhisperSegment], object]: ...


def _numpy_frame(pcm_s16le: bytes) -> object:
    try:
        np = import_module("numpy")
    except ImportError as error:
        raise RuntimeError("voice adapters require the optional numpy dependency") from error
    return np.frombuffer(pcm_s16le, dtype=np.int16)


def _numpy_audio(frames: tuple[AudioFrame, ...]) -> object:
    try:
        import numpy as np
    except ImportError as error:
        raise RuntimeError("voice adapters require the optional numpy dependency") from error
    joined = b"".join(frame.pcm_s16le for frame in frames)
    return np.frombuffer(joined, dtype=np.int16).astype(np.float32) / 32768.0


class OpenWakeWordDetector:
    """Adapter for an injected openWakeWord Model instance."""

    def __init__(
        self,
        model: WakeModel,
        wakeword: str,
        *,
        pcm_converter: Callable[[bytes], object] = _numpy_frame,
    ) -> None:
        if not wakeword:
            raise ValueError("wakeword must not be empty")
        self._model = model
        self._wakeword = wakeword
        self._pcm_converter = pcm_converter

    @classmethod
    def from_local_model(
        cls,
        model_path: Path,
        wakeword: str,
        *,
        feature_model_directory: Path | None = None,
    ) -> OpenWakeWordDetector:
        wakeword_model = _local_onnx_file(model_path, "wake word model")
        feature_directory = feature_model_directory or model_path.parent
        if feature_directory.is_symlink() or not feature_directory.is_dir():
            raise ValueError("feature model directory must be a local non-symlink directory")
        feature_directory = feature_directory.resolve(strict=True)
        melspectrogram = _local_onnx_file(
            feature_directory / "melspectrogram.onnx",
            "melspectrogram model",
        )
        embedding = _local_onnx_file(
            feature_directory / "embedding_model.onnx",
            "embedding model",
        )
        return cls._from_local_onnx_paths(
            wakeword_model,
            melspectrogram,
            embedding,
            wakeword,
        )

    @classmethod
    def from_local_bundle(cls, bundle: LoadedWakeWordBundle) -> OpenWakeWordDetector:
        if not bundle.verify():
            raise ValueError("wake-word bundle integrity verification failed")
        return cls._from_local_onnx_paths(
            bundle.wakeword_path,
            bundle.melspectrogram_path,
            bundle.embedding_path,
            bundle.wakeword_key,
        )

    @classmethod
    def _from_local_onnx_paths(
        cls,
        wakeword_model: Path,
        melspectrogram: Path,
        embedding: Path,
        wakeword: str,
    ) -> OpenWakeWordDetector:
        try:
            Model = import_module("openwakeword.model").Model
        except ImportError as error:
            raise RuntimeError("openwakeword optional dependency is not installed") from error
        try:
            model = Model(
                wakeword_models=[str(wakeword_model)],
                inference_framework="onnx",
                melspec_model_path=str(melspectrogram),
                embedding_model_path=str(embedding),
            )
        except Exception as error:
            raise RuntimeError("local openWakeWord model initialization failed") from error
        return cls(model, wakeword)

    def score(self, frame: AudioFrame) -> float:
        if frame.sample_rate != 16_000 or frame.channels != 1:
            raise ValueError("openWakeWord requires mono 16 kHz PCM")
        prediction = self._model.predict(self._pcm_converter(frame.pcm_s16le))
        raw_score = prediction.get(self._wakeword, 0.0)
        try:
            score = float(raw_score)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.0
        if not math.isfinite(score):
            return 0.0
        return min(1.0, max(0.0, score))

    def reset(self) -> None:
        self._model.reset()


def _repcnn_waveform(pcm_s16le: bytes) -> object:
    try:
        np = import_module("numpy")
    except ImportError as error:
        raise RuntimeError("RepCNN wake-word adapter requires numpy") from error
    waveform = np.frombuffer(pcm_s16le, dtype=np.int16).astype(np.float32)
    waveform /= 32768.0
    return waveform.reshape(1, -1)


def _repcnn_score(output: object) -> float:
    try:
        np = import_module("numpy")
    except ImportError as error:
        raise RuntimeError("RepCNN wake-word adapter requires numpy") from error
    values = np.asarray(output)
    if values.dtype != np.float32 or values.ndim != 1 or values.size != 1:
        raise ValueError("RepCNN output must be one float32 score")
    return float(values[0])


class RepCnnWakeWordDetector:
    """Experimental fixed-window adapter for a pinned local RepCNN ONNX model."""

    def __init__(
        self,
        session: OnnxSessionLike,
        *,
        window_samples: int = 48_000,
        hop_samples: int = 6_400,
        waveform_converter: Callable[[bytes], object] = _repcnn_waveform,
        score_converter: Callable[[object], float] = _repcnn_score,
    ) -> None:
        if window_samples < 1 or not 1 <= hop_samples <= window_samples:
            raise ValueError("invalid RepCNN window configuration")
        inputs = tuple(session.get_inputs())
        outputs = tuple(session.get_outputs())
        if len(inputs) != 1 or len(outputs) != 1:
            raise ValueError("RepCNN model must have one input and one output")
        input_node, output_node = inputs[0], outputs[0]
        if (
            input_node.type != "tensor(float)"
            or len(input_node.shape) != 2
            or output_node.type != "tensor(float)"
            or len(output_node.shape) != 1
            or not input_node.name
            or not output_node.name
        ):
            raise ValueError("RepCNN model input/output contract is incompatible")
        fixed_time = input_node.shape[1]
        input_batch = input_node.shape[0]
        output_batch = output_node.shape[0]
        if (
            (isinstance(input_batch, int) and input_batch != 1)
            or (isinstance(output_batch, int) and output_batch != 1)
        ):
            raise ValueError("RepCNN model batch size is incompatible")
        if isinstance(fixed_time, int) and fixed_time != window_samples:
            raise ValueError("RepCNN fixed input length does not match window")
        self._session = session
        self._input_name = input_node.name
        self._output_name = output_node.name
        self._window_bytes = window_samples * 2
        self._hop_samples = hop_samples
        self._waveform_converter = waveform_converter
        self._score_converter = score_converter
        self._buffer = bytearray()
        self._primed = False
        self._samples_since_inference = 0

    @classmethod
    def from_local_model(
        cls,
        model_path: Path,
        expected_sha256: str,
        *,
        window_samples: int = 48_000,
        hop_samples: int = 6_400,
        max_model_bytes: int = 16 * 1024 * 1024,
    ) -> RepCnnWakeWordDetector:
        if (
            len(expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_sha256)
            or max_model_bytes < 1
        ):
            raise ValueError("invalid RepCNN model integrity configuration")
        if (
            model_path.is_symlink()
            or model_path.suffix.lower() != ".onnx"
            or not model_path.is_file()
        ):
            raise ValueError("RepCNN model must be a local non-symlink ONNX file")
        try:
            with model_path.open("rb") as stream:
                model_bytes = stream.read(max_model_bytes + 1)
        except OSError as error:
            raise ValueError("RepCNN model could not be read") from error
        if len(model_bytes) > max_model_bytes:
            raise ValueError("RepCNN model exceeded size limit")
        if sha256(model_bytes).hexdigest() != expected_sha256:
            raise ValueError("RepCNN model integrity verification failed")
        try:
            InferenceSession = import_module("onnxruntime").InferenceSession
            session = InferenceSession(
                model_bytes,
                providers=["CPUExecutionProvider"],
            )
        except Exception as error:
            raise RuntimeError("local RepCNN model initialization failed") from error
        return cls(
            session,
            window_samples=window_samples,
            hop_samples=hop_samples,
        )

    def score(self, frame: AudioFrame) -> float:
        if (
            frame.sample_rate != 16_000
            or frame.channels != 1
            or len(frame.pcm_s16le) % 2
        ):
            raise ValueError("RepCNN requires mono 16 kHz signed 16-bit PCM")
        samples = len(frame.pcm_s16le) // 2
        self._buffer.extend(frame.pcm_s16le)
        if len(self._buffer) > self._window_bytes:
            del self._buffer[: len(self._buffer) - self._window_bytes]
        if len(self._buffer) < self._window_bytes:
            return 0.0
        if not self._primed:
            self._primed = True
            self._samples_since_inference = 0
        else:
            self._samples_since_inference += samples
            if self._samples_since_inference < self._hop_samples:
                return 0.0
            self._samples_since_inference %= self._hop_samples
        waveform = self._waveform_converter(bytes(self._buffer))
        outputs = self._session.run(
            [self._output_name],
            {self._input_name: waveform},
        )
        if len(outputs) != 1:
            return 0.0
        try:
            score = self._score_converter(outputs[0])
        except (TypeError, ValueError, OverflowError):
            return 0.0
        if not math.isfinite(score):
            return 0.0
        return min(1.0, max(0.0, score))

    def reset(self) -> None:
        self._buffer.clear()
        self._primed = False
        self._samples_since_inference = 0


class PcmEnergyVad:
    """Small offline RMS gate; deployment thresholds must be calibrated per microphone."""

    def __init__(self, *, rms_threshold: int = 500) -> None:
        if not 0 <= rms_threshold <= 32_767:
            raise ValueError("RMS threshold must fit signed 16-bit PCM")
        self._threshold = rms_threshold

    def is_speech(self, frame: AudioFrame) -> bool:
        if frame.channels != 1 or len(frame.pcm_s16le) % 2:
            return False
        samples = array("h")
        samples.frombytes(frame.pcm_s16le)
        if sys.byteorder != "little":
            samples.byteswap()
        if not samples:
            return False
        mean_square = sum(sample * sample for sample in samples) / len(samples)
        return math.sqrt(mean_square) >= self._threshold


def _local_onnx_file(path: Path, label: str) -> Path:
    if path.suffix.lower() != ".onnx" or path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be an existing local non-symlink ONNX file")
    return path.resolve(strict=True)


class FasterWhisperSpeechToText:
    """Offline faster-whisper adapter with bounded output and Korean default."""

    def __init__(
        self,
        model: WhisperModelLike,
        *,
        language: str = "ko",
        max_transcript_characters: int = 10_000,
        audio_converter: Callable[[tuple[AudioFrame, ...]], object] = _numpy_audio,
    ) -> None:
        if not language or max_transcript_characters < 1:
            raise ValueError("invalid speech-to-text configuration")
        self._model = model
        self._language = language
        self._max_characters = max_transcript_characters
        self._audio_converter = audio_converter

    @classmethod
    def from_local_model(
        cls,
        model_path: Path,
        *,
        language: str = "ko",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> FasterWhisperSpeechToText:
        if not model_path.is_dir():
            raise ValueError("Whisper model must be an existing local directory")
        try:
            WhisperModel = import_module("faster_whisper").WhisperModel
        except ImportError as error:
            raise RuntimeError("faster-whisper optional dependency is not installed") from error
        model = WhisperModel(
            str(model_path.resolve()),
            device=device,
            compute_type=compute_type,
            local_files_only=True,
        )
        return cls(model, language=language)

    @classmethod
    def from_local_bundle(
        cls,
        bundle: LoadedSttBundle,
        *,
        language: str = "ko",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> FasterWhisperSpeechToText:
        if not bundle.verify():
            raise ValueError("STT bundle integrity verification failed")
        return cls.from_local_model(
            bundle.root,
            language=language,
            device=device,
            compute_type=compute_type,
        )

    def transcribe(self, frames: tuple[AudioFrame, ...]) -> str:
        if not frames:
            return ""
        if any(frame.sample_rate != 16_000 or frame.channels != 1 for frame in frames):
            raise ValueError("faster-whisper adapter requires mono 16 kHz PCM")
        audio = self._audio_converter(frames)
        segments, _information = self._model.transcribe(
            audio,
            language=self._language,
            task="transcribe",
            beam_size=5,
            condition_on_previous_text=False,
            vad_filter=False,
        )
        pieces: list[str] = []
        characters = 0
        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            characters += len(text) + bool(pieces)
            if characters > self._max_characters:
                raise ValueError("transcript exceeded size limit")
            pieces.append(text)
        return " ".join(pieces)


class AudioCaptureError(RuntimeError):
    pass


class RawInputStream(Protocol):
    def start(self) -> object: ...

    def stop(self) -> object: ...

    def close(self) -> object: ...


class RawInputStreamFactory(Protocol):
    def __call__(self, **configuration: object) -> RawInputStream: ...


class SoundDeviceFrameSource:
    """Bounded sounddevice input source with prompt cancellation and cleanup."""

    def __init__(
        self,
        *,
        device: int | str | None = None,
        sample_rate: int = 16_000,
        frame_ms: int = 80,
        queue_capacity: int = 32,
        stream_factory: RawInputStreamFactory | None = None,
    ) -> None:
        if sample_rate != 16_000 or frame_ms < 10 or queue_capacity < 1:
            raise ValueError("invalid microphone capture configuration")
        blocksize = sample_rate * frame_ms // 1000
        if blocksize * 1000 != sample_rate * frame_ms:
            raise ValueError("frame duration must produce an integral sample count")
        self._device = device
        self._sample_rate = sample_rate
        self._blocksize = blocksize
        self._queue_capacity = queue_capacity
        self._stream_factory = stream_factory

    def frames(
        self,
        cancellation: CancellationToken,
        *,
        max_frames: int = 1_500,
    ) -> Iterable[AudioFrame]:
        if max_frames < 1:
            raise ValueError("max_frames must be positive")
        if cancellation.is_cancelled:
            return
        frame_queue: queue.Queue[bytes] = queue.Queue(maxsize=self._queue_capacity)
        errors: queue.Queue[str] = queue.Queue(maxsize=1)
        expected_bytes = self._blocksize * 2

        def callback(indata: Buffer, frames: int, _time: object, status: object) -> None:
            if status:
                self._record_capture_error(errors, "AUDIO_DEVICE_STATUS")
                return
            payload = bytes(indata)
            if frames != self._blocksize or len(payload) != expected_bytes:
                self._record_capture_error(errors, "INVALID_AUDIO_FRAME")
                return
            try:
                frame_queue.put_nowait(payload)
            except queue.Full:
                self._record_capture_error(errors, "AUDIO_QUEUE_OVERFLOW")

        factory = self._stream_factory or self._sounddevice_factory()
        stream = factory(
            samplerate=self._sample_rate,
            blocksize=self._blocksize,
            channels=1,
            dtype="int16",
            device=self._device,
            callback=callback,
        )
        yielded = 0
        try:
            stream.start()
            while not cancellation.is_cancelled and yielded < max_frames:
                try:
                    capture_error = errors.get_nowait()
                except queue.Empty:
                    pass
                else:
                    raise AudioCaptureError(capture_error)
                try:
                    payload = frame_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                yielded += 1
                yield AudioFrame(pcm_s16le=payload, sample_rate=self._sample_rate, channels=1)
        finally:
            stream.stop()
            stream.close()

    @staticmethod
    def _record_capture_error(errors: queue.Queue[str], message: str) -> None:
        with suppress(queue.Full):
            errors.put_nowait(message)

    @staticmethod
    def _sounddevice_factory() -> RawInputStreamFactory:
        try:
            sounddevice = import_module("sounddevice")
        except ImportError as error:
            raise RuntimeError("sounddevice optional dependency is not installed") from error
        return sounddevice.RawInputStream  # type: ignore[no-any-return]
