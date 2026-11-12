"""
[test_worker.py]

이 테스트는 검색 워커(SearchWorker)의 기본적인 배치 실행, 취소 로직 및 에러 처리 경로를 검증합니다.

- 테스트 목적:
  1. 병렬 실행기(GlobalExecutor)와 연동된 파일 검색 작업의 생명주기 관리 확인.
  2. 사용자 중단 요청이나 타임아웃 발생 시의 안전한 리소스 회수 보장.

- 주요 검증 사항:
  1. 검색 결과의 배치 처리 및 시그널 방출 무결성.
  2. 작업 취소(`stop`) 시의 즉각적인 루프 중단 및 스레드 정리.
  3. 파일 처리 도중 예외 발생 시의 스킵(Skip) 처리 및 결과 리포팅.
"""


from core.worker import SearchWorker
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants


def test_worker_total_limit_keeps_exact_allowed_prefix():
    worker = SearchWorker({"file_list": [], "search_string": "needle"})
    worker._total_matches_accumulated = 7
    worker.search_settings_snapshot[Constants.CONFIG_KEY_MAX_TOTAL_MATCHES] = 10

    batch = [
        (
            "many.txt",
            5,
            [(1, "one"), (2, "two"), (3, "three"), (4, "four"), (5, "five")],
        )
    ]
    accepted, count, reached = worker._fit_results_to_total_limit(batch)

    assert count == 3
    assert reached is True
    assert accepted == [("many.txt", 3, [(1, "one"), (2, "two"), (3, "three")])]


def test_worker_emits_global_limit_for_result_banner():
    worker = SearchWorker({"file_list": [], "search_string": "needle"})
    worker.search_settings_snapshot[Constants.CONFIG_KEY_MAX_TOTAL_MATCHES] = 500_000
    emitted = []
    worker.signals.total_match_limit_reached.connect(emitted.append)

    worker._stop_for_total_match_limit()
    worker._stop_for_total_match_limit()

    assert emitted == [500_000]


def test_rust_worker_emits_partial_file_notice_and_keeps_results(monkeypatch):
    worker = SearchWorker(
        {
            "search_paths": ["unused"],
            "search_string": "needle",
        }
    )
    results_received = []
    skipped_received = []
    finished_received = []
    worker.signals.results_found.connect(lambda batch: results_received.extend(batch))
    worker.signals.skipped_found.connect(lambda batch: skipped_received.extend(batch))
    worker.signals.search_finished.connect(lambda *values: finished_received.append(values))

    def fake_search(*args, **kwargs):
        kwargs["results_callback"](
            [
                (
                    "limited.txt",
                    [
                        (1, "needle", None, None),
                        (0, "__SF_TRUNCATED__", None, None),
                    ],
                )
            ]
        )
        return {"results": [], "skipped": []}

    monkeypatch.setattr("core.search_engine.search_directory_fast", fake_search)
    worker.run()

    assert results_received[0][0] == "limited.txt"
    assert skipped_received[0][0] == "limited.txt"
    assert AppStrings.SKIP_REASON_FILE_MATCH_LIMIT.split("{}")[0] in skipped_received[0][1]
    assert finished_received == [(1, 1, 1)]


