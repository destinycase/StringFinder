"""빌드·검증 도구: 엔진 재사용, 벤치마크 앱 버전, 확인 도구의 데이터 폴더(7.7.8 정리)."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

import build_rust

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fake_engine(tmp_path, monkeypatch):
    """임시 엔진 파일과 가짜 cargo. cargo가 불리면 calls에 남고 dll을 만들어 줍니다."""
    pyd = tmp_path / "sf_engine.pyd"
    pyd.write_bytes(b"ORIGINAL-ENGINE-BYTES")
    monkeypatch.setattr(build_rust, "SSOT_ENGINE_PYD", str(pyd))
    monkeypatch.setattr(build_rust, "current_engine_identity", lambda: ("abc123", "1.0.0"))
    monkeypatch.setattr(build_rust, "clean_old_binaries", lambda: None)
    monkeypatch.setattr(build_rust, "clean_non_ssot_engine_binaries", lambda: None)
    calls = []
    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if cmd and cmd[0] == "cargo":
            calls.append(cmd)
            raise RuntimeError("cargo called")  # 실제 빌드는 하지 않습니다.
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(build_rust.subprocess, "run", fake_run)
    return pyd, calls


def _build(**kwargs):
    with pytest.raises(SystemExit):
        build_rust.build_rust_engine(**kwargs)


def test_same_source_hash_skips_cargo_and_keeps_engine_bytes(fake_engine, monkeypatch):
    pyd, calls = fake_engine
    monkeypatch.setattr(build_rust, "installed_engine_identity", lambda path=None: ("abc123", "1.0.0"))
    assert build_rust.build_rust_engine() == str(pyd)
    assert calls == []
    assert pyd.read_bytes() == b"ORIGINAL-ENGINE-BYTES"


def test_different_source_hash_calls_cargo(fake_engine, monkeypatch):
    _, calls = fake_engine
    monkeypatch.setattr(build_rust, "installed_engine_identity", lambda path=None: ("stale0000", "1.0.0"))
    _build()
    assert calls and calls[0][:2] == ["cargo", "build"]


def test_missing_engine_calls_cargo(fake_engine, monkeypatch):
    pyd, calls = fake_engine
    pyd.unlink()
    _build()
    assert calls


def test_bumped_engine_version_with_same_source_hash_calls_cargo(fake_engine, monkeypatch):
    """소스 지문은 엔진 버전 줄을 빼고 계산하므로, 버전만 올려도 다시 빌드해야 합니다."""
    _, calls = fake_engine
    monkeypatch.setattr(build_rust, "installed_engine_identity", lambda path=None: ("abc123", "0.9.0"))
    _build()
    assert calls and calls[0][:2] == ["cargo", "build"]


def test_force_rebuilds_even_when_source_hash_matches(fake_engine, monkeypatch):
    _, calls = fake_engine
    monkeypatch.setattr(build_rust, "installed_engine_identity", lambda path=None: ("abc123", "1.0.0"))
    _build(force=True)
    assert calls


def test_installed_engine_identity_reads_real_engine_without_loading_it():
    """실제 엔진의 지문·버전을 다른 프로세스에서 읽고, 지금 소스와 같습니다(이 프로세스에 엔진을 불러오지 않음)."""
    assert build_rust.installed_engine_identity() == build_rust.current_engine_identity()


def test_benchmark_result_has_app_version_and_handles_old_baseline():
    sys.path.insert(0, str(ROOT))
    from tools import benchmark

    meta = benchmark.result_metadata(type("E", (), {"ENGINE_VERSION": "1.0.0"})())
    from sf_utils._version import VERSION

    assert meta["app_version"] == VERSION and meta["engine_version"] == "1.0.0"
    line = benchmark.version_line(meta, {"engine_version": "7.7.1", "samples": {}})
    assert VERSION in line and "1.0.0" in line and "7.7.1" in line and "기록 없음" in line
    result = {**meta, "samples": {"A": {"median_ms": 10.0, "hits": 1, "digest": "d"}}}
    old = {"engine_version": "7.7.1", "samples": {"A": {"median_ms": 10.0, "hits": 1, "digest": "d"}}}
    assert benchmark.compare(result, old, 1.15)[0].startswith("A")


def test_dev_environment_check_leaves_no_files_in_appdata(tmp_path):
    """확인 도구를 돌려도 원래 데이터 폴더에 StringFinder 폴더·로그가 생기지 않습니다."""
    appdata = tmp_path / "appdata"
    appdata.mkdir()
    env = dict(os.environ, APPDATA=str(appdata), LOCALAPPDATA=str(appdata))
    env.pop("PYTEST_CURRENT_TEST", None)
    result = subprocess.run([sys.executable, str(ROOT / "tools" / "check_dev_environment.py"), "--require-engine"],
                            env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert "native engine" in result.stdout
    assert list(appdata.iterdir()) == []
