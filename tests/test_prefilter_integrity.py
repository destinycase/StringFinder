import os
import json
import unittest
from core.search_engine import _fast_existence_check, search_in_json_special, search_in_archive_special

class TestPrefilterIntegrity(unittest.TestCase):
    def setUp(self):
        self.test_json = "test_corrupted.json"
        self.test_archive = "test_corrupted.archive"
        
    def tearDown(self):
        for f in [self.test_json, self.test_archive]:
            if os.path.exists(f):
                os.remove(f)

    def test_utf16_prefilter_hit(self):
        """UTF-16LE 대용량 파일에서 pre-filter가 정확히 매치를 찾는지 확인"""
        self.test_file = "test_utf16_large.json"
        target_str = "한글매치데이터"
        data = {"key": target_str, "padding": "x" * 1024 * 1024} # 1MB+
        
        try:
            # UTF-16LE로 저장
            with open(self.test_file, "wb") as f:
                f.write(json.dumps(data, ensure_ascii=False).encode("utf-16-le"))
                
            is_hit = _fast_existence_check(self.test_file, target_str)
            self.assertTrue(is_hit, "UTF-16LE 대용량 파일에서 pre-filter 매치 실패")
            
            res = search_in_json_special(self.test_file, target_str, existence_only=True)
            self.assertIsNotNone(res, "UTF-16LE 검색 결과 누락")
        finally:
            if os.path.exists(self.test_file):
                os.remove(self.test_file)

    def test_large_corrupted_file_integrity(self):
        """6MB 손상된 파일(Invalid Encoding)에서 NoneType 에러 없이 SKIPPED 반환되는지 확인 [v4.63.10]"""
        # 6MB의 0xFF (Invalid UTF-8/Any)
        corrupted_data = b"\xff" * 6 * 1024 * 1024
        
        for f_path in [self.test_json, self.test_archive]:
            with open(f_path, "wb") as f:
                f.write(corrupted_data)
            
            # JSON 경로 테스트
            if f_path.endswith(".json"):
                res = search_in_json_special(f_path, "any", existence_only=True)
            else:
                res = search_in_archive_special(f_path, "any", existence_only=True)
                
            self.assertIsInstance(res, tuple, f"{f_path}에서 결과가 Tuple(SKIPPED)이 아님")
            self.assertEqual(res[0], "SKIPPED", f"{f_path} 무결성 체크 실패 보고 누락")
            self.assertNotIn("NoneType", str(res[1]), f"{f_path}에서 NoneType 참조 오류 발생")

    def test_mmap_json_malformed_integrity(self):
        """디코딩은 성공하지만 JSON 파싱이 실패하는 6MB 파일에서 메시지 왜곡 없는지 확인 [v4.63.11]"""
        # 6MB의 유효한 UTF-8 문자열이지만 JSON 형식이 아님
        malformed_data = "This is a valid UTF-8 text but NOT a valid JSON object. " + ("x" * 6 * 1024 * 1024)
        
        with open(self.test_json, "w", encoding="utf-8") as f:
            f.write(malformed_data)
        
        res = search_in_json_special(self.test_json, "any", existence_only=True)
        
        self.assertIsInstance(res, tuple)
        self.assertEqual(res[0], "SKIPPED")
        # NoneType 에러가 보고되는지 확인 (v4.63.10 이전의 버그)
        self.assertNotIn("NoneType", str(res[1]), "JSON 파싱 실패 경로에서 NoneType 참조 오류 발생 (메시지 왜곡)")
        self.assertIn("Integrity check failed", res[1], "정상적인 무결성 실패 메시지가 보고되지 않음")

    def test_utf16_prefilter_miss(self):
        """매치가 없는 경우 정확히 False를 반환하여 조기 종료되는지 확인"""
        target_str = "존재하지않는값"
        data = {"key": "다른값", "padding": "y" * 5 * 1024 * 1024} # 5MB+
        
        with open(self.test_json, "w", encoding="utf-8") as f:
            json.dump(data, f)
            
        is_hit = _fast_existence_check(self.test_json, target_str)
        self.assertFalse(is_hit, "매치가 없는데 pre-filter가 True를 반환함 (최적화 실패)")

    def test_utf16_bom_prefilter_hit(self):
        """[v4.63.13 Fix] UTF-16 BOM 파일의 중간에 위치한 검색어를 정상 탐지하는지 확인"""
        self.test_file = "test_utf16_bom_middle.json"
        target_str = "target_match_bom"
        # 검색어가 중간에 오도록 padding 추가
        data = {"padding": "P" * 1024 * 1024, "key": target_str}
        
        try:
            with open(self.test_file, "wb") as f:
                # encode("utf-16") -> BOM 포함됨
                f.write(json.dumps(data).encode("utf-16"))
                
            is_hit = _fast_existence_check(self.test_file, target_str)
            self.assertTrue(is_hit, "UTF-16 BOM 파일 중간의 매치를 찾지 못함 (v4.63.13 결함)")
            
            res = search_in_json_special(self.test_file, target_str, existence_only=True)
            self.assertIsNotNone(res, "UTF-16 BOM existence_only 결과 누락")
        finally:
            if os.path.exists(self.test_file):
                os.remove(self.test_file)

if __name__ == "__main__":
    unittest.main()
