"""Packaging checks should reject unrelated ICU binaries before deployment."""

import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from build_support import isolated_packaging_path, verify_qt_icu


def test_packaging_path_excludes_external_tools(monkeypatch):
    monkeypatch.setenv("PATH", "C:/external/poppler;C:/other/Qt")
    monkeypatch.setenv("SystemRoot", "C:/Windows")
    result = isolated_packaging_path().split(os.pathsep)
    assert "poppler" not in ";".join(result)
    assert "other" not in ";".join(result)
    assert result[0].replace("\\", "/") == "C:/Windows/System32"


@pytest.mark.parametrize("compatible", [False, True])
def test_icu_export_guard(compatible):
    core = SimpleNamespace(DIRECTORY_ENTRY_IMPORT=[SimpleNamespace(
        dll=b"icuuc.dll", imports=[SimpleNamespace(name=b"ucnv_open")]
    )])
    dll = SimpleNamespace(DIRECTORY_ENTRY_EXPORT=SimpleNamespace(symbols=[
        SimpleNamespace(name=b"ucnv_open" if compatible else b"ucnv_open_78")
    ]))
    archive = SimpleNamespace(toc={"PySide6/Qt6Core.dll": None, "icuuc.dll": None}, extract=lambda n: n)
    with patch("PyInstaller.archive.readers.CArchiveReader", return_value=archive), patch(
        "pefile.PE", side_effect=[core, dll]
    ):
        if compatible:
            verify_qt_icu("candidate.exe")
        else:
            with pytest.raises(RuntimeError, match="Incompatible bundled"):
                verify_qt_icu("candidate.exe")


def test_smoke_test_uses_temporary_data_folder(monkeypatch, tmp_path):
    """빌드 기동 검사는 사용자 데이터 폴더(APPDATA)에 로그를 남기지 않게 임시 폴더를 쓰고 지웁니다(7.6.5)."""
    import os
    import subprocess

    import build_support

    seen = {}

    class FakeProcess:
        pid = 0

        def wait(self, timeout=None):
            return 0

    def fake_popen(args, env=None, creationflags=0):
        seen["env"] = env
        os.makedirs(os.path.join(env["APPDATA"], "StringFinder"), exist_ok=True)
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setenv("APPDATA", str(tmp_path / "real_appdata"))
    build_support.smoke_test_executable(tmp_path / "StringFinder.exe")
    data_dir = seen["env"]["APPDATA"]
    assert data_dir != str(tmp_path / "real_appdata")
    assert seen["env"]["LOCALAPPDATA"] == data_dir
    assert not os.path.exists(data_dir)
