"""Empty-cell fast path must preserve scalar values and search controls."""
import datetime
from types import SimpleNamespace

import pytest

from core import search_engine as se
from sf_utils.constants import Constants


@pytest.fixture
def fake_sheet(monkeypatch):
    rows = [[None, "", " ", 0, False, "가나다", "needle"]]
    sheet = SimpleNamespace(start=(0, 0), iter_rows=lambda: iter(rows))
    workbook = SimpleNamespace(sheet_names=['Data'], get_sheet_by_name=lambda _: sheet)
    monkeypatch.setattr('python_calamine.CalamineWorkbook',
                        SimpleNamespace(from_path=lambda _: workbook))
    monkeypatch.setattr(se, '_check_excel_signature', lambda _: (True, None))
    monkeypatch.setattr(se, '_positive_limit', lambda *args: 100)
    return rows, sheet


def test_empty_cells_skip_conversion_and_normalization(fake_sheet, monkeypatch):
    converted = []
    normalized = []
    original_convert = se._excel_cell_text
    original_normalize = se.normalize_unicode

    def convert(value):
        converted.append(value)
        return original_convert(value)

    def normalize(value):
        normalized.append(value)
        return original_normalize(value)

    monkeypatch.setattr(se, '_excel_cell_text', convert)
    monkeypatch.setattr(se, 'normalize_unicode', normalize)
    result = se.search_in_excel_special('fake.xlsx', 'absent', use_complex_search=True)
    assert result is None
    assert len(converted) == 5
    assert converted == [' ', 0, False, '가나다', 'needle']
    assert normalized == ['absent', ' ', '0', 'false', '가나다', 'needle']


def test_cell_casefold_is_reused_without_changing_content(fake_sheet, monkeypatch):
    folded = []
    rows, _ = fake_sheet
    rows[:] = [['', ' Straße ']]
    original_normalize = se.normalize_unicode

    class TrackedText(str):
        def casefold(self):
            folded.append(str(self))
            return super().casefold()

    monkeypatch.setattr(se, 'normalize_unicode',
                        lambda text: TrackedText(original_normalize(text)))
    result = se.search_in_excel_special('fake.xlsx', 'strasse', exact_match=True,
                                       use_complex_search=True)
    assert result and result[2] == [(0, 'Data', 'B1', ' Straße ')]
    assert folded == ['strasse', ' Straße ']


@pytest.mark.parametrize(('query', 'cell', 'text'), [
    ('0', 'D1', '0'), ('false', 'E1', 'false'),
    ('가나다', 'F1', '가나다'), ('needle', 'G1', 'needle'),
])
@pytest.mark.parametrize('exact', [False, True])
@pytest.mark.parametrize('existence', [False, True])
def test_nonempty_values_and_last_cell_remain_searchable(fake_sheet, query, cell, text, exact, existence):
    result = se.search_in_excel_special('fake.xlsx', query, exact_match=exact,
                                       use_complex_search=True, existence_only=existence)
    assert result and result[1] == 1
    if not existence:
        assert result[2] == [(0, 'Data', cell, text)]


@pytest.mark.parametrize('query', ['', ' ', '\t'])
@pytest.mark.parametrize('exact', [False, True])
def test_normalized_empty_query_keeps_existing_behavior(fake_sheet, query, exact):
    result = se.search_in_excel_special('fake.xlsx', query, exact_match=exact, use_complex_search=True)
    assert result and result[1] == (2 if exact else 6)
    assert result[2][0] == (0, 'Data', 'B1', '')


def test_dates_and_times_remain_searchable(fake_sheet):
    rows, _ = fake_sheet
    rows[:] = [[None, '', datetime.date(2026, 10, 3), datetime.time(12, 30)]]
    result = se.search_in_excel_special('fake.xls', '12:30', use_complex_search=True)
    assert result and result[2] == [(0, 'Data', 'D1', '12:30:00')]


def test_empty_rows_keep_cancellation_checks(fake_sheet):
    rows, sheet = fake_sheet
    rows[:] = [[''] * 10 for _ in range(101)] + [['needle']]
    checks = []

    def is_set():
        checks.append(True)
        return len(checks) == 3  # Before sheet, row 0, then row 100.

    result = se.search_in_excel_special('fake.xlsx', 'needle', use_complex_search=True,
                                       stop_event=SimpleNamespace(is_set=is_set))
    assert result is None
    assert len(checks) == 3


def test_empty_cells_keep_per_file_limit_markers(fake_sheet, monkeypatch):
    rows, _ = fake_sheet
    rows[:] = [['', 'needle', '', 'needle', 'needle']]
    monkeypatch.setattr(se, '_positive_limit', lambda *args: 1)
    result = se.search_in_excel_special('fake.xlsx', 'needle', use_complex_search=True)
    assert result and result[1] == 2
    assert result[2][0] == (0, 'Data', 'B1', 'needle')
    assert result[2][1][0] == -1


def test_row_iterator_failure_is_not_silently_ignored(fake_sheet):
    _, sheet = fake_sheet

    def rows():
        yield ['', None]
        raise RuntimeError('injected iterator failure')

    sheet.iter_rows = rows
    result = se.search_in_excel_special('fake.xlsx', 'needle', use_complex_search=True)
    assert result and result[0] == Constants.STATUS_SKIPPED
