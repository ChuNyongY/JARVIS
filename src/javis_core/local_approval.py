from __future__ import annotations

import json
from enum import StrEnum
from typing import Protocol

from .approval_broker import ApprovalChannel, ApprovalRequest, ApprovalResponse
from .models import FrozenModel


class ApprovalView(FrozenModel):
    title: str
    capability: str
    purpose: str
    target: str
    risk: str
    arguments_json: str
    strong_auth_notice: str
    request_reference: str


class ApprovalDialogBackend(Protocol):
    def show(self, view: ApprovalView, timeout_seconds: int) -> bool: ...


class LocalApprovalPresenter:
    """Turn a local modal dialog result into a request-bound approval response."""

    def __init__(self, backend: ApprovalDialogBackend, *, timeout_seconds: int = 60) -> None:
        if timeout_seconds <= 0:
            raise ValueError("approval timeout must be positive")
        self._backend = backend
        self._timeout_seconds = timeout_seconds

    def request(self, approval: ApprovalRequest) -> ApprovalResponse:
        view = self._build_view(approval)
        try:
            approved = self._backend.show(view, self._timeout_seconds)
        except Exception:
            approved = False
        return ApprovalResponse(
            request_id=approval.request_id,
            approved=approved,
            channel=ApprovalChannel.LOCAL_UI,
        )

    @staticmethod
    def _build_view(approval: ApprovalRequest) -> ApprovalView:
        return ApprovalView(
            title="JAVIS 작업 승인",
            capability=approval.capability_id,
            purpose=approval.purpose,
            target=approval.target_identity or "(지정되지 않음)",
            risk=approval.risk.name,
            arguments_json=json.dumps(
                approval.arguments,
                sort_keys=True,
                indent=2,
                ensure_ascii=False,
            ),
            strong_auth_notice=(
                "승인 후 Windows 보안 인증이 추가로 필요합니다."
                if approval.strong_auth_required
                else "추가 Windows 보안 인증은 필요하지 않습니다."
            ),
            request_reference=str(approval.request_id),
        )


class TkApprovalDialog:
    """A stdlib-only local dialog. It never collects a password or PIN."""

    def show(self, view: ApprovalView, timeout_seconds: int) -> bool:
        import tkinter as tk
        from tkinter import scrolledtext

        approved = False
        root = tk.Tk()
        root.title(view.title)
        root.resizable(False, False)
        root.attributes("-topmost", True)

        def finish(value: bool) -> None:
            nonlocal approved
            approved = value
            root.destroy()

        root.protocol("WM_DELETE_WINDOW", lambda: finish(False))
        root.after(timeout_seconds * 1000, lambda: finish(False))
        frame = tk.Frame(root, padx=18, pady=18)
        frame.pack()
        fields = (
            ("기능", view.capability),
            ("목적", view.purpose),
            ("대상", view.target),
            ("위험도", view.risk),
            ("요청 ID", view.request_reference),
        )
        for row, (label, value) in enumerate(fields):
            tk.Label(frame, text=label, anchor="w", width=10).grid(row=row, column=0, sticky="nw")
            tk.Label(frame, text=value, anchor="w", justify="left", wraplength=520).grid(
                row=row,
                column=1,
                sticky="w",
            )
        arguments = scrolledtext.ScrolledText(frame, width=68, height=8, wrap="word")
        arguments.grid(row=len(fields), column=0, columnspan=2, pady=(12, 8))
        arguments.insert("1.0", view.arguments_json)
        arguments.configure(state="disabled")
        tk.Label(frame, text=view.strong_auth_notice, fg="#9a3412").grid(
            row=len(fields) + 1,
            column=0,
            columnspan=2,
            sticky="w",
        )
        buttons = tk.Frame(frame)
        buttons.grid(row=len(fields) + 2, column=0, columnspan=2, pady=(16, 0))
        tk.Button(buttons, text="거부", width=14, command=lambda: finish(False)).pack(
            side="left",
            padx=8,
        )
        tk.Button(buttons, text="승인", width=14, command=lambda: finish(True)).pack(
            side="left",
            padx=8,
        )
        root.lift()
        root.focus_force()
        root.grab_set()
        root.mainloop()
        return approved


class ConsentResult(StrEnum):
    VERIFIED = "VERIFIED"
    CANCELED = "CANCELED"
    UNAVAILABLE = "UNAVAILABLE"
    FAILED = "FAILED"


class OsConsentVerifier(Protocol):
    """Adapter boundary for an OS-owned authentication prompt."""

    def request_verification(self, message: str) -> ConsentResult: ...


class OsBackedStrongAuthenticator:
    """Fail-closed R4 authenticator that never receives user credentials."""

    def __init__(self, verifier: OsConsentVerifier) -> None:
        self._verifier = verifier

    def verify(self, approval: ApprovalRequest, response: ApprovalResponse) -> bool:
        if (
            response.request_id != approval.request_id
            or not response.approved
            or response.channel is not ApprovalChannel.LOCAL_UI
        ):
            return False
        message = (
            f"JAVIS {approval.capability_id} 승인 | "
            f"대상: {approval.target_identity or '(없음)'} | "
            f"요청: {approval.request_id} | 인자 해시: {approval.arguments_hash}"
        )
        try:
            return self._verifier.request_verification(message) is ConsentResult.VERIFIED
        except Exception:
            return False
