from __future__ import annotations

from .benchmark_collection import (
    BenchmarkCollectionCell,
    BenchmarkConditionRunner,
    BenchmarkRunOutcome,
)
from .benchmark_tasks import BenchmarkTask


class BenchmarkRunnerChainError(RuntimeError):
    pass


class BenchmarkConditionRunnerChain:
    """Dispatch a cell to exactly one condition runner without broadening support."""

    def __init__(self, runners: tuple[BenchmarkConditionRunner, ...]) -> None:
        if not runners:
            raise ValueError("runner chain cannot be empty")
        self._runners = runners

    def supports(self, cell: BenchmarkCollectionCell, task: BenchmarkTask) -> bool:
        return sum(runner.supports(cell, task) for runner in self._runners) == 1

    def run(
        self,
        cell: BenchmarkCollectionCell,
        task: BenchmarkTask,
    ) -> BenchmarkRunOutcome:
        matches = tuple(runner for runner in self._runners if runner.supports(cell, task))
        if len(matches) != 1:
            raise BenchmarkRunnerChainError("BENCHMARK_RUNNER_DISPATCH_AMBIGUOUS")
        return matches[0].run(cell, task)
