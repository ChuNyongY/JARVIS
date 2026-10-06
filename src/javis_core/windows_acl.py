from __future__ import annotations

import os
from pathlib import Path
from typing import Any


class WindowsAclError(PermissionError):
    pass


class WindowsUserOnlyAcl:
    """Protect a file DACL for the current token user and LocalSystem only."""

    def harden(self, path: Path) -> None:
        resolved = self._validated_file(path)
        modules = self._modules()
        win32api, win32con, win32security, ntsecuritycon = modules
        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(),
            win32con.TOKEN_QUERY,
        )
        try:
            user_sid = win32security.GetTokenInformation(
                token,
                win32security.TokenUser,
            )[0]
        finally:
            token.Close()
        system_sid = win32security.CreateWellKnownSid(
            win32security.WinLocalSystemSid,
            None,
        )
        dacl = win32security.ACL()
        for sid in (user_sid, system_sid):
            dacl.AddAccessAllowedAceEx(
                win32security.ACL_REVISION,
                0,
                ntsecuritycon.FILE_ALL_ACCESS,
                sid,
            )
        information = (
            win32security.DACL_SECURITY_INFORMATION
            | win32security.PROTECTED_DACL_SECURITY_INFORMATION
        )
        try:
            win32security.SetNamedSecurityInfo(
                str(resolved),
                win32security.SE_FILE_OBJECT,
                information,
                None,
                None,
                dacl,
                None,
            )
        except Exception as error:
            raise WindowsAclError("could not apply protected file DACL") from error
        if not self.verify(resolved):
            raise WindowsAclError("file DACL verification failed")

    def verify(self, path: Path) -> bool:
        try:
            resolved = self._validated_file(path)
            win32api, win32con, win32security, ntsecuritycon = self._modules()
            token = win32security.OpenProcessToken(
                win32api.GetCurrentProcess(),
                win32con.TOKEN_QUERY,
            )
            try:
                user_sid = win32security.GetTokenInformation(
                    token,
                    win32security.TokenUser,
                )[0]
            finally:
                token.Close()
            system_sid = win32security.CreateWellKnownSid(
                win32security.WinLocalSystemSid,
                None,
            )
            expected_sids = {
                win32security.ConvertSidToStringSid(user_sid),
                win32security.ConvertSidToStringSid(system_sid),
            }
            descriptor = win32security.GetNamedSecurityInfo(
                str(resolved),
                win32security.SE_FILE_OBJECT,
                win32security.DACL_SECURITY_INFORMATION,
            )
            control, _revision = descriptor.GetSecurityDescriptorControl()
            if not control & win32security.SE_DACL_PROTECTED:
                return False
            dacl = descriptor.GetSecurityDescriptorDacl()
            if dacl is None or dacl.GetAceCount() != len(expected_sids):
                return False
            observed_sids: set[str] = set()
            for index in range(dacl.GetAceCount()):
                header, mask, sid = dacl.GetAce(index)
                ace_type, flags = header
                if (
                    ace_type != win32security.ACCESS_ALLOWED_ACE_TYPE
                    or flags & win32security.INHERITED_ACE
                    or mask != ntsecuritycon.FILE_ALL_ACCESS
                ):
                    return False
                observed_sids.add(win32security.ConvertSidToStringSid(sid))
            return observed_sids == expected_sids
        except Exception:
            return False

    @staticmethod
    def _validated_file(path: Path) -> Path:
        if os.name != "nt":
            raise WindowsAclError("Windows ACL hardening requires Windows")
        if path.is_symlink():
            raise WindowsAclError("ACL target must not be a symlink")
        try:
            resolved = path.resolve(strict=True)
        except OSError as error:
            raise WindowsAclError("ACL target is unavailable") from error
        if not resolved.is_file():
            raise WindowsAclError("ACL target must be a file")
        return resolved

    @staticmethod
    def _modules() -> tuple[Any, Any, Any, Any]:
        try:
            import ntsecuritycon  # type: ignore[import-untyped]
            import win32api  # type: ignore[import-untyped]
            import win32con  # type: ignore[import-untyped]
            import win32security  # type: ignore[import-untyped]
        except ImportError as error:
            raise WindowsAclError("pywin32 is required for ACL hardening") from error
        return win32api, win32con, win32security, ntsecuritycon
