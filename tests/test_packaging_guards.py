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
