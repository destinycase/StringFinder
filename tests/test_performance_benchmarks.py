"""
성능 벤치마크 테스트 모듈

pytest-benchmark를 사용하여 핵심 기능의 성능을 측정하고 회귀를 감지합니다.

실행 방법:
    pytest tests/test_performance_benchmarks.py --benchmark-only
    pytest tests/test_performance_benchmarks.py --benchmark-compare
"""
import pytest
import tempfile
import os
import sys
import re

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from core.search_engine import search_in_file, detect_encoding_quickly


class TestSearchEnginePerformance:
    """검색 엔진 핵심 기능 성능 벤치마크"""
    
    @pytest.fixture
    def medium_text_file(self):
        """10MB 테스트 파일 생성"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8', suffix='.txt') as f:
            line = "This is a test line with some search keywords and more text.\n"
            for _ in range(200_000):  # 약 10MB
                f.write(line)
            yield f.name
        os.unlink(f.name)
    
    @pytest.fixture
    def large_text_file(self):
        """50MB 테스트 파일 생성"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8', suffix='.txt') as f:
            line = "This is a test line with some search keywords and more text.\n"
            for _ in range(1_000_000):  # 약 50MB
                f.write(line)
            yield f.name
        os.unlink(f.name)
    
    def test_search_medium_file_performance(self, benchmark, medium_text_file):
        """10MB 파일 검색 성능 측정"""
        file_size = os.path.getsize(medium_text_file)
        
        result = benchmark(
            search_in_file,
            medium_text_file,
            "keywords",
            file_size=file_size
        )
        
        # 결과 검증
        assert result is not None
        assert result[1] > 0  # count > 0
        
        # 성능 기준: 10MB 파일은 1초 이내 완료
        assert benchmark.stats['mean'] < 1.0
    
    def test_search_large_file_performance(self, benchmark, large_text_file):
        """50MB 파일 검색 성능 측정"""
        file_size = os.path.getsize(large_text_file)
        
        result = benchmark(
            search_in_file,
            large_text_file,
            "keywords",
            file_size=file_size
        )
        
        # 결과 검증
        assert result is not None
        
        # 성능 기준: 50MB 파일은 5초 이내 완료
        assert benchmark.stats['mean'] < 5.0
    
    def test_encoding_detection_performance(self, benchmark):
        """인코딩 감지 성능 측정"""
        test_data = "한글 테스트 데이터 UTF-8 인코딩".encode('utf-8')
        
        result = benchmark(detect_encoding_quickly, test_data)
        
        assert result in ['utf-8', 'cp949', 'utf-16le']
        # 인코딩 감지는 1ms 이내 완료
        assert benchmark.stats['mean'] < 0.001


class TestRegexCachingPerformance:
    """정규식 캐싱 최적화 효과 측정"""
    
    def test_regex_compilation_overhead(self, benchmark):
        """정규식 컴파일 오버헤드 측정"""
        search_string = "complex.*pattern.*with.*wildcards"
        
        def compile_pattern():
            return re.compile(search_string.encode('utf-8'), re.IGNORECASE)
        
        result = benchmark(compile_pattern)
        
        assert result is not None
        # 컴파일은 0.1ms 이내
        assert benchmark.stats['mean'] < 0.0001
    
    def test_regex_caching_benefit(self, benchmark):
        """캐싱된 패턴 재사용 vs 매번 컴파일 비교"""
        search_string = "test"
        test_data = b"This is a test string with test keywords"
        
        # 캐싱된 패턴 사용
        compiled_pattern = re.compile(search_string.encode('utf-8'), re.IGNORECASE)
        
        def use_cached_pattern():
            return list(compiled_pattern.finditer(test_data))
        
        result = benchmark(use_cached_pattern)
        
        assert len(result) == 2  # "test" 2번 발견
        # 캐싱된 패턴 사용은 매우 빠름 (0.01ms 이내)
        assert benchmark.stats['mean'] < 0.00001


class TestUIPerformance:
    """UI 관련 성능 벤치마크"""
    
    @pytest.fixture
    def large_preview_file(self):
        """미리보기 테스트용 대용량 파일"""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, encoding='utf-8', suffix='.txt') as f:
            for i in range(1_000_000):
                f.write(f"Line {i}: Some content here\n")
            yield f.name
        os.unlink(f.name)
    
    def test_lazy_loading_line_extraction(self, benchmark, large_preview_file):
        """Lazy Loading 라인 추출 성능 측정"""
        target_line = 500_000
        context_range = 5
        
        def extract_lines():
            """미리보기 로직 시뮬레이션"""
            start_target = max(1, target_line - context_range)
            end_target = target_line + context_range
            
            preview_lines = []
            current_idx = 0
            
            with open(large_preview_file, 'r', encoding='utf-8') as f:
                for line in f:
                    current_idx += 1
                    if current_idx < start_target:
                        continue
                    if current_idx > end_target:
                        break
                    preview_lines.append((current_idx, line.rstrip()))
            
            return preview_lines
        
        result = benchmark(extract_lines)
        
        # 결과 검증
        assert len(result) == 11  # context_range * 2 + 1
        assert result[5][0] == target_line  # 중간 라인이 타겟
        
        # 성능 기준: 100만 라인 파일에서 특정 라인 추출은 0.5초 이내
        assert benchmark.stats['mean'] < 0.5


if __name__ == "__main__":
    pytest.main([__file__, "--benchmark-only", "-v"])
