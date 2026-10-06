import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from javis_core import (
    Action,
    CancellationToken,
    PackageIdentityVerifier,
    PackageVerifyExecutor,
    Postcondition,
)
from javis_core.process_launch import RegisteredApp
from javis_core.runtime_models import ExecutionResult


class Lease:
    def __init__(self, accepted: bool = True) -> None:
        self.accepted = accepted

    @contextmanager
    def hold(self, package: RegisteredApp) -> Iterator[bool]:
        actual = hashlib.sha256(package.executable.read_bytes()).hexdigest()
        yield self.accepted and actual == package.sha256


class Publisher:
    def __init__(self, accepted: bool = True) -> None:
        self.accepted = accepted

    def verify(self, executable: Path, expected_subject: str) -> bool:
        return self.accepted


def create_package(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "fixture.msi"
    path.write_bytes(b"signed fixture package")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def action(path: Path, digest: str) -> Action:
    return Action(
        capability_id="package.verify",
        arguments={
            "path": str(path),
            "sha256": digest,
            "publisher": "CN=Fixture Publisher",
        },
        purpose="verify installer without execution",
        target_identity=f"package-sha256:{digest}",
        expected_postconditions=(Postcondition(predicate="package.identity.verified"),),
    )


def test_verifies_identity_and_independent_receipt_without_path_leak(tmp_path: Path) -> None:
    path, digest = create_package(tmp_path)
    executor = PackageVerifyExecutor(
        (tmp_path.resolve(),),
        Publisher(),
        lease=Lease(),
    )
    requested = action(path, digest)

    result = executor.execute(requested)
    verified = PackageIdentityVerifier(executor).verify(requested, result)

    assert result.success and verified.success
    assert str(path) not in result.model_dump_json()
    assert "CN=Fixture Publisher" not in result.model_dump_json()


def test_hash_publisher_and_forged_receipt_fail_closed(tmp_path: Path) -> None:
    path, digest = create_package(tmp_path)
    hash_failure = PackageVerifyExecutor(
        (tmp_path.resolve(),), Publisher(), lease=Lease(False)
    ).execute(action(path, digest))
    publisher_failure = PackageVerifyExecutor(
        (tmp_path.resolve(),), Publisher(False), lease=Lease()
    ).execute(action(path, digest))
    assert hash_failure.error_code == "PACKAGE_HASH_MISMATCH"
    assert publisher_failure.error_code == "PACKAGE_PUBLISHER_MISMATCH"

    executor = PackageVerifyExecutor((tmp_path.resolve(),), Publisher(), lease=Lease())
    requested = action(path, digest)
    forged = ExecutionResult(
        action_id=requested.action_id,
        executor="native",
        success=True,
        observed={
            "package_sha256": digest,
            "publisher_sha256": "0" * 64,
            "identity_verified": True,
        },
        duration_ms=0,
    )
    assert PackageIdentityVerifier(executor).verify(
        requested, forged
    ).reason == "PACKAGE_RECEIPT_MISMATCH"


def test_outside_root_wrong_suffix_invalid_target_and_stop_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside, digest = create_package(tmp_path)
    executor = PackageVerifyExecutor((root.resolve(),), Publisher(), lease=Lease())
    assert executor.execute(action(outside, digest)).error_code == "INVALID_ARGUMENTS"

    wrong = root / "fixture.txt"
    wrong.write_bytes(b"package")
    wrong_digest = hashlib.sha256(wrong.read_bytes()).hexdigest()
    assert executor.execute(action(wrong, wrong_digest)).error_code == "INVALID_ARGUMENTS"

    valid = root / "fixture.msi"
    valid.write_bytes(b"package")
    valid_digest = hashlib.sha256(valid.read_bytes()).hexdigest()
    invalid_target = action(valid, valid_digest).model_copy(update={"target_identity": "wrong"})
    assert executor.execute(invalid_target).error_code == "INVALID_ARGUMENTS"
    token = CancellationToken()
    token.cancel()
    assert executor.execute(action(valid, valid_digest), token).error_code == "STOP_REQUESTED"
