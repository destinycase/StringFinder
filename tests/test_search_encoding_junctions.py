"""Search-candidate and scalar boundary contracts, including native directory paths."""
import os
import subprocess
from datetime import date, datetime, time

import pytest
from openpyxl import Workbook
from openpyxl.utils.datetime import CALENDAR_MAC_1904

from core import search_engine as se
from sf_utils.config_manager import ConfigManager
from sf_utils.constants import Constants as C


@pytest.fixture(autouse=True)
def snapshot():
    with se.use_search_settings_snapshot({C.CONFIG_KEY_MAX_PER_FILE_MATCHES: 10000}):
        yield


def compare(path, query, mode=None, existence=False):
    native = se.search_directory_fast([str(path)], query, [], special_mode=mode,
                                     exclude_hidden=False, exclude_binary=True, existence_only=existence)
    precise = se.search_in_file(str(path), query, special_mode=mode,
                               use_complex_search=True, existence_only=existence, exclude_binary=True)
    assert not native['skipped']
    assert precise is None or precise[0] != C.STATUS_SKIPPED
    return sum(r[1] for r in native['results']), precise[1] if precise else 0


@pytest.mark.parametrize('encoding', ['utf-16-le', 'utf-16-be'])
@pytest.mark.parametrize('existence', [False, True])
def test_automatic_korean_utf16_prefix(tmp_path, encoding, existence):
    path = tmp_path / 'korean.txt'
    path.write_bytes(('가나다' * 30000 + '\nneedle\n').encode(encoding))
    assert compare(path, 'needle', existence=existence) == (1, 1)


def test_cp949_korean_is_not_mistaken_for_utf16(tmp_path):
    path = tmp_path / 'legacy.txt'
    path.write_bytes(('가나다' * 30000 + '\nneedle\n').encode('cp949'))
    assert se.detect_encoding_quickly(path.read_bytes()[:65536]) == 'cp949'
    assert compare(path, 'needle') == (1, 1)


@pytest.mark.parametrize('encoding', ['utf-8', 'cp949', 'utf-16-le', 'utf-16-be'])
@pytest.mark.parametrize('mode,suffix,content', [(None,'txt','needle 한글'),
    (C.MODE_JSON,'json','{"v":"needle 한글"}'), (C.MODE_XML,'xml','<root>needle 한글</root>')])
@pytest.mark.parametrize('existence', [False, True])
def test_explicit_encoding_all_paths(tmp_path, encoding, mode, suffix, content, existence):
    path = tmp_path / ('data.' + suffix)
    path.write_bytes(content.encode(encoding))
    with se.use_search_settings_snapshot({C.CONFIG_KEY_SEARCH_ENCODING: encoding}):
        assert compare(path, '한글', mode, existence) == (1, 1)
        listed = se.search_files_list_fast([str(path)], '한글', special_mode=mode, existence_only=existence)
        assert len(listed['results']) == 1 and not listed['skipped']
        found, skipped = se.find_files_with_keyword_fast([str(path)], '한글', special_mode=mode, return_skipped=True)
        assert len(found) == 1 and not skipped


@pytest.mark.parametrize('existence', [False, True])
@pytest.mark.parametrize('number', ['18446744073709551617','-9223372036854775809','18446744073709551615','-9223372036854775808','-0', '9' * 512])
def test_json_integer_boundaries(tmp_path, number, existence):
    path = tmp_path / 'number.json'
    path.write_text('{"v":' + number + '}', encoding='utf-8')
    assert compare(path, str(int(number)), C.MODE_JSON, existence) == (1, 1)


@pytest.mark.parametrize('content', ['{"$serde_json::private::Number":"needle"}', '{"$serde_json::private::Numb\\u0065r":"needle"}'])
def test_private_number_key_is_real_object(tmp_path, content):
    path = tmp_path / 'object.json'
    path.write_text(content, encoding='utf-8')
    assert compare(path, 'needle', C.MODE_JSON) == (1, 1)


@pytest.mark.parametrize('content', [
    '[[],{},1.25,{"v":18446744073709551617},[],2]',
    '{"a":{},"b":[],"c":[[],{},18446744073709551617],"v":-0}',
    '{"a":[[[[[1]]]]],"v":18446744073709551617}',
])
def test_numeric_fast_path_keeps_container_cursor_aligned(tmp_path, content):
    path = tmp_path / 'mixed.json'
    path.write_text(content, encoding='utf-8')
    for existence in (False, True):
        assert compare(path, '18446744073709551617', C.MODE_JSON, existence) == (1, 1)


def test_json_overflow_is_explicitly_skipped(tmp_path):
    path = tmp_path / 'overflow.json'
    path.write_text('{"v":1e400}', encoding='utf-8')
    output = se.search_directory_fast([str(path)], 'inf', [], special_mode=C.MODE_JSON)
    assert not output['results'] and output['skipped']
    result = se.search_in_file(str(path), 'inf', special_mode=C.MODE_JSON, use_complex_search=True)
    assert result[0] == C.STATUS_SKIPPED


