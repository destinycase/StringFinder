"""UE 현지화 archive 값만 검색: 구조 해석, 비교, 형식 오류, 인코딩, 상한, 화면 계약.

기본 검색(Rust)과 글자 변형 검색(Python)이 같은 표본에서 같은 결과를 내는지 함께 확인합니다.
표본 구조는 5.5.0까지 제공하던 archive 샘플 생성 스크립트와 같습니다.
"""
import json

import pytest
from PySide6.QtCore import Qt

from core import search_engine as engine
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants as C
from sf_utils.localization import get_language, set_language
from ui.models import MatchDetailModel
from ui.panels import SearchOptionsPanel

SAMPLE = {
    "Subnamespaces": [
        {
            "Namespace": "System_UI",
            "Children": [
                {"Key": "ID_BTN_CONFIRM", "Source": {"Text": "Confirm and Proceed"},
                 "Translation": {"Text": "확인 후 진행"}},
                {"Key": "ID_LBL_WELCOME", "Source": {"Text": "Welcome to StringFinder"},
                 "Translation": {"Text": "StringFinder에 오신 것을 환영합니다"}},
            ],
        },
        {
            "Namespace": "Game_Dialog",
            "Children": [
                {"Key": "NPC_GREET_01", "Source": {"Text": "Hello, brave traveler!"},
                 "Translation": {"Text": "안녕, 용감한 여행자여!"}},
                {"Key": "QUEST_DESC_01", "Source": {"Text": "Locate the hidden Abyss Point in the deep cave."},
                 "Translation": {"Text": "깊은 동굴 속 숨겨진 어비스 포인트를 찾으세요."}},
            ],
        },
    ]
}
ENGINES = pytest.mark.parametrize("precise", [False, True], ids=["rust", "python"])


@pytest.fixture(autouse=True)
def fixed_policy():
    if not engine.HAS_RUST_ENGINE:
        pytest.skip("the default archive path requires the real Rust engine")
    with engine.use_search_settings_snapshot({
        C.CONFIG_KEY_MAX_PER_FILE_MATCHES: 100,
        C.CONFIG_KEY_SEARCH_ENCODING: "auto",
        C.CONFIG_KEY_ALLOW_DUPLICATE_JSON_KEYS: True,
    }):
        yield


def write_archive(tmp_path, document, name="Game.archive", encoding="utf-8"):
    path = tmp_path / name
    text = document if isinstance(document, str) else json.dumps(document, ensure_ascii=False, indent=4)
    path.write_bytes(text.encode(encoding))
    return path


def search(path, query, precise, exact=False):
    return engine.search_in_archive_special(str(path), query, exact, use_complex_search=precise)


def rows(result):
    assert result is not None and result[0] != C.STATUS_SKIPPED, result
    return [tuple(match[1:5]) for match in result[2]]


@ENGINES
@pytest.mark.parametrize("query,expected", [
    ("proceed", [("System_UI", "ID_BTN_CONFIRM", "Confirm and Proceed", "확인 후 진행")]),
    ("여행자", [("Game_Dialog", "NPC_GREET_01", "Hello, brave traveler!", "안녕, 용감한 여행자여!")]),
    ("npc_greet", [("Game_Dialog", "NPC_GREET_01", "Hello, brave traveler!", "안녕, 용감한 여행자여!")]),
])
def test_source_translation_and_key_are_searched(tmp_path, precise, query, expected):
    assert rows(search(write_archive(tmp_path, SAMPLE), query, precise)) == expected


@ENGINES
def test_namespace_and_json_field_names_are_not_searched(tmp_path, precise):
    path = write_archive(tmp_path, SAMPLE)
    for query in ("System_UI", "Subnamespaces", "Translation"):
        assert search(path, query, precise) is None, query


@ENGINES
def test_one_entry_is_one_result_when_several_values_match(tmp_path, precise):
    result = search(write_archive(tmp_path, SAMPLE), "StringFinder", precise)
    assert result[1] == 1
    assert rows(result) == [("System_UI", "ID_LBL_WELCOME", "Welcome to StringFinder",
                             "StringFinder에 오신 것을 환영합니다")]


@ENGINES
def test_exact_match_compares_each_whole_value(tmp_path, precise):
    path = write_archive(tmp_path, SAMPLE)
    assert len(rows(search(path, "id_btn_confirm", precise, exact=True))) == 1
    assert len(rows(search(path, "확인 후 진행", precise, exact=True))) == 1
    assert search(path, "ID_BTN", precise, exact=True) is None


