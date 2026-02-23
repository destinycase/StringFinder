"""
[test_cache_integrity_v2.py]

이 테스트는 검색 캐시 시스템의 지능형 갱신 및 데이터 무결성을 검증합니다.

- 테스트 목적:
  1. 파일 시스템 변경(추가, 삭제, 수정) 시 캐시가 이를 정확히 감지하여 갱신하는지 확인.
  2. 재귀적 파일 시그너처(Signature)가 하위 디렉토리의 변경까지 포착하는지 검증.

- 주요 검증 사항:
  1. 기존 검색 결과에 파일이 추가되었을 때 전체 결과 업데이트 여부.
  2. 파일 내용 수정(Timestamp/Size 변경) 시 캐시 대조 실패 및 재검색 트리거 확인.
  3. 파일 삭제 시 캐시 히트에서 해당 결과가 제거되는지 확인.
"""

import os
import sys
import tempfile
import time
import unittest

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.worker import SearchWorker


class TestCacheIntegrityV2(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pass  # 테스트에서 로거 초기화는 필요 시에만 수행

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = self.temp_dir.name

    def tearDown(self):
        self.temp_dir.cleanup()

    def run_worker(self, paths, query):
        worker = SearchWorker(
            {"search_paths": paths, "search_string": query, "extensions": ["txt"], "cache_enabled": True}
        )

        found_data = []
        finished_found = -1

        def on_results(results):
            found_data.extend(results)

        def on_finished(found, skipped):
            nonlocal finished_found
            finished_found = found

        worker.signals.results_found.connect(on_results)
        worker.signals.search_finished.connect(on_finished)

        worker.run()
        return finished_found, found_data

    def test_scenario_a_existing_results_plus_addition(self):
        """test_scenario_a_existing_results_plus_addition 함수."""
        test_dir = os.path.join(self.root, "scenario_a")
        os.makedirs(test_dir)

        file1 = os.path.join(test_dir, "file1.txt")
        with open(file1, "w", encoding="utf-8") as f:
            f.write("target_keyword")

        # 1차 검색: 1건 발견 및 캐시 생성
        found1, _ = self.run_worker([test_dir], "target_keyword")
        self.assertEqual(found1, 1)

        # 2차 작업: 동일 키워드 포함 신규 파일 추가
        time.sleep(0.1)
        file2 = os.path.join(test_dir, "file2.txt")
        with open(file2, "w", encoding="utf-8") as f:
            f.write("target_keyword")

        # 2차 검색: 캐시 히트를 시도하지만 구조 변경을 감지하여 2건을 찾아야 함
        found2, results = self.run_worker([test_dir], "target_keyword")
        self.assertEqual(found2, 2, "신규 파일 추가가 감지되지 않아 캐시 누락 발생")

        paths = [r[0] for r in results]
        self.assertIn(file1, paths)
        self.assertIn(file2, paths)

    def test_scenario_b_zero_result_plus_deep_change(self):
        """test_scenario_b_zero_result_plus_deep_change 함수."""
        test_dir = os.path.join(self.root, "scenario_b")
        deep_dir = os.path.join(test_dir, "level1", "level2", "level3")
        os.makedirs(deep_dir)

        deep_file = os.path.join(deep_dir, "deep.txt")
        with open(deep_file, "w", encoding="utf-8") as f:
            f.write("empty")

        # 1차 검색: 0건 발견 및 캐시 생성
        found1, _ = self.run_worker([test_dir], "secret")
        self.assertEqual(found1, 0)

        # 2차 작업: 깊은 파일 내용 수정 (키워드 추가)
        time.sleep(0.1)
        with open(deep_file, "w", encoding="utf-8") as f:
            f.write("secret keyword")

        # 2차 검색: 재귀적 시그너처가 깊은 경로의 변경을 감지하여 1건을 찾아야 함
        found2, _ = self.run_worker([test_dir], "secret")
        self.assertEqual(found2, 1, "깊은 경로의 파일 내용 변경이 감지되지 않음")

    def test_scenario_c_file_deletion(self):
        """test_scenario_c_file_deletion 함수."""
        test_dir = os.path.join(self.root, "scenario_c")
        os.makedirs(test_dir)

        file1 = os.path.join(test_dir, "match1.txt")
        file2 = os.path.join(test_dir, "match2.txt")
        for p in [file1, file2]:
            with open(p, "w", encoding="utf-8") as f:
                f.write("findme")

        # 1차 검색: 2건 캐시
        found1, _ = self.run_worker([test_dir], "findme")
        self.assertEqual(found1, 2)

        # 2차 작업: 파일 1개 삭제
        time.sleep(0.1)
        os.remove(file1)

        # 2차 검색: 구조 변경 감지로 1건만 반환되어야 함
        found2, _ = self.run_worker([test_dir], "findme")
        self.assertEqual(found2, 1, "파일 삭제가 캐시에 반영되지 않음")


if __name__ == "__main__":
    unittest.main()
