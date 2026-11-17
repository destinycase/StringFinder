"""엔진 빌드가 빌드한 PC의 경로(계정 이름 포함)를 바이너리에 남기지 않는지 확인합니다(7.3.3)."""

import os
from pathlib import Path

import pytest

import build_rust

ENGINE = Path(build_rust.SSOT_ENGINE_PYD)


def _flags(env):
    return env["CARGO_ENCODED_RUSTFLAGS"].split("\x1f")


def test_build_env_remaps_cargo_rustup_and_project_paths(tmp_path):
    cargo = tmp_path / "my cargo"
    rustup = tmp_path / "rustup"
    env = build_rust.rust_build_env({"CARGO_HOME": str(cargo), "RUSTUP_HOME": str(rustup)})

    flags = _flags(env)
    assert f"--remap-path-prefix={cargo}=/cargo" in flags
    assert f"--remap-path-prefix={rustup}=/rustup" in flags
    assert f"--remap-path-prefix={build_rust.PROJECT_ROOT}=/stringfinder" in flags
    assert env["PYO3_USE_ABI3_FORWARD_COMPATIBILITY"] == "1"


def test_build_env_defaults_to_home_folders_and_keeps_existing_flags():
    env = build_rust.rust_build_env({"RUSTFLAGS": "-C target-cpu=x86-64"})

    flags = _flags(env)
    assert flags[:2] == ["-C", "target-cpu=x86-64"]
    home = os.path.expanduser("~")
    assert f"--remap-path-prefix={os.path.join(home, '.cargo')}=/cargo" in flags
    assert "RUSTFLAGS" not in env


def test_build_env_extends_encoded_flags():
    env = build_rust.rust_build_env({"CARGO_ENCODED_RUSTFLAGS": "-Cdebuginfo=0\x1f-Copt-level=3"})

    assert _flags(env)[:2] == ["-Cdebuginfo=0", "-Copt-level=3"]


@pytest.mark.skipif(not ENGINE.exists(), reason="built engine is required")
def test_committed_engine_has_no_local_build_paths():
    data = ENGINE.read_bytes().lower()
    for local in (os.path.expanduser("~"), build_rust.PROJECT_ROOT):
        for form in (local, local.replace("\\", "/")):
            assert form.lower().encode("utf-8") not in data, form
    assert b"\\.cargo\\registry" not in data
