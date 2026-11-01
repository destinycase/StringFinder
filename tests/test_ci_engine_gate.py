"""CI must fail if native contracts would silently run without the real engine."""
from types import SimpleNamespace
from pathlib import Path

import pytest

from core import search_engine
from sf_utils._version import VERSION
from tools.check_dev_environment import check_native_engine


@pytest.mark.parametrize("failure", ["disabled", "absent", "wrong-api", "wrong-version", "external-path"])
def test_ci_rejects_unusable_engine(monkeypatch, failure):
    engine = SimpleNamespace(ENGINE_VERSION=VERSION, API_VERSION=8,
                             __file__=str(Path(__file__).resolve().parents[1] / 'src/rust_engine/sf_engine.pyd'))
    if failure == "wrong-version":
        engine.ENGINE_VERSION = "0.0.0"
    elif failure == "wrong-api":
        engine.API_VERSION = 0
    elif failure == "external-path":
        engine.__file__ = "C:/another-project/sf_engine.pyd"
    monkeypatch.setattr(search_engine, "HAS_RUST_ENGINE", failure != "disabled")
    monkeypatch.setattr(search_engine, "sf_engine", None if failure == "absent" else engine)
    assert not check_native_engine()


def test_ci_accepts_built_project_engine():
    assert check_native_engine()
