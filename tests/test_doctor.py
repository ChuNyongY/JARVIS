from pathlib import Path

import pytest

from javis_core.doctor import DoctorCheck, DoctorStatus, default_doctor_checks, run_doctor


def test_doctor_separates_required_failures_from_optional_warnings(tmp_path: Path) -> None:
    checks = (
        DoctorCheck("required.ok", True, "REQUIRED_FAILED", lambda _root: True),
        DoctorCheck("optional.missing", False, "OPTIONAL_MISSING", lambda _root: False),
        DoctorCheck(
            "required.exception",
            True,
            "REQUIRED_EXCEPTION",
            lambda _root: (_ for _ in ()).throw(RuntimeError("private path")),
        ),
    )

    report = run_doctor(tmp_path.resolve(), checks=checks)

    assert not report.passed
    assert [item.status for item in report.items] == [
        DoctorStatus.PASS,
        DoctorStatus.WARN,
        DoctorStatus.FAIL,
    ]
    raw = report.model_dump_json()
    assert "private path" not in raw
    assert str(tmp_path) not in raw


def test_doctor_invalid_root_returns_stable_failure() -> None:
    report = run_doctor(Path("relative"))
    assert not report.passed
    assert report.items[0].code == "PROJECT_ROOT_INVALID"


def test_doctor_rejects_duplicate_or_unbounded_check_sets(tmp_path: Path) -> None:
    check = DoctorCheck("duplicate", True, "FAILED", lambda _root: True)
    with pytest.raises(ValueError, match="unique"):
        run_doctor(tmp_path.resolve(), checks=(check, check))
    with pytest.raises(ValueError, match="1..32"):
        run_doctor(tmp_path.resolve(), checks=())
    with pytest.raises(ValueError, match="1..32"):
        run_doctor(
            tmp_path.resolve(),
            checks=tuple(
                DoctorCheck(f"check.{index}", True, "FAILED", lambda _root: True)
                for index in range(33)
            ),
        )


def test_default_doctor_requires_bounded_sapi_probe() -> None:
    checks = {check.component: check for check in default_doctor_checks()}

    assert checks["tts.sapi"].required
    assert checks["tts.sapi"].failure_code == "SAPI_UNAVAILABLE"
    assert not checks["dependency.pycaw"].required
