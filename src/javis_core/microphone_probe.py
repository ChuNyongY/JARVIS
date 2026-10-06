from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from importlib import import_module
from numbers import Integral, Real
from typing import Literal, Protocol

from pydantic import Field

from .models import FrozenModel


class MicrophoneProbeError(RuntimeError):
    pass


class MicrophoneBackend(Protocol):
    def query_devices(self) -> object: ...

    def default_input_index(self) -> object: ...

    def check_input_settings(
        self,
        *,
        device: int,
        channels: int,
        dtype: str,
        samplerate: int,
    ) -> object: ...


class SoundDeviceProbeBackend:
    """Read-only adapter; no stream is created and no audio is captured."""

    def __init__(self) -> None:
        try:
            sounddevice = import_module("sounddevice")
        except ImportError as exc:
            raise MicrophoneProbeError("sounddevice optional dependency is not installed") from exc
        self._sounddevice = sounddevice

    def query_devices(self) -> object:
        return self._sounddevice.query_devices()

    def default_input_index(self) -> object:
        return self._sounddevice.default.device[0]

    def check_input_settings(
        self,
        *,
        device: int,
        channels: int,
        dtype: str,
        samplerate: int,
    ) -> object:
        return self._sounddevice.check_input_settings(
            device=device,
            channels=channels,
            dtype=dtype,
            samplerate=samplerate,
        )


ProbeErrorCode = Literal["NONE", "FORMAT_CHECK_FAILED"]


class MicrophoneDeviceProbe(FrozenModel):
    index: int = Field(ge=0, le=255)
    max_input_channels: int = Field(ge=1, le=1_024)
    default_sample_rate: float = Field(gt=0, le=384_000)
    is_default: bool
    compatible: bool
    error_code: ProbeErrorCode


class MicrophoneProbeReport(FrozenModel):
    enumerated_device_count: int = Field(ge=0, le=256)
    input_device_count: int = Field(ge=0, le=256)
    compatible_device_count: int = Field(ge=0, le=256)
    devices: tuple[MicrophoneDeviceProbe, ...]


def probe_microphones(
    backend: MicrophoneBackend | None = None,
    *,
    max_devices: int = 256,
) -> MicrophoneProbeReport:
    if not 1 <= max_devices <= 256:
        raise ValueError("max_devices must be within 1..256")
    provider = backend or SoundDeviceProbeBackend()
    try:
        raw_devices = provider.query_devices()
        default_index = _optional_index(provider.default_input_index())
    except Exception as exc:
        raise MicrophoneProbeError("microphone backend query failed") from exc
    if not isinstance(raw_devices, Sequence) or isinstance(raw_devices, (str, bytes)):
        raise MicrophoneProbeError("microphone backend returned malformed device data")
    if len(raw_devices) > max_devices:
        raise MicrophoneProbeError("microphone device count exceeded limit")
    if default_index is not None and default_index >= len(raw_devices):
        raise MicrophoneProbeError("microphone backend returned an invalid default device")

    results: list[MicrophoneDeviceProbe] = []
    for index, raw_device in enumerate(raw_devices):
        channels, sample_rate = _device_capabilities(raw_device)
        if channels == 0:
            continue
        compatible = True
        error_code: ProbeErrorCode = "NONE"
        try:
            provider.check_input_settings(
                device=index,
                channels=1,
                dtype="int16",
                samplerate=16_000,
            )
        except Exception:
            compatible = False
            error_code = "FORMAT_CHECK_FAILED"
        results.append(
            MicrophoneDeviceProbe(
                index=index,
                max_input_channels=channels,
                default_sample_rate=sample_rate,
                is_default=index == default_index,
                compatible=compatible,
                error_code=error_code,
            )
        )
    return MicrophoneProbeReport(
        enumerated_device_count=len(raw_devices),
        input_device_count=len(results),
        compatible_device_count=sum(item.compatible for item in results),
        devices=tuple(results),
    )


def _optional_index(value: object) -> int | None:
    if value is None or value == -1:
        return None
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise MicrophoneProbeError("default input index is malformed")
    index = int(value)
    if index < 0:
        raise MicrophoneProbeError("default input index is malformed")
    return index


def _device_capabilities(raw_device: object) -> tuple[int, float]:
    if not isinstance(raw_device, Mapping):
        raise MicrophoneProbeError("microphone device entry is malformed")
    channels_value = raw_device.get("max_input_channels")
    rate_value = raw_device.get("default_samplerate")
    if isinstance(channels_value, bool) or not isinstance(channels_value, Integral):
        raise MicrophoneProbeError("microphone channel count is malformed")
    if isinstance(rate_value, bool) or not isinstance(rate_value, Real):
        raise MicrophoneProbeError("microphone sample rate is malformed")
    channels = int(channels_value)
    sample_rate = float(rate_value)
    if not 0 <= channels <= 1_024 or not math.isfinite(sample_rate) or sample_rate <= 0:
        raise MicrophoneProbeError("microphone device capabilities are out of bounds")
    return channels, sample_rate
