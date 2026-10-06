from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

from .agent_session import PlannerContextProvider
from .app_registry import AppRegistryLoader
from .foreground_context import (
    ForegroundCollector,
    ForegroundContextCollector,
    StableForegroundContextCollector,
)
from .process_launch import RegisteredApp
from .product_host import LoadedProductHostConfig

PlannerAppLoader = Callable[[Path], Mapping[str, RegisteredApp]]


class PrivacyMinimizedPlannerContextProvider:
    """Expose only a verified app ID or an explicit unverified marker to the Planner."""

    def __init__(self, collector: ForegroundCollector) -> None:
        self._collector = collector

    def __call__(self) -> Mapping[str, object]:
        snapshot = self._collector.capture()
        if snapshot.identity_verified and snapshot.app_id is not None:
            return {
                "foreground_app_id": snapshot.app_id,
                "foreground_identity_verified": True,
            }
        return {
            "foreground_app_id": None,
            "foreground_identity_verified": False,
        }


def build_windows_planner_context_provider(
    configuration: LoadedProductHostConfig,
    *,
    app_loader: PlannerAppLoader | None = None,
) -> PlannerContextProvider:
    load_apps = app_loader or AppRegistryLoader(require_publisher=True).load
    apps = dict(load_apps(configuration.app_registry))
    if not apps:
        raise ValueError("planner context requires registered applications")
    collector = StableForegroundContextCollector(
        ForegroundContextCollector(apps),
        require_verified=False,
    )
    return PrivacyMinimizedPlannerContextProvider(collector)
