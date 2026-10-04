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
MODES = [None, C.MODE_JSON, C.MODE_XML, C.MODE_EXCEL]


@pytest.fixture(scope="module", params=MODES, ids=["text", "json", "xml", "excel"])
def corpus(request, tmp_path_factory):
    mode = request.param
    root = tmp_path_factory.mktemp("path_matrix")
    suffix = {None: "txt", C.MODE_JSON: "json", C.MODE_XML: "xml", C.MODE_EXCEL: "xlsx"}[mode]
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
    return [(match[0], match[1]) for match in row[2]]


@pytest.mark.parametrize("precise", [False, True], ids=["normal", "precise"])
@pytest.mark.parametrize("existence", [False, True], ids=["full", "existence"])
@pytest.mark.parametrize("exclude_hidden", [False, True], ids=["include-hidden", "exclude-hidden"])
@pytest.mark.parametrize("narrow", [False, True], ids=["all-names", "filename-filter"])
def test_candidate_and_result_oracles(corpus, precise, existence, exclude_hidden, narrow):
    root, mode, suffix, manifest = corpus
    extensions = [] if mode is None else [suffix.upper()]
    filename_filter = ["KEEP"] if narrow else None
    roots = [str(root), str(root / "nested"), str(root)]
    if narrow:
        # Parent/child order must not change coverage.
        roots = [str(root / "nested"), str(root), str(root)]
    expected_candidates = {
        name for name in manifest
        if "$RECYCLE.BIN" not in Path(name).parts
        and (not exclude_hidden or not any(part.startswith(".") for part in Path(name).parts))
        and (not extensions or Path(name).suffix.lower() == f".{suffix}")
        and (not narrow or "keep" in Path(name).name.lower())
    }
    scanner = engine.FileScanner(roots, extensions, filename_filter=filename_filter,
                                 exclude_hidden=exclude_hidden)
    files = scanner.scan()
    assert scanner.skipped == []
    assert_paths(files, root, expected_candidates, "candidate enumeration")

    def search(query):
        if precise:
            return engine.search_in_files_batch(
                files, query, special_mode=mode, use_complex_search=True,
                existence_only=existence,
            )
        return engine.search_directory_fast(
            roots, query, extensions, special_mode=mode, filename_filter=filename_filter,
            exclude_hidden=exclude_hidden, existence_only=existence,
        )

    probe = search(PROBE)
    assert probe["skipped"] == []
    assert_paths(probe["results"], root, expected_candidates, "candidate search canary")
    for row in probe["results"]:
        expected_count = 1 if existence else len(manifest[relative(root, row[0])][1])
        assert row[1] == len(row[2]) == expected_count
    expected_hits = {name for name in expected_candidates if manifest[name][0]}
    found = search(QUERY)
    assert found["skipped"] == []
    assert_paths(found["results"], root, expected_hits, "actual query results")
    for row in found["results"]:
        assert row[1] == (1 if existence else 2), relative(root, row[0])
        assert len(row[2]) == row[1]
        if not existence:
            values = manifest[relative(root, row[0])][1]
            expected = {
                None: [(1, values[0]), (2, values[1])],
                C.MODE_JSON: [("first", values[0]), ("last", values[1])],
                # Python's XML display paths omit the document root. Test each
                # contract explicitly rather than treating that as lost data.
                C.MODE_XML: [("first" if precise else "root/first", values[0]),
                             ("last" if precise else "root/last", values[1])],
                C.MODE_EXCEL: [("Data", "B2", values[0]), ("Data", "D9", values[1])],
            }[mode]
            # JSON object traversal order is not a cross-backend contract.
            # Counters still catch duplicates and missing positions/values.
            assert Counter(semantic_details(row, mode)) == Counter(expected)

    # The explicit-file native endpoint must obey the same search semantics.
    listed = engine.search_files_list_fast([path for path, _ in files], QUERY,
                                          special_mode=mode, existence_only=existence)
    assert listed["skipped"] == []
    assert_paths(listed["results"], root, expected_hits, "explicit file results")
    assert {relative(root, row[0]): row[1] for row in listed["results"]} == {
        name: 1 if existence else 2 for name in expected_hits
    }


@pytest.mark.parametrize("precise", [False, True], ids=["normal", "precise"])
@pytest.mark.parametrize("existence", [False, True], ids=["full", "existence"])
@pytest.mark.parametrize("mode,suffix,content", [
    (C.MODE_JSON, "json", '{"v":"\\u006e\\u0065\\u0065\\u0064\\u006c\\u0065"}'),
    (C.MODE_XML, "xml", "<root><v>&#110;&#101;&#101;&#100;&#108;&#101;</v></root>"),
], ids=["json-escape", "xml-entity"])
def test_decoded_values_are_intentionally_different_from_raw_search(
    tmp_path, precise, existence, mode, suffix, content
):
    path = tmp_path / f"encoded.{suffix}"
    path.write_text(content, encoding="utf-8")
    for special, expected_count in [(None, 0), (mode, 1)]:
        single = engine.search_in_file(str(path), QUERY, special_mode=special,
                                       use_complex_search=precise, existence_only=existence)
        if expected_count:
            assert single and single[0] == str(path) and single[1] == 1
        else:
            assert single is None
        native = engine.search_directory_fast([str(tmp_path)], QUERY, [suffix],
                                               special_mode=special, existence_only=existence)
        assert native["skipped"] == []
        assert_paths(native["results"], tmp_path, {path.name} if expected_count else set(),
                     "decoded versus raw search")
        assert sum(row[1] for row in native["results"]) == expected_count


@pytest.mark.parametrize("precise", [False, True], ids=["normal", "precise"])
@pytest.mark.parametrize("existence", [False, True], ids=["full", "existence"])
def test_json_key_only_is_not_a_structured_value_result(tmp_path, precise, existence):
    path = tmp_path / "key.json"
    path.write_text('{"needle":"quiet"}', encoding="utf-8")
    raw = engine.search_in_file(str(path), QUERY, use_complex_search=precise,
                                existence_only=existence)
    assert raw and raw[1] == 1
    special = engine.search_in_file(str(path), QUERY, special_mode=C.MODE_JSON,
                                    use_complex_search=precise, existence_only=existence)
    assert special is None
    native = engine.search_files_list_fast([str(path)], QUERY, special_mode=C.MODE_JSON,
                                           existence_only=existence)
    assert native == {"results": [], "skipped": []}
