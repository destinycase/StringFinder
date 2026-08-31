"""
[test_streaming_pipeline.py]

이 테스트는 검색 엔진과 UI 간의 실시간 결과 전송(Streaming) 및 데이터 누적 파이프라인을 검증합니다.

- 테스트 목적:
  1. 대량의 검색 결과가 실시간으로 UI 모델에 반영될 때의 응답성 및 안정성 확보.
  2. 데이터 배치(Batch) 처리 시의 누합 및 통계 계산 정확도 확인.

- 주요 검증 사항:
  1. `SearchWorker`의 스트림 콜백을 통한 결과 누적 무결성.
  2. `SearchResultModel`의 배치 추가 시 레이아웃 업데이트 정확도.
  3. 첫 결과 수신 시의 UI 상태 전환(Onboarding -> Result View).
"""

from unittest.mock import patch
from core.worker import SearchWorker
from ui.models import SearchResultModel
from ui.result_view import ResultView
from sf_utils.constants import Constants


def test_worker_streaming_accumulation(qtbot):
    """
    SearchWorker가 스트림 콜백을 통해 결과를 받을 때
    all_results에 올바르게 누적하고 최종 통계를 전송하는지 확인합니다.
    """
    params = {
        Constants.PAYLOAD_SEARCH_PATHS: ["/dummy/path"],
        Constants.PAYLOAD_SEARCH_STRING: "test",
        "use_complex_search": False,
    }
    worker = SearchWorker(params)

    # 시그널 수집용
    results_emitted = []
    finished_data = []
    worker.signals.results_found.connect(lambda b: results_emitted.append(b))
    worker.signals.search_finished.connect(lambda f, m, s: finished_data.append((f, m, s)))

    # Rust 엔진 함수 및 가용성 모킹
    with (
        patch("core.search_engine.search_directory_fast") as mock_search,
        patch("core.search_engine.HAS_RUST_ENGINE", True),
    ):

        def side_effect(*args, **kwargs):
            results_cb = kwargs.get("results_callback")
            if results_cb:
                # 배치 1번 전송
                results_cb([("/path/file1.txt", [(1, "match1", 10, 5)])])
                # 배치 2번 전송
                results_cb([("/path/file2.txt", [(2, "match2", 20, 5), (3, "match3", 30, 5)])])
            return {Constants.PAYLOAD_RESULTS: [], Constants.PAYLOAD_SKIPPED: []}

        mock_search.side_effect = side_effect
        worker.run()

    # 1. 시그널이 두 번 발생했는지 확인
    assert len(results_emitted) == 2
    # 2. all_results에 누적되었는지 확인
    assert len(worker.all_results) == 2
    # 3. 최종 통계가 누적된 값(파일 2개, 매치 3개)으로 전송되었는지 확인
    assert finished_data == [(2, 3, 0)]


def test_search_result_model_batch_addition():
    """SearchResultModel.add_results가 배치 추가 시 레이아웃 변경을 처리하는지 확인합니다."""
    model = SearchResultModel()

    # 초기 상태
    assert model.rowCount() == 0

    # 배치 추가 (SearchResultModel 형식: [count, name, folder, path, matches])
    batch = [[1, "file1.txt", "folder", "/path/file1.txt", []], [2, "file2.txt", "folder", "/path/file2.txt", []]]

    model.add_results(batch)

    # 행 수가 정확히 반영되었는지 확인 (현재 페이지 기준)
    assert model.rowCount() == 2
    assert len(model.get_all_results()) == 2


def test_search_result_model_sort_keeps_filtered_rows_in_sync(qtbot):
    model = SearchResultModel()
    model.add_results([
        [1, "z.txt", "folder", "/z/z.txt", [(1, "z")]],
        [2, "a.txt", "folder", "/a/a.txt", [(1, "a")]],
    ])

    model.sort(1)
    qtbot.waitUntil(lambda: not model._is_sorting, timeout=2000)

    assert [item[1] for item in model._result_buffer] == ["a.txt", "z.txt"]
    assert [item[1] for item in model._filtered_buffer] == ["a.txt", "z.txt"]
    assert [model.data(model.index(i, 1)) for i in range(model.rowCount())] == ["a.txt", "z.txt"]


def test_result_view_visibility_toggle(qtbot, mock_config_manager):
    """ResultView가 첫 결과 수신 시 가시성을 전환하는지 확인합니다."""
    view = ResultView(None, mock_config_manager)
    view.show()
    qtbot.addWidget(view)

    # 초기 상태: 온보딩 라벨 표시, 결과 테이블 숨김
    assert view.empty_label.isVisible()
    assert not view.result_splitter.isVisible()

    # 결과 추가 (SearchResultModel 형식)
    batch = [[1, "filename", "folder_path", "/path/file1.txt", [(1, "match", 0, 5)]]]
    view.add_results(batch)

    # 결과가 들어온 후: 온보딩 숨김, 결과 테이블 표시
    assert not view.empty_label.isVisible()
    assert view.result_splitter.isVisible()
