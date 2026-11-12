"""결과 내보내기(Excel)와 외부 편집기 실행 경로."""
import os
import sys

import openpyxl

from sf_utils.constants import Constants as C
from ui.result_view import ResultView

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))


class _FakeModel:
    def __init__(self, rows):
        self._rows = rows

    def get_all_results(self):
        return self._rows


def _export(tmp_path, mode, rows):
    output = tmp_path / "out.xlsx"
    fake = type("FakeView", (), {"search_mode": mode, "result_model": _FakeModel(rows)})()
    ResultView._export_to_excel(fake, str(output))
    return openpyxl.load_workbook(output).worksheets[1]


def test_export_survives_control_characters_and_skips_notice_rows(tmp_path):
    matches = [
        (1, "\x1b[31mERROR\x1b[0m needle", 0, 6),
        (-1, "파일당 상한 안내", None, None),
        (-2, "부분 검색 안내", None, None),
        (3, "Sheet1\tB2\tneedle 값", None, None),
    ]
    sheet = _export(tmp_path, C.MODE_NORMAL, [(2, "a.log", "dir", "dir/a.log", matches)])
    rows = [[cell.value for cell in row] for row in sheet.iter_rows(min_row=2)]
    assert rows == [["dir/a.log", "1", "[31mERROR[0m needle"], ["dir/a.log", "3", "Sheet1!B2 | needle 값"]]


def test_value_mode_export_writes_empty_cells_not_none(tmp_path):
    sheet = _export(tmp_path, C.MODE_JSON + " (부분 일치)", [(1, "a.json", "d", "d/a.json", [("", "k", None, None, None)])])
    row = [cell.value for cell in next(sheet.iter_rows(min_row=2))]
    # 빈 값은 빈 칸으로 저장됩니다(예전에는 "None"이라는 글자가 들어감). openpyxl은 빈 칸을 None으로 읽습니다.
    assert row == ["d/a.json", None, "k", None]


def test_batch_editor_launch_avoids_percent_and_caret_paths(tmp_path, monkeypatch):
    from sf_utils import file_helper

    target = tmp_path / "a%PATH%b^.txt"
    target.write_text("x", encoding="utf-8")
    launched, fallback = [], []
    monkeypatch.setattr(file_helper.shutil, "which", lambda _name: str(tmp_path / "code.cmd"))
    monkeypatch.setattr(file_helper.subprocess, "Popen", lambda command, **_k: launched.append(command))
    monkeypatch.setattr(file_helper, "open_file", lambda path: fallback.append(path) or True)
    file_helper.open_in_external_editor(str(target), 3, {C.CONFIG_KEY_EDITOR_TYPE: "vscode"})
    assert launched == []
    assert fallback == [str(target)]
