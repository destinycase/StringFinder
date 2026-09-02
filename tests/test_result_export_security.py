import openpyxl

from core.search_engine import search_in_file
from sf_utils.constants import Constants
from ui.result_view import ResultView, _sanitize_excel_cell


class _FakeResultModel:
    def __init__(self, search_result):
        path, count, matches = search_result
        self._results = [(count, "file", "folder", path, matches)]

    def get_all_results(self):
        return self._results


def test_excel_cell_sanitizer_blocks_formula_prefixes():
    for prefix in ("=", "+", "-", "@", "\t", "\r", "\n"):
        value = prefix + "payload"
        assert _sanitize_excel_cell(value) == "'" + value

    assert _sanitize_excel_cell("safe") == "safe"
    assert _sanitize_excel_cell(42) == 42


def test_excel_export_writes_external_formula_text_as_literals(tmp_path):
    formula = '=HYPERLINK("https://example.invalid","click")'
    source = tmp_path / "formula.txt"
    source.write_text(formula, encoding="utf-8")
    output = tmp_path / "results.xlsx"
    search_result = search_in_file(str(source), formula)
    assert search_result is not None
    assert search_result[0] == str(source)

    fake_view = type(
        "FakeResultView",
        (),
        {
            "search_mode": Constants.MODE_NORMAL,
            "result_model": _FakeResultModel(search_result),
        },
    )()

    ResultView._export_to_excel(fake_view, str(output))

    workbook = openpyxl.load_workbook(output, data_only=False)
    file_sheet = workbook.worksheets[0]
    detail_sheet = workbook.worksheets[1]
    for cell in (file_sheet["B2"], detail_sheet["A2"], detail_sheet["C2"]):
        assert cell.data_type != "f"
    assert file_sheet["B2"].value == str(source)
    assert detail_sheet["A2"].value == str(source)
    assert detail_sheet["C2"].value == "'" + formula
