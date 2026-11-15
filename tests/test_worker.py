"""검색 작업자의 전체 결과 상한 경계, 상한 안내, 파일 일부 검색 안내."""


from core.worker import SearchWorker
from sf_utils.app_strings import AppStrings
from sf_utils.constants import Constants
from folder_search_helper import fake_matches


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
                    fake_matches([
                        (1, "needle", None, None),
                        (0, "__SF_TRUNCATED__", None, None),
                    ]),
                )
            ]
        )
        return {"results": [], "skipped": []}

    monkeypatch.setattr("core.search_engine.search_directory_fast", fake_search)
    worker.run()

    assert results_received[0][0] == "limited.txt"
    assert skipped_received[0][0] == "limited.txt"
    from core.skip_reason_formatter import render_skip_reason

    assert AppStrings.SKIP_REASON_FILE_MATCH_LIMIT.split("{}")[0] in render_skip_reason(skipped_received[0][1])
    assert finished_received == [(1, 1, 1)]


