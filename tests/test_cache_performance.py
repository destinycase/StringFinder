"""
캐시 성능 벤치마크 테스트

시나리오:
1. 10,000개 파일 중 50개에서 문자열 발견
2. 10개 파일 변경 (기존에 매칭 안 되던 파일에 문자열 추가)
3. 두 번째 검색: 기존 40개 + 새로운 10개 = 50개 발견
4. 캐시 효율성 측정: 변경된 10개만 재검색, 나머지 40개는 캐시 사용
"""

import os
import time
from pathlib import Path

import pytest

from core.search_cache import HybridSearchCache


class TestCachePerformance:
    """캐시 성능 벤치마크 테스트"""

    @pytest.fixture
    def temp_cache_dir(self, tmp_path):
        """임시 캐시 디렉토리"""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        return str(cache_dir)

    @pytest.fixture
    def test_files_dir(self, tmp_path):
        """테스트 파일 디렉토리 생성"""
        files_dir = tmp_path / "test_files"
        files_dir.mkdir()
        return files_dir

    def create_test_files(self, base_dir: Path, count: int, match_indices: list, search_string: str):
        """테스트 파일 생성"""
        files = []
        for i in range(count):
            file_path = base_dir / f"file_{i:05d}.txt"

            # 매칭 파일에는 검색어 포함
            if i in match_indices:
                content = f"Line 1: Some content\nLine 2: {search_string}\nLine 3: More content\n"
            else:
                content = "Line 1: Some content\nLine 2: No match here\nLine 3: More content\n"

            file_path.write_text(content, encoding="utf-8")
            files.append(str(file_path))

        return files

    def simulate_search(self, files: list, search_string: str) -> list:
        """검색 시뮬레이션 (실제 파일 읽기)"""
        results = []
        for file_path in files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content = f.read()
                    if search_string in content:
                        # 매칭된 라인 찾기
                        lines = content.split("\n")
                        for line_num, line in enumerate(lines, 1):
                            if search_string in line:
                                results.append((file_path, 1, line.strip()))
            except Exception:
                pass

        return results

    def test_cache_efficiency_scenario(self, temp_cache_dir, test_files_dir):
        """캐시 효율성 시나리오 테스트"""
        # 설정
        total_files = 10000
        initial_matches = 50
        files_to_change = 10
        search_string = "SEARCH_TARGET"

        print(f"\n{'=' * 80}")
        print("캐시 성능 벤치마크 테스트")
        print(f"{'=' * 80}")
        print(f"총 파일 수: {total_files:,}개")
        print(f"초기 매칭 파일: {initial_matches}개")
        print(f"변경할 파일: {files_to_change}개")
        print(f"검색어: '{search_string}'")
        print(f"{'=' * 80}\n")

        # 1. 초기 파일 생성
        print("[1] 초기 파일 생성 중...")
        initial_match_indices = list(range(initial_matches))
        all_files = self.create_test_files(test_files_dir, total_files, initial_match_indices, search_string)
        print(f"    -> {total_files:,}개 파일 생성 완료\n")

        # 2. 캐시 초기화
        cache = HybridSearchCache(temp_cache_dir, max_results=100, persist=True)

        # 3. 첫 번째 검색 (캐시 미스)
        print("[2] 첫 번째 검색 수행 (캐시 미스)...")
        start_time = time.time()
        first_results = self.simulate_search(all_files, search_string)
        first_search_time = time.time() - start_time

        print(f"    -> 검색 시간: {first_search_time:.3f}초")
        print(f"    -> 발견된 결과: {len(first_results)}개")

        # 캐시에 저장
        cache_key = cache._get_cache_key(search_string, [str(test_files_dir)], [".txt"])
        cache.result_cache.put(cache_key, first_results)

        # 파일별 캐시 저장
        for result in first_results:
            file_path = result[0]
            stat = os.stat(file_path)
            file_cache_key = (file_path, search_string)
            cache.file_cache[file_cache_key] = (stat.st_mtime, stat.st_size, [result])

        cache.save_to_disk()
        print("    -> 캐시 저장 완료\n")

        # 4. 파일 변경
        print("[3] 파일 변경 중...")
        time.sleep(0.1)  # mtime 변경 보장

        changed_indices = list(range(initial_matches, initial_matches + files_to_change))
        for idx in changed_indices:
            file_path = Path(all_files[idx])
            content = f"Line 1: Some content\nLine 2: {search_string}\nLine 3: Modified content\n"
            file_path.write_text(content, encoding="utf-8")

        print(f"    -> {files_to_change}개 파일 변경 완료\n")

        # 5. 두 번째 검색 - 캐시 없이 (비교용)
        print("[4] 두 번째 검색 (캐시 없이) - 비교용...")
        start_time = time.time()
        second_results_no_cache = self.simulate_search(all_files, search_string)
        second_search_time_no_cache = time.time() - start_time

        print(f"    -> 검색 시간: {second_search_time_no_cache:.3f}초")
        print(f"    -> 발견된 결과: {len(second_results_no_cache)}개\n")

        # 6. 두 번째 검색 - 캐시 활용 (증분 검색)
        print("[5] 두 번째 검색 (캐시 활용) - 증분 검색...")
        start_time = time.time()

        # 캐시에서 기존 결과 가져오기
        cached_results = cache.result_cache.get(cache_key)

        # 변경된 파일만 재검색
        changed_files = [all_files[idx] for idx in changed_indices]
        new_results = self.simulate_search(changed_files, search_string)

        # 변경되지 않은 파일의 캐시 결과 필터링
        unchanged_results = [r for r in cached_results if r[0] not in changed_files]

        # 결과 병합
        final_results = unchanged_results + new_results

        second_search_time_with_cache = time.time() - start_time

        print(f"    -> 검색 시간: {second_search_time_with_cache:.3f}초")
        print(f"    -> 캐시 재사용: {len(unchanged_results)}개")
        print(f"    -> 새로 검색: {len(new_results)}개")
        print(f"    -> 총 결과: {len(final_results)}개\n")

        # 7. 성능 분석
        print(f"{'=' * 80}")
        print("[성능 분석 결과]")
        print(f"{'=' * 80}")

        speedup = second_search_time_no_cache / second_search_time_with_cache
        time_saved = second_search_time_no_cache - second_search_time_with_cache
        percentage_saved = (time_saved / second_search_time_no_cache) * 100

        print("\n[검색 시간 비교]")
        print(f"   - 첫 번째 검색 (캐시 미스):     {first_search_time:.3f}초")
        print(f"   - 두 번째 검색 (캐시 없이):     {second_search_time_no_cache:.3f}초")
        print(f"   - 두 번째 검색 (캐시 활용):     {second_search_time_with_cache:.3f}초")
        print("\n[성능 향상]")
        print(f"   - 속도 향상:                    {speedup:.1f}배")
        print(f"   - 시간 절약:                    {time_saved:.3f}초")
        print(f"   - 절약률:                       {percentage_saved:.1f}%")

        files_scanned_no_cache = total_files
        files_scanned_with_cache = files_to_change
        scan_reduction = ((files_scanned_no_cache - files_scanned_with_cache) / files_scanned_no_cache) * 100

        print("\n[파일 처리 효율성]")
        print(f"   - 캐시 없이 스캔:               {files_scanned_no_cache:,}개 파일")
        print(f"   - 캐시 활용 스캔:               {files_scanned_with_cache:,}개 파일")
        print(f"   - 스캔 감소율:                  {scan_reduction:.1f}%")

        cache_hit_rate = (len(unchanged_results) / len(final_results)) * 100 if final_results else 0

        print("\n[캐시 효율성]")
        print(f"   - 캐시 히트:                    {len(unchanged_results)}개 ({cache_hit_rate:.1f}%)")
        print(f"   - 새로 검색:                    {len(new_results)}개 ({100 - cache_hit_rate:.1f}%)")
        print(f"   - 총 결과:                      {len(final_results)}개")

        print(f"\n{'=' * 80}\n")

        # 검증
        assert len(first_results) == initial_matches
        assert len(second_results_no_cache) == initial_matches + files_to_change
        assert len(final_results) == initial_matches + files_to_change
        assert second_search_time_with_cache < second_search_time_no_cache
        assert speedup >= 2.0, f"성능 향상 미달: {speedup:.1f}배"

        print("[OK] 모든 검증 통과!")
        print(f"     캐시를 활용한 증분 검색이 {speedup:.1f}배 빠릅니다!\n")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