@ENGINES
def test_root_children_and_nested_namespaces_keep_document_order(tmp_path, precise):
    document = {
        "FormatVersion": 2,
        "Namespace": "",
        "Children": [{"Key": "ROOT", "Source": {"Text": "needle root"}, "Translation": {"Text": ""},
                      "MetaData": {"ignored": "needle in metadata"}}],
        "Subnamespaces": [
            {"Namespace": "UI", "Subnamespaces": [
                {"Namespace": "Menu", "Children": [
                    {"Key": "DEEP", "Source": {"Text": "needle deep"}, "Translation": {"Text": "x"}}]}]},
            {"Namespace": "Last", "Children": [
                {"Key": "LAST", "Source": {"Text": "needle last"}, "Translation": {"Text": "y"}}]},
        ],
    }
    found = rows(search(write_archive(tmp_path, document), "needle", precise))
    assert [(namespace, key) for namespace, key, _, _ in found] == [("", "ROOT"), ("UI.Menu", "DEEP"),
                                                                  ("Last", "LAST")]


@ENGINES
@pytest.mark.parametrize("document,location", [
    ([], "/"),
    ({"FormatVersion": 2}, "/Children"),
    ({"Subnamespaces": [{"Children": [{"Source": {"Text": "a"}, "Translation": {"Text": "b"}}]}]},
     "/Subnamespaces/0/Children/0/Key"),
    ({"Children": [{"Key": "k", "Source": {"Text": "needle"}}]}, "/Children/0/Translation/Text"),
    ({"Children": {"Key": "k"}}, "/Children"),
    ({"Namespace": 3, "Children": []}, "/Namespace"),
])
def test_structure_violations_are_skipped_with_location(tmp_path, precise, document, location):
    result = search(write_archive(tmp_path, document), "needle", precise)
    assert result == (C.STATUS_SKIPPED, AppStrings.ERROR_ARCHIVE_FORMAT.format(location))


@ENGINES
def test_invalid_entry_after_a_hit_discards_the_whole_file(tmp_path, precise):
    document = {"Children": [
        {"Key": "OK", "Source": {"Text": "needle"}, "Translation": {"Text": ""}},
        {"Key": "BROKEN", "Source": {"Text": "needle"}},
    ]}
    result = search(write_archive(tmp_path, document), "needle", precise)
    assert result[0] == C.STATUS_SKIPPED


@ENGINES
@pytest.mark.parametrize("text", [
    '{"Children": [',
    '{"Children": [], "Children": []}',  # 중복 키 허용 설정과 관계없이 archive는 건너뜁니다.
])
def test_json_errors_and_duplicate_keys_are_skipped(tmp_path, precise, text):
    result = search(write_archive(tmp_path, text), "needle", precise)
    assert result[0] == C.STATUS_SKIPPED
    assert result[1].startswith(AppStrings.ERROR_JSON_PARSE.split("{")[0])


@ENGINES
@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16", "utf-16-be"])
def test_supported_encodings_return_identical_rows(tmp_path, precise, encoding):
    text = json.dumps(SAMPLE, ensure_ascii=False)
    if encoding == "utf-16-be":
        raw = b"\xfe\xff" + text.encode("utf-16-be")
        path = tmp_path / "Game.archive"
        path.write_bytes(raw)
    else:
        path = write_archive(tmp_path, text, encoding=encoding)
    assert rows(search(path, "여행자", precise)) == [
        ("Game_Dialog", "NPC_GREET_01", "Hello, brave traveler!", "안녕, 용감한 여행자여!")]


@ENGINES
def test_per_file_limit_keeps_exactly_the_limit_and_one_notice(tmp_path, precise):
    children = [{"Key": f"K{index}", "Source": {"Text": "needle"}, "Translation": {"Text": ""}}
                for index in range(5)]
    path = write_archive(tmp_path, {"Children": children})
    with engine.use_search_settings_snapshot({C.CONFIG_KEY_MAX_PER_FILE_MATCHES: 2,
                                              C.CONFIG_KEY_SEARCH_ENCODING: "auto"}):
        result = search(path, "needle", precise)
    visible = [match for match in result[2] if str(match[0]) not in {"-1", "-2"}]
    notices = [match for match in result[2] if str(match[0]) == "-1"]
    assert [match[2] for match in visible] == ["K0", "K1"]
    assert len(notices) == 1


