"""Regression coverage for the verified 5.9.27 audit findings."""

import os
from pathlib import Path
import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPlainTextEdit

from core import search_engine as engine
import folder_search_helper
from sf_utils.config_manager import ConfigManager
from sf_utils.constants import Constants as C
from ui.models import MatchDetailModel
from ui.result_view import ResultView


def test_failed_config_replace_preserves_original(tmp_path, monkeypatch):
    manager = object.__new__(ConfigManager)
    manager._save_lock = threading.Lock()
    manager._config_lock = threading.Lock()
    manager._save_timer = None
    manager._config = {"new": "settings"}
    manager.config_path = str(tmp_path / "config.json")
    original = b'{"old":"settings"}'
    Path(manager.config_path).write_bytes(original)

    def denied(*args):
        raise PermissionError("injected replacement failure")

    monkeypatch.setattr("sf_utils.config_manager.os.replace", denied)
    monkeypatch.setattr("sf_utils.config_manager.time.sleep", lambda _: None)
    assert manager.save_immediately() is False
    assert Path(manager.config_path).read_bytes() == original
    assert not Path(manager.config_path + C.TEMP_FILE_SUFFIX).exists()
    assert not Path(manager.config_path + C.BACKUP_FILE_SUFFIX).exists()


def test_stranded_config_backup_is_validated_and_loaded(mock_config_manager):
    manager = mock_config_manager
    path = Path(manager.config_path)
    manager.set(C.CONFIG_KEY_THEME, "Light")
    assert manager.save_immediately()
    path.rename(str(path) + C.BACKUP_FILE_SUFFIX)
    assert manager._load()[C.CONFIG_KEY_THEME] == "Light"
    Path(str(path) + C.BACKUP_FILE_SUFFIX).write_text('[]', encoding="utf-8")
    assert manager._load()[C.CONFIG_KEY_THEME] == manager._defaults[C.CONFIG_KEY_THEME]


@pytest.mark.parametrize("mode,path,matches,column,expected", [
    (C.MODE_JSON, "data.json", [(1, "v", "None")], 2, "None"),
    (C.MODE_XML, "data.xml", [(1, "root", "None")], 2, "None"),
    (C.MODE_NORMAL, "data.txt", [(1, "None")], 0, "None"),
    (C.MODE_NORMAL, "data.xlsx", [(1, "NoneSheet", "A1", "None", None, None)], 0, "None"),
    (C.MODE_EXCEL, "data.xlsx", [(1, "NoneSheet", "A1", "None", None, None)], 2, "None"),
])
def test_literal_none_is_not_hidden(mode, path, matches, column, expected):
    model = MatchDetailModel()
    model.set_matches(path, matches, "None", mode)
    for role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
        assert expected in model.data(model.index(0, column), role)


@pytest.mark.parametrize("mode,path,matches", [
    (C.MODE_JSON, "data.json", [(1, "v", None)]),
    (C.MODE_XML, "data.xml", [(1, "v", None, None, None)]),
    (C.MODE_EXCEL, "data.xlsx", [(1, "sheet", "A1", None, None, None)]),
])
def test_actual_none_remains_empty(mode, path, matches):
    model = MatchDetailModel()
    model.set_matches(path, matches, "", mode)
    for role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
        assert model.data(model.index(0, 2), role) == ""


@pytest.mark.parametrize("path,matches", [
    ("data.xlsx", [(1, "NoneSheet", "A1", None, None, None)]),
    ("data.txt", [(1, None)]),
])
def test_normal_view_does_not_stringify_actual_none(path, matches):
    model = MatchDetailModel()
    model.set_matches(path, matches, "", C.MODE_NORMAL)
    assert model._all_data_buffer[0].extra_1 == "" if path.endswith("xlsx") else model._all_data_buffer[0].content == ""


@pytest.mark.parametrize("prefix", ["ß" * 4500, "İ" * 4500, "가" * 4500, "a" * 4500])
def test_long_preview_uses_original_character_indices(qapp, prefix):
    preview = QPlainTextEdit()
    line = prefix + "needle" + "x" * 600
    view = SimpleNamespace(
        _context_workers=[], _context_request_id=1, _context_target_line=1,
        _context_match_content="", search_text="needle", context_preview=preview,
        context_highlighter=Mock(), match_model=SimpleNamespace(current_file_path="data.txt"),
        CONTEXT_MAX_LINE_CHARS=ResultView.CONTEXT_MAX_LINE_CHARS,
        CONTEXT_TRUNCATED_BEFORE_CHARS=ResultView.CONTEXT_TRUNCATED_BEFORE_CHARS,
        CONTEXT_TRUNCATED_AFTER_CHARS=ResultView.CONTEXT_TRUNCATED_AFTER_CHARS,
        _is_dark_theme=lambda: True,
    )
    ResultView._on_context_preview_finished(view, 1, [(1, line)], None, False)
    text = preview.toPlainText()
    assert "needle" in text
    assert prefix[-20:] + "needle" in text
    preview.close()


