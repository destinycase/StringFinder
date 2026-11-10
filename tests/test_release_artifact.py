"""버전별 배포 파일(release/StringFinder-vX.Y.Z.exe) 관리 테스트."""

import hashlib
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import build_support  # noqa: E402


def test_release_path_contains_version(tmp_path) -> None:
    path = build_support.release_artifact_path(tmp_path, "6.4.1")
    assert path == tmp_path / "release" / "StringFinder-v6.4.1.exe"


@pytest.mark.parametrize("version", ["", "unknown", "6.4/1", "6.4:1"])
def test_release_path_rejects_invalid_version(tmp_path, version) -> None:
    with pytest.raises(ValueError):
        build_support.release_artifact_path(tmp_path, version)


def test_publish_copies_and_reports_size_and_hash(tmp_path) -> None:
    source = tmp_path / "build" / "StringFinder.exe"
    source.parent.mkdir()
    source.write_bytes(b"exe-bytes")
    destination = build_support.release_artifact_path(tmp_path, "1.2.3")

    size, sha256 = build_support.publish_release_artifact(source, destination)

    assert destination.read_bytes() == b"exe-bytes"
    assert size == len(b"exe-bytes")
    assert sha256 == hashlib.sha256(b"exe-bytes").hexdigest()
    assert not destination.with_name(destination.name + ".partial").exists()


def test_existing_release_is_never_overwritten(tmp_path) -> None:
    source = tmp_path / "new.exe"
    source.write_bytes(b"new")
    destination = build_support.release_artifact_path(tmp_path, "1.2.3")
    destination.parent.mkdir()
    destination.write_bytes(b"old")

    with pytest.raises(FileExistsError):
        build_support.ensure_release_absent(destination)
    with pytest.raises(FileExistsError):
        build_support.publish_release_artifact(source, destination)
    assert destination.read_bytes() == b"old"


def test_release_folder_is_not_tracked_by_git() -> None:
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/release/" in gitignore


def test_stable_dist_executable_stays_tracked_and_untouched_by_build() -> None:
    # dist/StringFinder.exe는 사용자 바로가기가 가리키는 마지막 안정 버전입니다.
    gitignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert not any(line.strip() in ("/dist/", "dist/", "/dist/*", "dist/StringFinder.exe") for line in gitignore)
    build_source = (PROJECT_ROOT / "build.py").read_text(encoding="utf-8")
    assert '"dist"' not in build_source and "'dist'" not in build_source
