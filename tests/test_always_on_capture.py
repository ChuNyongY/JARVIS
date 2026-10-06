from __future__ import annotations

import threading
import time
from collections.abc import Iterable

from test_always_on_runtime import (
    BlockingExecutor,
    BlockingPlanner,
    capture,
    runtime_with_executor,
)

from javis_core import (
    AlwaysOnCapturePump,
    AudioFrame,
    CancellationToken,
    CapturePumpResult,
    CapturePumpStatus,
    CaptureStatus,
    VoiceCaptureResult,
)
from javis_core.always_on_runtime import RuntimeEventType

FRAME = AudioFrame(pcm_s16le=b"\x00\x00")


class FiniteFrameSource:
    def __init__(self, count: int) -> None:
        self._count = count

    def frames(
        self,
        cancellation: CancellationToken,
        *,
        max_frames: int,
    ) -> Iterable[AudioFrame]:
        for _index in range(min(self._count, max_frames)):
            if cancellation.is_cancelled:
                return
            yield FRAME


class BlockingFrameSource:
    def __init__(self) -> None:
        self.started = threading.Event()

    def frames(
        self,
        cancellation: CancellationToken,
        *,
        max_frames: int,
    ) -> Iterable[AudioFrame]:
        self.started.set()
        for _index in range(max_frames):
            if cancellation.is_cancelled:
                return
            yield FRAME
            time.sleep(0.005)


class ScriptedPipeline:
    def __init__(self, results: list[VoiceCaptureResult | None]) -> None:
        self._results = iter(results)
        self.cancelled = False

    def feed(self, frame: AudioFrame) -> VoiceCaptureResult | None:
        del frame
        return next(self._results, None)

    def cancel(self) -> None:
        self.cancelled = True


def test_single_capture_pump_delivers_stop_while_planning() -> None:
    planner = BlockingPlanner()
    runtime, _classifier, _planner = runtime_with_executor(
        BlockingExecutor(),
        planner=planner,
    )
    pipeline = ScriptedPipeline([capture("긴 계획"), capture("멈춰")])
    pump = AlwaysOnCapturePump(
        frame_source=FiniteFrameSource(2),
        voice_pipeline=pipeline,
        runtime=runtime,
    )

    result = pump.run(max_frames=2)
    planner.release.set()
    completion = runtime.wait_for_idle(timeout_seconds=1)

    assert result.status is CapturePumpStatus.FRAME_LIMIT_REACHED
    assert result.capture_count == 2
    assert result.tasks_started == 1
    assert result.stops_accepted == 1
    assert result.last_event is not None
    assert result.last_event.event_type is RuntimeEventType.STOP_ACCEPTED
    assert completion is not None
    assert completion.reason == "PLANNING_CANCELLED"
    assert pipeline.cancelled


def test_request_stop_ends_active_capture_loop() -> None:
    runtime, _classifier, _planner = runtime_with_executor(BlockingExecutor())
    source = BlockingFrameSource()
    pipeline = ScriptedPipeline([])
    pump = AlwaysOnCapturePump(
        frame_source=source,
        voice_pipeline=pipeline,
        runtime=runtime,
    )
    results: list[CapturePumpResult] = []
    worker = threading.Thread(target=lambda: results.append(pump.run(max_frames=500)))
    worker.start()
    assert source.started.wait(timeout=1)

    assert pump.request_stop()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert len(results) == 1
    result = results[0]
    assert result.status is CapturePumpStatus.STOPPED
    assert pipeline.cancelled
    assert not pump.is_running


def test_aborted_capture_is_forwarded_without_transcript_retention() -> None:
    runtime, _classifier, _planner = runtime_with_executor(BlockingExecutor())
    aborted = VoiceCaptureResult(
        status=CaptureStatus.ABORTED,
        transcript=None,
        wake_confidence=0.8,
        frame_count=3,
        reason="STT_FAILED",
    )
    pump = AlwaysOnCapturePump(
        frame_source=FiniteFrameSource(1),
        voice_pipeline=ScriptedPipeline([aborted]),
        runtime=runtime,
    )

    result = pump.run(max_frames=1)
    completed = runtime.wait_for_idle(timeout_seconds=1)

    assert result.capture_count == 1
    assert completed is not None
    assert completed.event_type is RuntimeEventType.FAILED
    assert "transcript" not in result.model_dump_json()