@pytest.mark.parametrize('value,query', [(date(1904,1,1),'1904-01-01'),
    (datetime(1904,1,1,12),'1904-01-01 12:00:00'), (time(12),'12:00:00')])
@pytest.mark.parametrize('existence', [False, True])
def test_excel_1904_dates_and_times(tmp_path, value, query, existence):
    path = tmp_path / 'dates.xlsx'
    wb = Workbook()
    wb.epoch = CALENDAR_MAC_1904
    wb.active['C4'] = value
    wb.save(path)
    assert compare(path, query, C.MODE_EXCEL, existence) == (1, 1)
    if isinstance(value, time):
        assert compare(path, '1904-01-01', C.MODE_EXCEL, existence) == (0, 0)


@pytest.fixture
def junction_tree(tmp_path):
    if os.name != 'nt':
        pytest.skip('Windows junctions')
    target, scan = tmp_path / 'target', tmp_path / 'scan'
    target.mkdir()
    scan.mkdir()
    (target / 'needle.txt').write_text('needle', encoding='utf-8')
    links = [scan / 'first', scan / 'second', target / 'cycle']
    try:
        for link, destination in zip(links, [target, target, scan]):
            completed = subprocess.run(['cmd','/c','mklink','/J',str(link),str(destination)],capture_output=True)
            if completed.returncode:
                pytest.skip('junction creation unavailable')
        yield scan
    finally:
        for link in reversed(links):
            if os.path.isjunction(link):
                os.rmdir(link)


@pytest.mark.parametrize('include', [False, True])
def test_junction_policy_cycle_and_duplicate(junction_tree, include):
    with se.use_search_settings_snapshot({C.CONFIG_KEY_INCLUDE_JUNCTIONS: include}):
        scanner = se.FileScanner([str(junction_tree)], [])
        assert len(scanner.scan()) == int(include)
        normal = se.search_directory_fast([str(junction_tree)], 'needle', [], exclude_hidden=False)
        assert len(normal['results']) == int(include)
        found = se.find_files_with_keyword_fast([str(junction_tree)], 'needle', exclude_hidden=False)
        assert len(found) == int(include)


@pytest.mark.parametrize('value', [None, 12, True, {}, [], 'unknown'])
def test_invalid_encoding_setting_resets(value):
    manager = object.__new__(ConfigManager)
    normalized = manager._normalize_advanced_settings({C.CONFIG_KEY_SEARCH_ENCODING: value})
    assert normalized[C.CONFIG_KEY_SEARCH_ENCODING] == 'auto'
    assert normalized[C.CONFIG_KEY_INCLUDE_JUNCTIONS] is False


def test_settings_choices_and_persistence(qtbot, mock_config_manager):
    from ui.settings_dialog import SettingsDialog
    dialog = SettingsDialog(mock_config_manager)
    qtbot.addWidget(dialog)
    from PySide6.QtWidgets import QLabel
    from sf_utils.app_strings import AppStrings
    descriptions = {
        label.text(): label
        for label in dialog.findChildren(QLabel, 'advancedSettingDescription')
    }
    reference = descriptions[AppStrings.JSON_DUPLICATE_KEYS_DESCRIPTION]
    for text in (AppStrings.JUNCTION_DESCRIPTION, AppStrings.SEARCH_ENCODING_DESCRIPTION):
        assert descriptions[text].styleSheet() == reference.styleSheet()
        assert descriptions[text].wordWrap() == reference.wordWrap()
    assert dialog.include_junctions_combo.currentData() is False
    assert dialog.search_encoding_combo.currentData() == 'auto'
    dialog.include_junctions_combo.setCurrentIndex(1)
    dialog.search_encoding_combo.setCurrentIndex(dialog.search_encoding_combo.findData('utf-16-be'))
    settings = mock_config_manager.get_advanced_settings()
    assert settings[C.CONFIG_KEY_INCLUDE_JUNCTIONS] is True
    assert settings[C.CONFIG_KEY_SEARCH_ENCODING] == 'utf-16-be'
    from unittest.mock import patch
    with patch('ui.settings_dialog.QMessageBox.information'):
        dialog._reset_advanced_settings()
    assert dialog.include_junctions_combo.currentData() is False
    assert dialog.search_encoding_combo.currentData() == 'auto'
    dialog.close()


def test_selected_encoding_does_not_filter_or_fallback(tmp_path):
    good, bad = tmp_path / 'good.txt', tmp_path / 'bad.txt'
    good.write_text('한글', encoding='utf-8')
    bad.write_bytes('한글'.encode('cp949'))
    with se.use_search_settings_snapshot({C.CONFIG_KEY_SEARCH_ENCODING: 'utf-8'}):
        normal = se.search_directory_fast([str(tmp_path)], '한글', [])
        assert len(normal['results']) == 1 and len(normal['skipped']) == 1
        precise = se.search_in_file(str(bad), '한글', use_complex_search=True)
        assert precise[0] == C.STATUS_SKIPPED
        found, skipped = se.find_files_with_keyword_fast([str(tmp_path)], '한글', return_skipped=True)
        assert len(found) == 1 and len(skipped) == 1
