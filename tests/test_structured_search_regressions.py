import json

import pytest

from core import search_engine
from core.search_engine import (
    find_files_with_keyword_fast,
    format_skip_reason,
    search_directory_fast,
    search_files_list_fast,
    search_in_json_special,
    search_in_xml_special,
)
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants


requires_rust = pytest.mark.skipif(
    not search_engine.HAS_RUST_ENGINE,
    reason="Rust extension is required for integration coverage",
)


@requires_rust
@pytest.mark.parametrize(
    ("name", "content", "mode", "extension", "expected_message"),
    [
        ("broken.json", "{ malformed ", Constants.MODE_JSON, "json", "JSON"),
        ("broken.xml", "<root><v>needle</root>", Constants.MODE_XML, "xml", "XML"),
    ],
)
def test_directory_search_reports_malformed_structured_files(
    tmp_path, name, content, mode, extension, expected_message
):
    file_path = tmp_path / name
    file_path.write_text(content, encoding="utf-8")

    result = search_directory_fast(
        [str(tmp_path)],
        "needle",
        extensions=[extension],
        special_mode=mode,
        existence_only=True,
    )

    assert result["results"] == []
    assert len(result["skipped"]) == 1
    assert result["skipped"][0][0] == str(file_path)
    assert expected_message in result["skipped"][0][1]


@requires_rust
def test_file_list_and_smart_scan_report_malformed_json(tmp_path):
    file_path = tmp_path / "broken.json"
    file_path.write_text('{"value":"needle",', encoding="utf-8")

    list_result = search_files_list_fast(
        [str(file_path)],
        "needle",
        special_mode=Constants.MODE_JSON,
        existence_only=True,
    )
    found, smart_skipped = find_files_with_keyword_fast(
        [str(tmp_path)],
        "needle",
        extensions=["json"],
        special_mode=Constants.MODE_JSON,
        existence_only=True,
        return_skipped=True,
    )

    assert list_result["results"] == []
    assert len(list_result["skipped"]) == 1
    assert "JSON" in list_result["skipped"][0][1]
    assert found == []
    assert len(smart_skipped) == 1
    assert smart_skipped[0][0] == str(file_path)
    assert "JSON" in smart_skipped[0][1]


@requires_rust
def test_single_file_xml_search_rejects_match_before_parse_error(tmp_path):
    file_path = tmp_path / "broken.xml"
    file_path.write_text("<root><value>needle</root>", encoding="utf-8")

    result = search_in_xml_special(str(file_path), "needle", existence_only=True)

    assert result is not None
    assert result[0] == Constants.STATUS_SKIPPED
    assert result[1] == AppStrings.ERROR_XML_PARSE.format(
        AppStrings.XML_DETAIL_TAG_MISMATCH.format("</value>", "</root>")
    )
    assert "Expecting" not in result[1]


@pytest.mark.parametrize(
    ("raw_reason", "expected_detail"),
    [
        (
            "ERR_XML_PARSE|Expecting </root> found </broken_root>",
            AppStrings.XML_DETAIL_TAG_MISMATCH.format("</root>", "</broken_root>"),
        ),
        (
            "ERR_XML_PARSE|DOCTYPE declaration must appear before the root element",
            AppStrings.XML_DETAIL_DOCTYPE_POSITION,
        ),
    ],
)
def test_xml_parser_details_are_localized_for_skipped_file_popup(raw_reason, expected_detail):
    localized = format_skip_reason(raw_reason)

    assert localized == AppStrings.ERROR_XML_PARSE.format(expected_detail)
    assert "Expecting" not in localized
    assert "must appear" not in localized


@requires_rust
@pytest.mark.parametrize(
    "content",
    [
        "<root>needle</root><!DOCTYPE root>",
        "<!DOCTYPE root><!DOCTYPE root><root>needle</root>",
        "<root>needle</root><?xml version='1.0'?>",
    ],
)
def test_rust_xml_search_rejects_invalid_document_ordering(tmp_path, content):
    file_path = tmp_path / "invalid-order.xml"
    file_path.write_text(content, encoding="utf-8")

    result = search_in_xml_special(str(file_path), "needle", existence_only=True)

    assert result is not None
    assert result[0] == Constants.STATUS_SKIPPED
    assert "XML" in result[1]


