import pytest

from javis_core.microphone_probe import MicrophoneProbeError, probe_microphones


class FakeBackend:
    def __init__(
        self,
        devices: object,
        *,
        default: object = 0,
        incompatible: tuple[int, ...] = (),
    ) -> None:
        self.devices = devices
        self.default = default
        self.incompatible = incompatible
        self.checks: list[dict[str, object]] = []

    def query_devices(self) -> object:
        return self.devices

    def default_input_index(self) -> object:
        return self.default

    def check_input_settings(self, **settings: object) -> None:
        self.checks.append(settings)
        if settings["device"] in self.incompatible:
            raise RuntimeError("private driver error")


def device(channels: object, rate: object = 48_000.0) -> dict[str, object]:
    return {
        "name": "must not be returned",
        "hostapi": "must not be returned",
        "max_input_channels": channels,
        "default_samplerate": rate,
    }


def test_probe_reports_only_bounded_capabilities() -> None:
    backend = FakeBackend(
        [device(2), device(0), device(1)],
        default=0,
        incompatible=(2,),
    )

    report = probe_microphones(backend)

    assert report.enumerated_device_count == 3
    assert report.input_device_count == 2
    assert report.compatible_device_count == 1
    assert report.devices[0].is_default
    assert report.devices[1].error_code == "FORMAT_CHECK_FAILED"
    serialized = report.model_dump_json()
    assert "must not be returned" not in serialized
    assert backend.checks == [
        {"device": 0, "channels": 1, "dtype": "int16", "samplerate": 16_000},
        {"device": 2, "channels": 1, "dtype": "int16", "samplerate": 16_000},
    ]


@pytest.mark.parametrize(
    "devices",
    [
        "not-a-sequence-of-devices",
        [device("two")],
        [device(1, float("nan"))],
        [object()],
    ],
)
def test_malformed_backend_data_fails_closed(devices: object) -> None:
    with pytest.raises(MicrophoneProbeError):
        probe_microphones(FakeBackend(devices))


def test_excessive_device_count_is_rejected_before_checks() -> None:
    backend = FakeBackend([device(1)] * 257)

    with pytest.raises(MicrophoneProbeError, match="count"):
        probe_microphones(backend)

    assert backend.checks == []


def test_backend_query_exception_is_redacted() -> None:
    class BrokenBackend(FakeBackend):
        def query_devices(self) -> object:
            raise RuntimeError("secret device name")

    with pytest.raises(MicrophoneProbeError) as captured:
        probe_microphones(BrokenBackend([]))

    assert "secret device name" not in str(captured.value)


def test_invalid_default_index_is_rejected() -> None:
    with pytest.raises(MicrophoneProbeError, match="default"):
        probe_microphones(FakeBackend([device(1)], default=4))
