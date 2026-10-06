from __future__ import annotations

from dataclasses import dataclass

from javis_core.volume_scenario import S02VolumeScenarioRunner, VolumeScenarioResult


@dataclass
class _FakeBackend:
    percent: float = 47.5
    sets: int = 0

    def read_percent(self) -> float:
        return self.percent

    def set_percent(self, percent: float) -> None:
        self.sets += 1
        self.percent = percent


def test_nominal_uses_pipeline_and_restores_original() -> None:
    backend = _FakeBackend()

    result = S02VolumeScenarioRunner(lambda: backend).run()

    assert result == VolumeScenarioResult(
        success=True,
        verified_assertions=("volume_within_tolerance",),
    )
    assert backend.percent == 47.5
    assert backend.sets == 2
    assert "47.5" not in result.model_dump_json()
    assert "30" not in result.model_dump_json()


def test_retryable_app_delay_is_observed_retried_and_restored() -> None:
    backend = _FakeBackend()

    result = S02VolumeScenarioRunner(lambda: backend).run_app_delay()

    assert result.success
    assert result.verified_assertions == ("volume_within_tolerance",)
    assert backend.percent == 47.5
    assert backend.sets == 2


def test_restore_failure_is_fail_closed_and_redacted() -> None:
    class _RestoreFailureBackend(_FakeBackend):
        def set_percent(self, percent: float) -> None:
            self.sets += 1
            if self.sets == 1:
                self.percent = percent

    result = S02VolumeScenarioRunner(lambda: _RestoreFailureBackend()).run()

    assert not result.success
    assert result.error_code == "S02_VOLUME_RESTORE_FAILED"
    assert result.verified_assertions == ()
