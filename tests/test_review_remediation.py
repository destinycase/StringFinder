"""Deterministic regressions for the 5.9.2 follow-up review."""

import pytest

from ui.models import SearchResultModel


class DeferredPool:
    def __init__(self):
        self.jobs = []

    def start(self, worker):
        self.jobs.append(worker)


@pytest.mark.parametrize("global_sort", [False, True])
@pytest.mark.parametrize("old_finishes_first", [False, True])
def test_old_sort_cannot_replace_new_search(monkeypatch, qtbot, global_sort, old_finishes_first):
    pool = DeferredPool()
    monkeypatch.setattr("ui.models.QThreadPool.globalInstance", lambda: pool)
    model = SearchResultModel()
    model.add_results([("old.txt", 1, [(1, "old")])])
    model.sort_globally() if global_sort else model.sort(1)
    model.clear()
    model.add_results([("new.txt", 1, [(1, "new")])])
    model.sort(1)
    if not old_finishes_first:
        pool.jobs[1].run()
    pool.jobs[0].run()
    assert [row[3] for row in model.get_all_results()] == ["new.txt"]
    assert model._is_sorting == old_finishes_first
    if old_finishes_first:
        pool.jobs[1].run()
    assert not model._is_sorting
    assert [row[3] for row in model.get_all_results()] == ["new.txt"]


def test_append_during_sort_preserves_results(monkeypatch, qtbot):
    pool = DeferredPool()
    monkeypatch.setattr("ui.models.QThreadPool.globalInstance", lambda: pool)
    model = SearchResultModel()
    model.add_results([("old.txt", 1, [])])
    model.sort(1)
    model.add_results([("new.txt", 1, [])])
    pool.jobs[0].run()
    assert {row[3] for row in model.get_all_results()} == {"old.txt", "new.txt"}
    assert not model._is_sorting


def test_sort_failure_preserves_results(monkeypatch, qtbot):
    pool = DeferredPool()
    monkeypatch.setattr("ui.models.QThreadPool.globalInstance", lambda: pool)
    model = SearchResultModel()
    model.add_results([("old.txt", 1, [])])
    model.sort(99)
    pool.jobs[0].run()
    assert [row[3] for row in model.get_all_results()] == ["old.txt"]
    assert not model._is_sorting


def test_sort_requested_after_append_uses_latest_data(monkeypatch, qtbot):
    pool = DeferredPool()
    monkeypatch.setattr("ui.models.QThreadPool.globalInstance", lambda: pool)
    model = SearchResultModel()
    model.add_results([("old.txt", 1, [])])
    model.sort(1)
    model.add_results([("new.txt", 2, [])])
    model.sort_results()
    pool.jobs[0].run()
    assert len(pool.jobs) == 2
    pool.jobs[1].run()
    assert [r[3] for r in model.get_all_results()] == ["new.txt", "old.txt"]


@pytest.mark.parametrize("failure", ["copy", "replace", None])
def test_binary_install_preserves_destination_on_failure(tmp_path, monkeypatch, failure):
    import build_rust

    source = tmp_path / "new.dll"
    destination = tmp_path / "engine.pyd"
    source.write_bytes(b"complete-new-engine")
    destination.write_bytes(b"previous-engine")

    def denied(*args, **kwargs):
        raise PermissionError("locked by another process")

    if failure == "copy":
        monkeypatch.setattr(build_rust.shutil, "copy2", denied)
    elif failure == "replace":
        monkeypatch.setattr(build_rust.os, "replace", denied)
    if failure:
        with pytest.raises(OSError, match="previous destination was not removed"):
            build_rust.install_binary(str(source), str(destination))
        assert destination.read_bytes() == b"previous-engine"
    else:
        build_rust.install_binary(str(source), str(destination))
        assert destination.read_bytes() == b"complete-new-engine"
    assert not list(tmp_path.glob(".sf-install-*"))


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "utf-16"])
def test_xml_attribute_locations_in_built_engine(tmp_path, encoding):
    from rust_engine import sf_engine
    from sf_utils.constants import Constants

    xml = '<root needle="other"\n x="needle"/>'
    path = tmp_path / "location.xml"
    path.write_bytes(xml.encode(encoding))
    matches = sf_engine.search_file(str(path), "needle", Constants.RUST_MODE_XML)
    assert len(matches) == 1
    match = matches[0]
    assert match.line == 2
    assert match.content == "/root/@x\tneedle"
    if encoding == "utf-16":
        assert match.offset is None and match.length is None
    else:
        assert match.offset == path.read_bytes().rindex(b"needle")


@pytest.mark.parametrize("existence_only", [False, True])
def test_malformed_xml_is_skipped_in_app_paths(tmp_path, monkeypatch, existence_only):
    from core import search_engine
    from sf_utils.constants import Constants

    path = tmp_path / "invalid.xml"
    path.write_text('<root><a>needle</a><b>needle</b><c x="needle" x="duplicate"/></root>', encoding="utf-8")
    monkeypatch.setattr(search_engine, "_get_rust_search_limits", lambda: (1, 500000, 20000, 524288000))
    result = search_engine.search_directory_fast(
        [str(tmp_path)], "needle", ["xml"], special_mode=Constants.MODE_XML, existence_only=existence_only,
    )
    assert result["results"] == []
    assert len(result["skipped"]) == 1
    result = search_engine.search_files_list_fast(
        [str(path)], "needle", special_mode=Constants.MODE_XML, existence_only=existence_only,
    )
    assert result["results"] == []
    assert len(result["skipped"]) == 1
