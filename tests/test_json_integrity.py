"""
[test_json_integrity.py]

이 테스트는 JSON 및 Archive 파일 검색 시의 데이터 추출 무결성을 검증합니다.

- 테스트 목적:
  1. JSON 키(Key)가 아닌 값(Value) 영역에서만 정확한 검색이 이루어지는지 확인.
  2. 손상된(Malformed) JSON 파일에 대한 방어적 예외 처리 로직 검증.
  3. UTF-16 및 BOM 환경에서의 JSON 파싱 안정성 확보.

- 주요 검증 사항:
  1. JSON/Archive Key 매칭 배제 여부.
  2. 대용량 깨진 JSON 처리 시 `STATUS_SKIPPED` 반환 여부.
  3. UTF-16 LE BOM JSON 검색 성공 여부.
"""

import json
import pytest
from core.search_engine import search_in_json_special, search_in_archive_special
from core import search_engine
from sf_utils.constants import Constants
from sf_utils.app_strings import AppStrings

@pytest.fixture(autouse=True)
def disable_rust():
    # Rust 엔진 비활성화하여 Python 폴백 경로 강제 테스트
    old_has_rust = search_engine.HAS_RUST_ENGINE
    search_engine.HAS_RUST_ENGINE = False
    yield
    search_engine.HAS_RUST_ENGINE = old_has_rust

def test_json_key_only_match_integrity(tmp_path):
    # [1] JSON Key 검색 배제 (Value만 검색해야 함)
    file_path = tmp_path / "test_key.json"
    content = {"needle_key": "ignore_value", "other": "y"}
    file_path.write_text(json.dumps(content), encoding="utf-8")
    
    # existence_only=True일 때도 Key만 매칭되면 None이어야 함
    result = search_in_json_special(str(file_path), "needle", existence_only=True)
    assert result is None, "Should not match JSON key"

def test_archive_key_only_match_integrity(tmp_path):
    # [2] Archive Key 검색 배제
    file_path = tmp_path / "test.archive"
    content = {
        "Subnamespaces": [
            {
                "Namespace": "NS",
                "Children": [
                    {"Key": "needle_key", "Source": {"Text": "safe"}, "Translation": {"Text": "safe"}}
                ]
            }
        ]
    }
    file_path.write_text(json.dumps(content), encoding="utf-8")
    
    result = search_in_archive_special(str(file_path), "needle", existence_only=True)
    assert result is None, "Should not match Archive key"

def test_malformed_json_integrity(tmp_path):
    # [3] 깨진 JSON 처리 (조기 성공 반환으로 파싱 에러 누락 방지)
    file_path = tmp_path / "broken.json"
    file_path.write_text('{"a": "needle", ', encoding="utf-8") # 콤마로 끝나고 닫히지 않음
    
    result = search_in_json_special(str(file_path), "needle", existence_only=True)
    assert result is not None
    assert result[0] == Constants.STATUS_SKIPPED, "Malformed JSON should be marked as SKIPPED"

def test_exact_match_integrity(tmp_path):
    # [4] Exact Match 정밀도 (부분 일치 배제)
    file_path = tmp_path / "exact.json"
    content = {"a": "foobar"}
    file_path.write_text(json.dumps(content), encoding="utf-8")
    
    # exact_match=True, search="foo" -> foobar는 매치되면 안 됨
    result = search_in_json_special(str(file_path), "foo", exact_match=True, existence_only=True)
    assert result is None, "Exact match should not trigger on partial overlap"
    
    # exact_match=True, search="foobar" -> 성공해야 함
    result = search_in_json_special(str(file_path), "foobar", exact_match=True, existence_only=True)
    assert result is not None
    assert result[1] == 1

def test_malformed_large_json_existence_only_integrity(tmp_path):
    # [v4.57.0] 대용량 Malformed JSON이 existence_only에서 None으로 씹히는지 확인
    # 131KB(Fast Filter 기준점)보다 큰 파일 생성
    file_path = tmp_path / "large_broken.json"
    content = '{"data": "no_match", ' + (" " * 140000) # 닫히지 않은 JSON
    file_path.write_text(content, encoding="utf-8")
    
    # existence_only=True일 때 필터는 False(미매치)를 반환하겠지만, loads에서 SKIPPED가 터져야 함
    result = search_in_json_special(str(file_path), "needle", existence_only=True)
    assert result is not None
    assert result[0] == Constants.STATUS_SKIPPED
    assert AppStrings.ERROR_JSON_PARSE in result[1]

def test_utf16_bom_json_search(tmp_path):
    # [v4.57.0] UTF-16 BOM이 포함된 JSON 파일 검색 성공 여부 확인
    file_path = tmp_path / "utf16_bom.json"
    content = {"message": "안녕하세요", "status": "ok"}
    
    # UTF-16 LE with BOM
    with open(file_path, "wb") as f:
        f.write(b"\xff\xfe")
        f.write(json.dumps(content).encode("utf-16-le"))
        
    result = search_in_json_special(str(file_path), "안녕하세요")
    assert result is not None
    assert result[1] == 1
    assert "message" in result[2][0].content