@pytest.mark.parametrize("use_complex_search", [False, True])
def test_xml_dtd_is_explicitly_unsupported(tmp_path, use_complex_search):
    if not use_complex_search and not search_engine.HAS_RUST_ENGINE:
        pytest.skip("Rust extension is required for the fast XML path")
    file_path = tmp_path / "entity.xml"
    file_path.write_text(
        '<!DOCTYPE root [<!ENTITY company "ACME">]><root>&company;</root>',
        encoding="utf-8",
    )

    result = search_in_xml_special(
        str(file_path),
        "ACME",
        use_complex_search=use_complex_search,
        existence_only=True,
    )

    assert result is not None
    assert result[0] == Constants.STATUS_SKIPPED
    assert "DTD" in result[1]


def test_python_xml_existence_search_validates_the_entire_document(tmp_path):
    file_path = tmp_path / "broken-tail.xml"
    file_path.write_text("<root>needle</root><extra/>", encoding="utf-8")

    result = search_in_xml_special(
        str(file_path),
        "needle",
        use_complex_search=True,
        existence_only=True,
    )

    assert result is not None
    assert result[0] == Constants.STATUS_SKIPPED


def test_python_xml_search_matches_text_split_by_character_callbacks(tmp_path):
    file_path = tmp_path / "split-character-data.xml"
    file_path.write_text("<root><v>need&#108;e</v></root>", encoding="utf-8")

    result = search_in_xml_special(
        str(file_path),
        "needle",
        use_complex_search=True,
    )

    assert result is not None
    assert result[0] == str(file_path)
    assert result[1] == 1
    assert result[2][0][2] == "needle"


def test_python_json_fallback_uses_json_boolean_and_null_literals(tmp_path):
    file_path = tmp_path / "scalars.json"
    file_path.write_text(
        json.dumps({"enabled": True, "disabled": False, "missing": None}),
        encoding="utf-8",
    )

    true_result = search_in_json_special(
        str(file_path), "true", exact_match=True, use_complex_search=True
    )
    false_result = search_in_json_special(
        str(file_path), "false", exact_match=True, use_complex_search=True
    )
    null_result = search_in_json_special(
        str(file_path), "null", exact_match=True, use_complex_search=True
    )

    assert true_result is not None and true_result[2][0][2] == "true"
    assert false_result is not None and false_result[2][0][2] == "false"
    assert null_result is not None and null_result[2][0][2] == "null"
    assert search_in_json_special(
        str(file_path), "True", exact_match=True, use_complex_search=True
    ) is not None


@requires_rust
def test_python_precise_json_uses_stack_safe_fallback_for_deep_document(tmp_path):
    depth = 1_500
    file_path = tmp_path / "deep.json"
    file_path.write_text("[" * depth + '"needle"' + "]" * depth, encoding="utf-8")

    result = search_in_json_special(
        str(file_path),
        "needle",
        exact_match=True,
        use_complex_search=True,
    )

    assert result is not None
    assert result[0] == str(file_path)
    assert result[1] == 1
    assert result[2][0][2] == "needle"


@requires_rust
def test_rust_json_search_matches_true_literal(tmp_path):
    file_path = tmp_path / "active.json"
    file_path.write_text('{"active": true}', encoding="utf-8")

    result = search_in_json_special(str(file_path), "true", exact_match=True)

    assert result is not None
    assert result[0] == str(file_path)
    assert result[1] == 1
    assert result[2][0][1] == "active"
    assert result[2][0][2] == "true"


@requires_rust
def test_json_location_targets_value_and_omits_unverifiable_escape_location(tmp_path):
    regular_path = tmp_path / "regular.json"
    regular_path.write_text(
        '{\n  "needle": "other",\n  "value": "needle"\n}', encoding="utf-8"
    )
    escaped_path = tmp_path / "escaped.json"
    escaped_path.write_text(
        '{"value":"\\u006e\\u0065\\u0065\\u0064\\u006c\\u0065"}',
        encoding="utf-8",
    )

    regular = search_in_json_special(str(regular_path), "needle")
    escaped = search_in_json_special(str(escaped_path), "needle")

    assert regular is not None
    assert regular[2][0][0] == 3
    assert regular[2][0][3] == regular_path.read_bytes().rfind(b"needle")
    assert escaped is not None
    assert escaped[2][0][0] == ""
    assert escaped[2][0][3] is None
    assert escaped[2][0][4] is None


def test_json_parse_message_does_not_leak_format_placeholder(tmp_path):
    file_path = tmp_path / "broken.json"
    file_path.write_text('{"value":', encoding="utf-8")

    result = search_in_json_special(
        str(file_path), "needle", use_complex_search=True
    )

    assert result is not None
    assert result[0] == Constants.STATUS_SKIPPED
    assert "{}" not in result[1]
    assert AppStrings.ERROR_JSON_PARSE.split(":", 1)[0] in result[1]
