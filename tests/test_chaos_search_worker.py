"""
[test_chaos_search_worker.py]

???뚯뒪?몃뒗 寃???뚯빱(SearchWorker)??洹뱁븳 ?듭젣 ?곹솴?먯꽌???덉젙?깆쓣 寃利앺빀?덈떎.

- ?뚯뒪??紐⑹쟻:
  1. 寃???꾨줈?몄뒪 ?(ProcessPool) ?ъ궗??諛??댁젣 ??醫鍮??꾨줈?몄뒪 諛쒖깮 諛⑹?.
  2. ???寃???꾩쨷 媛뺤젣 以묒? ??濡쒖쭅??利됯컖?곸씤 ?묐떟??Responsiveness) ?뺤씤.

- 二쇱슂 寃利??ы빆:
  1. ?섏떗 ?뚯쓽 寃???뚯빱 ?앹꽦 諛?媛뺤젣 醫낅즺 諛섎났 ??由ъ냼???꾩쟻 ?щ?.
  2. ?뚯빱 醫낅즺 ???쒓렇???щ’ ?곌껐???덉쟾?섍쾶 ?댁젣?섎뒗吏 ?뺤씤.
"""

import random
import threading
import time

import pytest

from core.worker import SearchWorker


@pytest.mark.chaos
def test_chaos_rapid_search_worker_start_stop(tmp_path):
    root = tmp_path / "search_worker_chaos"
    root.mkdir()

    for i in range(120):
        (root / f"f_{i:04d}.txt").write_text("needle data\n", encoding="utf-8")

    errors: list[str] = []
    cycles = 20

    for _ in range(cycles):
        worker = SearchWorker(
            {
                "search_paths": [str(root)],
                "search_string": "needle",
                "extensions": ["txt"],
            }
        )
        worker.signals.error.connect(lambda msg, sink=errors: sink.append(msg))

        thread = threading.Thread(target=worker.run, daemon=True)
        thread.start()
        time.sleep(random.uniform(0.002, 0.02))
        worker.stop()
        thread.join(timeout=8.0)

        assert not thread.is_alive()

    assert not errors
