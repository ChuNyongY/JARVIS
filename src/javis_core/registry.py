from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType

from .models import CapabilitySpec, ExecutorKind, RiskLevel


class UnknownCapabilityError(LookupError):
    pass


class CapabilityRegistry:
    def __init__(self, capabilities: Iterable[CapabilitySpec]) -> None:
        items: dict[str, CapabilitySpec] = {}
        for capability in capabilities:
            if capability.capability_id in items:
                raise ValueError(f"duplicate capability: {capability.capability_id}")
            items[capability.capability_id] = capability
        self._items: Mapping[str, CapabilitySpec] = MappingProxyType(items)

    def get(self, capability_id: str) -> CapabilitySpec | None:
        return self._items.get(capability_id)

    def require(self, capability_id: str) -> CapabilitySpec:
        capability = self.get(capability_id)
        if capability is None:
            raise UnknownCapabilityError(f"unknown capability: {capability_id}")
        return capability

    def list(self) -> tuple[CapabilitySpec, ...]:
        return tuple(self._items[key] for key in sorted(self._items))


def default_registry() -> CapabilityRegistry:
    def spec(
        capability_id: str,
        description: str,
        risk: RiskLevel,
        executors: tuple[ExecutorKind, ...],
        *,
        reversible: bool = True,
        external: bool = False,
        sensitive: bool = False,
    ) -> CapabilitySpec:
        return CapabilitySpec(
            capability_id=capability_id,
            description=description,
            risk_floor=risk,
            allowed_executors=executors,
            reversible=reversible,
            external_effect=external,
            sensitive=sensitive,
        )

    return CapabilityRegistry(
        (
            spec(
                "system.info.read",
                "Read system information",
                RiskLevel.R0,
                ("native", "powershell"),
            ),
            spec(
                "process.launch",
                "Launch an application",
                RiskLevel.R1,
                ("native", "powershell"),
            ),
            spec(
                "explorer.folder.open",
                "Open an allowlisted directory in File Explorer",
                RiskLevel.R1,
                ("native",),
            ),
            spec(
                "settings.open",
                "Open an allowlisted Windows Settings page",
                RiskLevel.R1,
                ("native",),
            ),
            spec(
                "audio.volume.set",
                "Set default audio render endpoint volume",
                RiskLevel.R1,
                ("native",),
            ),
            spec(
                "audio.volume.read",
                "Read default audio render endpoint volume",
                RiskLevel.R0,
                ("native",),
            ),
            spec("file.list", "List files", RiskLevel.R0, ("native", "powershell")),
            spec("storage.status.read", "Read bounded storage status", RiskLevel.R0, ("native",)),
            spec("cleanup.suggest", "Suggest cleanup without mutation", RiskLevel.R0, ("native",)),
            spec("file.open", "Open a hash-pinned file", RiskLevel.R1, ("native",)),
            spec("memory.search", "Search bounded local memory", RiskLevel.R0, ("native",)),
            spec(
                "browser.fixture.read",
                "Read an allowlisted local browser fixture set",
                RiskLevel.R0,
                ("native",),
            ),
            spec(
                "browser.navigate",
                "Open an allowlisted HTTPS URL in a registered browser",
                RiskLevel.R1,
                ("native",),
            ),
            spec(
                "document.write",
                "Write a new verified DOCX fixture document",
                RiskLevel.R2,
                ("native",),
            ),
            spec(
                "package.verify",
                "Verify a hash-pinned signed installer package",
                RiskLevel.R1,
                ("native",),
                sensitive=True,
            ),
            spec(
                "package.install",
                "Install a verified package inside an attested snapshot VM",
                RiskLevel.R3,
                ("native",),
                reversible=False,
                sensitive=True,
            ),
            spec(
                "ui.element.inspect",
                "Inspect structural metadata of an authorized UI element",
                RiskLevel.R2,
                ("uia",),
                sensitive=True,
            ),
            spec(
                "notepad.text.replace",
                "Replace text in an authorized Notepad editor",
                RiskLevel.R2,
                ("uia",),
            ),
            spec("file.move", "Move a file", RiskLevel.R2, ("native", "powershell")),
            spec(
                "file.delete.trash",
                "Move a file to trash",
                RiskLevel.R2,
                ("native", "powershell"),
            ),
            spec(
                "file.delete.permanent",
                "Permanently delete a file",
                RiskLevel.R4,
                ("native", "powershell"),
                reversible=False,
            ),
            spec("message.draft", "Draft a message", RiskLevel.R1, ("native",)),
            spec(
                "message.send",
                "Send a message",
                RiskLevel.R3,
                ("native",),
                reversible=False,
                external=True,
            ),
            spec(
                "system.shutdown",
                "Shut down Windows",
                RiskLevel.R3,
                ("native", "powershell"),
                reversible=False,
            ),
            spec(
                "system.reset.commit",
                "Commit a Windows reset",
                RiskLevel.R4,
                ("native",),
                reversible=False,
                sensitive=True,
            ),
            spec(
                "system.reset.prepare",
                "Prepare a VM reset request without committing it",
                RiskLevel.R4,
                ("native",),
                sensitive=True,
            ),
        )
    )
