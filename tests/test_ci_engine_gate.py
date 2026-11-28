"""CI must fail if native contracts would silently run without the real engine."""
import tomllib
from types import SimpleNamespace
from pathlib import Path

import pytest

from core import search_engine
from tools.check_dev_environment import check_native_engine
from tools.engine_source_hash import engine_source_hash


@pytest.mark.parametrize("failure", ["disabled", "absent", "wrong-api", "stale-source", "external-path"])
def test_ci_rejects_unusable_engine(monkeypatch, failure):
    engine = SimpleNamespace(ENGINE_VERSION="1.0.0", API_VERSION=8, SOURCE_HASH=engine_source_hash(),
                             __file__=str(Path(__file__).resolve().parents[1] / 'src/rust_engine/sf_engine.pyd'))
    if failure == "stale-source":
        # 엔진 버전은 앱과 따로 매기므로(7.7.6), 지금 소스로 빌드되지 않은 엔진은 소스 지문으로 거릅니다.
        engine.SOURCE_HASH = "0" * 16
    elif failure == "wrong-api":
        engine.API_VERSION = 0
    elif failure == "external-path":
        engine.__file__ = "C:/another-project/sf_engine.pyd"
    monkeypatch.setattr(search_engine, "HAS_RUST_ENGINE", failure != "disabled")
    monkeypatch.setattr(search_engine, "sf_engine", None if failure == "absent" else engine)
    assert not check_native_engine()


def test_ci_accepts_built_project_engine():
    assert check_native_engine()


def test_engine_source_hash_matches_built_engine_and_ignores_version_only(tmp_path):
    """엔진의 소스 지문이 Python 계산과 같고, 엔진 버전 숫자만 바꾸면 지문이 그대로입니다(7.7.6)."""
    import shutil

    root = Path(__file__).resolve().parents[1] / "src" / "rust_engine"
    assert search_engine.sf_engine.SOURCE_HASH == engine_source_hash(root)
    copy = tmp_path / "rust_engine"
    for sub in ("src", "data"):
        shutil.copytree(root / sub, copy / sub)
    for name in ("Cargo.toml", "Cargo.lock"):
        shutil.copy2(root / name, copy / name)
    base = engine_source_hash(copy)
    assert base == engine_source_hash(root)
    toml = (copy / "Cargo.toml").read_bytes().replace(b"\r\n", b"\n")
    # 지금 엔진 버전을 읽어 씁니다(엔진 버전을 올려도 이 시험은 그대로).
    current = tomllib.loads(toml.decode("utf-8"))["package"]["version"].encode()
    (copy / "Cargo.toml").write_bytes(toml.replace(b'version = "' + current + b'"', b'version = "9.9.9"', 1))
    lock = (copy / "Cargo.lock").read_bytes().replace(b"\r\n", b"\n")
    lock_entry = b'name = "sf_engine"\nversion = "' + current + b'"'
    assert lock_entry in lock
    (copy / "Cargo.lock").write_bytes(lock.replace(lock_entry, b'name = "sf_engine"\nversion = "9.9.9"'))
    assert engine_source_hash(copy) == base
    # 줄바꿈만 CRLF로 바뀌어도 같습니다(Windows 체크아웃과 CI가 같은 값).
    lib = copy / "src" / "lib.rs"
    lib.write_bytes(lib.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    assert engine_source_hash(copy) == base
    # 소스가 한 줄이라도 바뀌면 지문이 바뀝니다.
    lib.write_bytes(lib.read_bytes() + b"// changed\r\n")
    assert engine_source_hash(copy) != base