def test_directory_and_file_list_paths_return_archive_rows(tmp_path):
    path = write_archive(tmp_path, SAMPLE)
    (tmp_path / "plain.txt").write_text("needle Proceed", encoding="utf-8")
    mode = AppStrings.SPECIAL_SEARCH_ITEMS[7]
    directory = engine.search_directory_fast([str(tmp_path)], "proceed", [C.EXT_ARCHIVE], special_mode=mode)
    listed = engine.search_files_list_fast([str(path)], "proceed", special_mode=mode)
    for outcome in (directory, listed):
        assert outcome["skipped"] == []
        assert [(row[0], row[1]) for row in outcome["results"]] == [(str(path), 1)]
        assert tuple(outcome["results"][0][2][0][1:5]) == (
            "System_UI", "ID_BTN_CONFIRM", "Confirm and Proceed", "확인 후 진행")


def test_values_only_off_searches_archive_files_as_plain_text(tmp_path):
    path = write_archive(tmp_path, SAMPLE)
    result = engine.search_in_file(str(path), "System_UI")
    assert result is not None and result[1] == 1


def test_format_error_reason_survives_language_switch(tmp_path):
    previous = get_language()
    try:
        set_language("ko")
        korean = AppStrings.ERROR_ARCHIVE_FORMAT.format("/Children/0/Key")
        set_language("en-US")
        assert engine.localize_skip_reason_for_display(korean) == AppStrings.ERROR_ARCHIVE_FORMAT.format(
            "/Children/0/Key")
    finally:
        set_language(previous)


@pytest.fixture(params=["ko", "en-US"])
def language(request):
    previous = get_language()
    set_language(request.param)
    yield request.param
    set_language(previous)


def test_menu_titles_and_compact_button_text(qtbot, language):
    panel = SearchOptionsPanel()
    qtbot.addWidget(panel)
    titles = [action.text() for action in panel.value_menu.actions()][-2:]
    compact = AppStrings.SPECIAL_SEARCH_COMPACT_ITEMS[7:]
    if language == "ko":
        assert titles == ["UE 현지화 archive (부분 일치)", "UE 현지화 archive (정확히 일치)"]
        assert compact == ["ARCHIVE (부분 일치)", "ARCHIVE (정확히 일치)"]
    else:
        assert titles == ["UE localization archive (contains)", "UE localization archive (exact)"]
        assert compact == ["ARCHIVE (contains)", "ARCHIVE (exact)"]
    panel.special_mode_actions[AppStrings.SPECIAL_SEARCH_ITEMS[8]].trigger()
    assert panel.search_settings_button.text() == f"{AppStrings.SEARCH_PROFILE_NORMAL_COMPACT} · {compact[1]}"
    for value in AppStrings.SPECIAL_SEARCH_ITEMS[7:]:
        # 다른 형식의 판별 글자가 섞이면 엉뚱한 파서로 연결됩니다.
        assert not any(code.casefold() in value.casefold()
                       for code in (C.MODE_JSON, C.MODE_XML, C.MODE_EXCEL))
        assert C.MODE_ARCHIVE in value


def test_target_extension_follows_archive_mode(qtbot, mock_config_manager):
    from ui.search_tab import SearchTab

    tab = SearchTab(mock_config_manager)
    qtbot.addWidget(tab)
    tab.search_panel.special_mode_actions[AppStrings.SPECIAL_SEARCH_ITEMS[7]].trigger()
    assert tab.ext_panel.get_selected_extensions() == [C.EXT_ARCHIVE]
    tab.close()


def test_detail_model_shows_four_archive_columns():
    model = MatchDetailModel()
    model.set_matches(
        "Game.archive",
        [("", "System_UI", "ID_BTN_CONFIRM", "Confirm and Proceed", "확인 후 진행", None, None),
         (-1, AppStrings.MSG_MATCH_LIMIT_PER_FILE.format(1), "", "", "", None, None)],
        "proceed",
        AppStrings.SPECIAL_SEARCH_ITEMS[7],
    )
    assert [model.headerData(index, Qt.Orientation.Horizontal) for index in range(model.columnCount())] == [
        AppStrings.HEADER_ARCHIVE_NAMESPACE, AppStrings.HEADER_ARCHIVE_KEY,
        AppStrings.HEADER_ARCHIVE_SOURCE, AppStrings.HEADER_ARCHIVE_TRANSLATION,
    ]
    first = model.get_match(0)
    assert (first.position, first.content, first.extra_1, first.extra_2) == (
        "System_UI", "ID_BTN_CONFIRM", "Confirm and Proceed", "확인 후 진행")
    model.set_column_filter(3, "확인")
    assert model.rowCount() == 1
    model.set_column_filter(3, "없음")
    assert model.rowCount() == 0
