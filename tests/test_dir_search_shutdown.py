"""폴더 검색 종료 반복 시험(8.1.6, 엔진 1.0.2에서 search_dir를 단계별 함수로 나눈 뒤).

결과 콜백이 있을 때·없을 때, 검색 도중 중지, 콜백 오류를 수백 번 되풀이해 검색이 멈추지 않는지,
마지막 결과 묶음이 빠지지 않는지 봅니다. 오래 걸려 stress 표시로 평소 시험에서는 빠집니다.
"""

import threading

import pytest

from core import search_engine as se

pytestmark = pytest.mark.stress

ROUNDS = 150
FILES = 40
HANG_SECONDS = 20


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("dir_search_shutdown")
    for i in range(FILES):
        (root / f"f{i:03}.txt").write_text(f"line\nneedle {i}\nneedle again\n", encoding="utf-8")
    (root / "other.txt").write_text("nothing here\n", encoding="utf-8")
    return str(root)


def _run(**kwargs):
    """검색을 따로 돌리고, HANG_SECONDS 안에 끝나지 않으면 멈춤으로 실패합니다."""
    box = {}

    def target():
        try:
            box["value"] = se.sf_engine.search_dir(**kwargs)
        except BaseException as error:  # 시험이 오류 종류를 확인합니다.
            box["error"] = error

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(HANG_SECONDS)
    assert not thread.is_alive(), "search_dir did not finish"
    return box


def test_results_callback_delivers_every_file_every_time(corpus):
    for round_index in range(ROUNDS):
        delivered = []
        box = _run(root_paths=[corpus], pattern="needle", results_callback=delivered.extend,
                   batch_size=1 + round_index % 7, _flush_ms=1 + round_index % 5)
        assert "error" not in box, box
        found, skipped = box["value"]
        assert found == [] and skipped == []  # 콜백으로 받으면 돌려주는 목록은 비어 있습니다.
        assert len(delivered) == FILES, round_index  # 마지막 묶음까지 빠짐없이


def test_without_callback_returns_every_file_every_time(corpus):
    for _ in range(ROUNDS):
        box = _run(root_paths=[corpus], pattern="needle")
        assert "error" not in box, box
        found, skipped = box["value"]
        assert len(found) == FILES and skipped == []


BIG_FILES = 20_000
BIG_DIRS = 200
STOP_ROUNDS = 60
STOP_AFTER = 50


@pytest.fixture(scope="module")
def big_corpus(tmp_path_factory):
    """도중 중지 시험 전용: 검색이 감시 간격(0.1초)보다 오래 걸릴 만큼 큰 폴더."""
    root = tmp_path_factory.mktemp("dir_search_stop")
    for d in range(BIG_DIRS):
        (root / f"d{d:03}").mkdir()
    for i in range(BIG_FILES):
        (root / f"d{i % BIG_DIRS:03}" / f"f{i:05}.txt").write_text(f"x\nneedle {i}\n", encoding="utf-8")
    return str(root)


def test_stop_during_search_finishes_and_keeps_partial_results(big_corpus):
    stopped_midway = 0
    for round_index in range(STOP_ROUNDS):
        stop = threading.Event()
        delivered = []

        def on_results(batch):
            delivered.extend(batch)
            if len(delivered) >= STOP_AFTER:
                stop.set()

        box = _run(root_paths=[big_corpus], pattern="needle", stop_event=stop, results_callback=on_results,
                   batch_size=1 + round_index % 50, _flush_ms=1 + round_index % 20)
        assert "error" not in box, box
        assert 1 <= len(delivered) <= BIG_FILES
        if len(delivered) < BIG_FILES:
            stopped_midway += 1
    # 장비 속도에 흔들리지 않도록 매 회차가 아니라 대부분의 회차에서 도중에 멈췄는지 봅니다.
    assert stopped_midway >= STOP_ROUNDS * 0.8, stopped_midway


def test_failing_results_callback_stops_and_reports(corpus):
    for _ in range(ROUNDS):
        def broken(_batch):
            raise ValueError("ui gone")

        box = _run(root_paths=[corpus], pattern="needle", results_callback=broken, batch_size=1)
        assert isinstance(box.get("error"), RuntimeError), box
        assert "results callback failed" in str(box["error"])


def test_failing_progress_callback_stops_and_reports(corpus):
    for _ in range(ROUNDS // 3):
        def broken(_count):
            raise ValueError("progress gone")

        box = _run(root_paths=[corpus], pattern="needle", progress_callback=broken)
        if "error" in box:  # 검색이 감시 간격(0.1초)보다 빨리 끝나면 콜백이 불리지 않을 수 있습니다.
            assert "progress callback failed" in str(box["error"])
        else:
            assert len(box["value"][0]) == FILES
