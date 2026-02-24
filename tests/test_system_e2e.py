"""
[test_system_e2e.py]

이 테스트는 사용자의 실제 시나리오를 바탕으로 한 전체 시스템 엔드투엔드(End-to-End) 워크플로우를 검증합니다.

- 테스트 목적:
  1. UI 조작부터 엔진 실행, 결과 표시까지 이어지는 전체 흐름의 상호작용 무결성 보장.

- 주요 검증 사항:
  1. 폴더 추가 -> 확장자 설정 -> 검색어 입력 -> 검색 결과 확인 및 미리보기로 이어지는 기본 작업 흐름.
  2. 한국어 검색어에 대한 UI 표시 및 매칭 결과 대조.
  3. 다중 폴더 대상 검색 및 확장자 필터의 동적 적용 정확도.
"""

import os
import sys

import pytest
from PySide6.QtCore import Qt

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from sf_utils.app_strings import AppStrings
from ui.search_tab import SearchTab


@pytest.fixture
def search_tab(qtbot, mock_config_manager):
    widget = SearchTab(mock_config_manager)
    qtbot.addWidget(widget)
    widget.resize(900, 700)
    return widget


def _normalize_ext(value):
    return value.strip().lower().lstrip(".")


def _set_checked_extensions(tab, enabled_exts):
    panel = tab.ext_panel
    targets = {_normalize_ext(ext) for ext in enabled_exts}

    panel.special_combo.setCurrentText(AppStrings.SPECIAL_SEARCH_OFF)
    panel.toggle_all(False)

    for ext in targets:
        panel.add_extension(ext)

    for i in range(panel.ext_list.count()):
        item = panel.ext_list.item(i)
        widget = panel.ext_list.itemWidget(item)
        if widget is None:
            continue
        widget.checkbox.setChecked(_normalize_ext(widget.text()) in targets)


def _run_search_and_wait(qtbot, tab, timeout=10000):
    with qtbot.waitSignal(
        tab.search_status_changed,
        timeout=timeout,
        check_params_cb=lambda is_searching: is_searching is False,
    ):
        qtbot.mouseClick(tab.search_panel.search_btn, Qt.MouseButton.LeftButton)

    qtbot.wait(150)


def _ensure_first_result_selected(tab, qtbot):
    rv = tab.result_view_panel
    if rv.match_model.rowCount() == 0 and rv.result_model.rowCount() > 0:
        rv.auto_select_first_result()
        qtbot.wait(80)


class TestSystemE2E:
    def test_e2e_basic_workflow(self, search_tab, tmp_path, qtbot):
        target_dir = tmp_path / "basic_data"
        target_dir.mkdir()
        (target_dir / "target.txt").write_text("Hello StringFinder", encoding="utf-8")

        search_tab.folder_panel.add_folder(str(target_dir))
        _set_checked_extensions(search_tab, ["txt"])
        search_tab.search_panel.search_combo.setEditText("StringFinder")

        _run_search_and_wait(qtbot, search_tab)
        _ensure_first_result_selected(search_tab, qtbot)

        rv = search_tab.result_view_panel
        assert rv.result_model.rowCount() == 1

        file_path, _matches = rv.result_model.get_full_data(0)
        assert "target.txt" in file_path

        assert rv.match_model.rowCount() == 1
        # [v4.48.0] Normal 모드는 단일 컬럼(0)에 모든 정보가 표시됨
        idx_combined = rv.match_model.index(0, 0)
        display_data = rv.match_model.data(idx_combined, Qt.ItemDataRole.EditRole)
        assert "Hello StringFinder" in display_data

    def test_e2e_extension_filter(self, search_tab, tmp_path, qtbot):
        target_dir = tmp_path / "filter_data"
        target_dir.mkdir()

        (target_dir / "script.py").write_text("common_keyword", encoding="utf-8")
        (target_dir / "notes.txt").write_text("common_keyword", encoding="utf-8")

        search_tab.folder_panel.add_folder(str(target_dir))
        _set_checked_extensions(search_tab, ["py"])
        search_tab.search_panel.search_combo.setEditText("common_keyword")

        _run_search_and_wait(qtbot, search_tab)
        _ensure_first_result_selected(search_tab, qtbot)

        rv = search_tab.result_view_panel
        assert rv.result_model.rowCount() == 1

        file_path, _matches = rv.result_model.get_full_data(0)
        assert "script.py" in file_path
        assert "notes.txt" not in file_path

    def test_e2e_multi_folder(self, search_tab, tmp_path, qtbot):
        dir_a = tmp_path / "DirA"
        dir_b = tmp_path / "DirB"
        dir_a.mkdir()
        dir_b.mkdir()

        (dir_a / "file_a.txt").write_text("find_me", encoding="utf-8")
        (dir_b / "file_b.txt").write_text("find_me", encoding="utf-8")

        search_tab.folder_panel.add_folder(str(dir_a))
        search_tab.folder_panel.add_folder(str(dir_b))
        _set_checked_extensions(search_tab, ["txt"])
        search_tab.search_panel.search_combo.setEditText("find_me")

        _run_search_and_wait(qtbot, search_tab)
        _ensure_first_result_selected(search_tab, qtbot)

        rv = search_tab.result_view_panel
        assert rv.result_model.rowCount() == 2

    def test_e2e_json_mode(self, search_tab, tmp_path, qtbot):
        target_dir = tmp_path / "json_data"
        target_dir.mkdir()
        (target_dir / "data.json").write_text('{"user": "admin", "id": 123}', encoding="utf-8")

        search_tab.folder_panel.add_folder(str(target_dir))
        search_tab.ext_panel.special_combo.setCurrentText("JSON (부분 일치)")
        search_tab.search_panel.search_combo.setEditText("admin")

        _run_search_and_wait(qtbot, search_tab)
        _ensure_first_result_selected(search_tab, qtbot)

        rv = search_tab.result_view_panel
        assert rv.result_model.rowCount() == 1
        assert rv.match_model.rowCount() >= 1

        row_values = []
        for col in range(rv.match_model.columnCount()):
            idx = rv.match_model.index(0, col)
            row_values.append(str(rv.match_model.data(idx, Qt.ItemDataRole.EditRole)))
        assert any("admin" in value for value in row_values)

    def test_e2e_korean_search(self, search_tab, tmp_path, qtbot):
        target_dir = tmp_path / "korean_data"
        target_dir.mkdir()
        (target_dir / "korean.txt").write_text("안녕하세요 반갑습니다", encoding="utf-8")

        search_tab.folder_panel.add_folder(str(target_dir))
        _set_checked_extensions(search_tab, ["txt"])
        search_tab.search_panel.search_combo.setEditText("반갑")

        _run_search_and_wait(qtbot, search_tab)
        _ensure_first_result_selected(search_tab, qtbot)

        rv = search_tab.result_view_panel
        assert rv.result_model.rowCount() == 1

        file_path, _matches = rv.result_model.get_full_data(0)
        assert "korean.txt" in file_path

        # [v4.48.0] Normal 모드는 단일 컬럼(0) 사용
        idx_combined = rv.match_model.index(0, 0)
        display_data = rv.match_model.data(idx_combined, Qt.ItemDataRole.EditRole)
        assert "반갑" in display_data