@pytest.mark.parametrize("query,content,found", [("a\ufeffb", "ab\n", False),
                                                  ("a\ufeffb", "a\ufeffb\n", True)])
def test_query_bom_is_preserved(tmp_path, query, content, found):
    path = tmp_path / "bom.txt"
    path.write_text(content, encoding="utf-8")
    direct = folder_search_helper.search_in_file(str(path), query)
    assert bool(direct) == found
    if engine.HAS_RUST_ENGINE:
        batch = engine.search_directory_fast([str(tmp_path)], query, ["txt"], exclude_hidden=False)
        assert bool(batch["results"]) == found


def test_packaging_path_boundary_and_root_deletion(tmp_path):
    from build_support import checked_cleanup_path, is_path_within
    project = tmp_path / "StringFinder"
    project.mkdir()
    assert is_path_within(project / "src", project)
    assert not is_path_within(tmp_path / "StringFinder_other" / "src", project)
    for unsafe in (project, tmp_path / "StringFinder_other"):
        with pytest.raises(ValueError):
            checked_cleanup_path(unsafe, project)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_packaging_prunes_junctions_and_preserves_target(tmp_path):
    from build_support import checked_cleanup_path, walk_project_tree
    project = tmp_path / "project"
    project.mkdir()
    external = tmp_path / "unrelated_project"
    cache = external / "__pycache__"
    cache.mkdir(parents=True)
    marker = cache / "keep.pyc"
    marker.write_bytes(b"preserve")
    link = project / "linked_folder"
    creation = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(external)],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if creation.returncode:
        pytest.skip("Cannot create Windows junction")
    try:
        assert not any("linked_folder" in folder for folder, _, _ in walk_project_tree(project, project))
        with pytest.raises(ValueError):
            checked_cleanup_path(link, project)
        with pytest.raises(ValueError):
            checked_cleanup_path(link / "__pycache__", project)
        assert marker.read_bytes() == b"preserve"
    finally:
        os.rmdir(link)  # Remove the junction only, never its target.


@pytest.mark.parametrize("tamper", [None, "source", "engine", "duplicate", "unknown_module"])
def test_packaged_payload_rejects_nonproject_code_and_duplicate_engines(tmp_path, monkeypatch, tamper):
    import marshal
    from build_support import verify_project_payload

    source = tmp_path / "src/core/example.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    entry = tmp_path / "src/sf_main.py"
    entry.write_text("MAIN = 1\n", encoding="utf-8")
    engine_path = tmp_path / "src/rust_engine/sf_engine.pyd"
    engine_path.parent.mkdir()
    engine_path.write_bytes(b"canonical-engine")
    module_name = "core.unknown" if tamper == "unknown_module" else "core.example"
    code = compile("VALUE = 2\n" if tamper == "source" else "VALUE = 1\n", "example.py", "exec")
    pyz = SimpleNamespace(toc={module_name: (0, 0, 0)}, extract=lambda _: code)
    contents = {
        "sf_main": marshal.dumps(compile("MAIN = 1\n", "sf_main.py", "exec")),
        "rust_engine\\sf_engine.pyd": b"wrong-engine" if tamper == "engine" else b"canonical-engine",
    }
    toc = {"PYZ.pyz": (0, 0, 0, 0, "z"), **{name: (0, 0, 0, 0, "b") for name in contents}}
    if tamper == "duplicate":
        toc["sf_engine.pyd"] = (0, 0, 0, 0, "b")
    archive = SimpleNamespace(toc=toc, open_embedded_archive=lambda _: pyz, extract=contents.__getitem__)
    monkeypatch.setattr("PyInstaller.archive.readers.CArchiveReader", lambda _: archive)
    if tamper:
        with pytest.raises(RuntimeError):
            verify_project_payload("unused.exe", tmp_path)
    else:
        verify_project_payload("unused.exe", tmp_path)
