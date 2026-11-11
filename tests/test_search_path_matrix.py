"""Independent candidate/result oracles across real native and precise paths.

The probe occurs in every eligible document, including negative-result files.
This makes candidate loss distinguishable from query result loss. No scanner,
parser or matching implementation is mocked. Raw offsets/snippet formats are
not compared: native and precise paths have deliberately different contracts.
"""
from collections import Counter
import json
from pathlib import Path

import pytest
from openpyxl import Workbook

from core import search_engine as engine
from sf_utils.constants import Constants as C


PROBE = "sf_candidate_probe"
QUERY = "needle"
MODES = [None, C.MODE_JSON, C.MODE_XML, C.MODE_EXCEL, C.MODE_ARCHIVE]


@pytest.fixture(scope="module", params=MODES, ids=["text", "json", "xml", "excel", "archive"])
def corpus(request, tmp_path_factory):
    mode = request.param
    root = tmp_path_factory.mktemp("path_matrix")
    suffix = {None: "txt", C.MODE_JSON: "json", C.MODE_XML: "xml", C.MODE_EXCEL: "xlsx",
              C.MODE_ARCHIVE: "archive"}[mode]
    manifest = {}

    def document(relative, hit=True):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        values = [f"{PROBE} {QUERY if hit else 'quiet'} first", f"{PROBE} {QUERY if hit else 'quiet'} last"]
        if mode == C.MODE_EXCEL:
            book = Workbook()
            book.active.title = "Data"
            book.active["B2"], book.active["D9"] = values
            book.save(path)
            book.close()
        elif mode == C.MODE_JSON:
            path.write_text(json.dumps({"first": values[0], "last": values[1]}), encoding="utf-8")
        elif mode == C.MODE_ARCHIVE:
            children = [{"Key": key, "Source": {"Text": value}, "Translation": {"Text": ""}}
                        for key, value in zip(("first", "last"), values)]
            archive = {"Subnamespaces": [{"Namespace": "NS", "Children": children}]}
            path.write_text(json.dumps(archive), encoding="utf-8")
        elif mode == C.MODE_XML:
            path.write_text(f"<root><first>{values[0]}</first><last>{values[1]}</last></root>", encoding="utf-8")
        else:
            path.write_text("\n".join(values) + "\n", encoding="utf-8")
        manifest[relative] = (hit, values)

    for name, hit in [
        (f"keep_top.{suffix}", True),
        (f"nested/keep_nested.{suffix.upper()}", True),
        (f"ignored/keep_ignored.{suffix}", True),
        (f".keep_dot.{suffix}", True),
        (f".private/keep_private.{suffix}", True),
        (f"drop_name.{suffix}", True),
        (f"keep_negative.{suffix}", False),
        (f"$RECYCLE.BIN/keep_recycled.{suffix}", True),
    ]:
        document(name, hit)
    if mode is None:
        document("keep_no_extension")
        document("keep_other.data")
    else:
        # Same valid contents but a nonselected extension must not be searched.
        document("keep_wrong_extension.data")
    for name in (".gitignore", ".ignore"):
        (root / name).write_text(f"ignored/\nkeep_top.{suffix}\n# {PROBE}\n", encoding="utf-8")
        manifest[name] = (False, [f"# {PROBE}"])
    return root, mode, suffix, manifest


@pytest.fixture(autouse=True)
def fixed_policy():
    if not engine.HAS_RUST_ENGINE:
        pytest.skip("this differential contract requires the real Rust engine")
    with engine.use_search_settings_snapshot({
        C.CONFIG_KEY_MAX_PER_FILE_MATCHES: 100,
        C.CONFIG_KEY_MAX_TOTAL_MATCHES: 10000,
        C.CONFIG_KEY_SEARCH_ENCODING: "auto",
        C.CONFIG_KEY_INCLUDE_JUNCTIONS: False,
    }):
        yield


def relative(root, path):
    return Path(path).relative_to(root).as_posix()


def assert_paths(rows, root, expected, stage):
    # A set alone would hide duplicate results from overlapping roots.
    actual = Counter(relative(root, row[0]) for row in rows)
    assert actual == Counter({name: 1 for name in expected}), f"{stage}: {actual} != {expected}"


def semantic_details(row, mode):
    """Compare meaningful positions and complete short values, not byte offsets."""
    if mode == C.MODE_JSON:
        return [(match[1], match[2]) for match in row[2]]
    if mode == C.MODE_XML:
        return [(match[1].strip("/").replace(" > ", "/"), match[2]) for match in row[2]]
    if mode == C.MODE_EXCEL:
        return [(match[1], match[2], match[3]) for match in row[2]]
    if mode == C.MODE_ARCHIVE:
        return [(match[1], match[2], match[3]) for match in row[2]]
    return [(match[0], match[1]) for match in row[2]]


@pytest.mark.parametrize("precise", [False, True], ids=["normal", "precise"])
@pytest.mark.parametrize("mode,suffix,content", [
    (C.MODE_JSON, "json", '{"v":"\\u006e\\u0065\\u0065\\u0064\\u006c\\u0065"}'),
    (C.MODE_XML, "xml", "<root><v>&#110;&#101;&#101;&#100;&#108;&#101;</v></root>"),
], ids=["json-escape", "xml-entity"])
def test_decoded_values_are_intentionally_different_from_raw_search(
    tmp_path, precise, mode, suffix, content
):
    path = tmp_path / f"encoded.{suffix}"
    path.write_text(content, encoding="utf-8")
    for special, expected_count in [(None, 0), (mode, 1)]:
        single = engine.search_in_file(str(path), QUERY, special_mode=special)
        if expected_count:
            assert single and single[0] == str(path) and single[1] == 1
        else:
            assert single is None
        native = engine.search_directory_fast([str(tmp_path)], QUERY, [suffix],
                                               special_mode=special)
        assert native["skipped"] == []
        assert_paths(native["results"], tmp_path, {path.name} if expected_count else set(),
                     "decoded versus raw search")
        assert sum(row[1] for row in native["results"]) == expected_count


@pytest.mark.parametrize("precise", [False, True], ids=["normal", "precise"])
def test_json_key_only_is_not_a_structured_value_result(tmp_path, precise):
    path = tmp_path / "key.json"
    path.write_text('{"needle":"quiet"}', encoding="utf-8")
    raw = engine.search_in_file(str(path), QUERY,
                                )
    assert raw and raw[1] == 1
    special = engine.search_in_file(str(path), QUERY, special_mode=C.MODE_JSON)
    assert special is None
    native = engine.search_files_list_fast([str(path)], QUERY, special_mode=C.MODE_JSON,
                                           )
    assert native == {"results": [], "skipped": []}
