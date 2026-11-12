import os
import unittest
from folder_search_helper import search_in_json_special

class TestPrefilterIntegrity(unittest.TestCase):
    def setUp(self):
        self.test_json = "test_corrupted.json"
        
    def tearDown(self):
        for f in [self.test_json]:
            if os.path.exists(f):
                os.remove(f)


    def test_large_corrupted_file_integrity(self):
        """6MB 손상된 파일(Invalid Encoding)에서 NoneType 에러 없이 SKIPPED 반환되는지 확인 [v4.63.10]"""
        # 6MB의 0xFF (Invalid UTF-8/Any)
        corrupted_data = b"\xff" * 6 * 1024 * 1024
        
        for f_path in [self.test_json]:
            with open(f_path, "wb") as f:
                f.write(corrupted_data)
            
            # JSON 경로 테스트
            res = search_in_json_special(f_path, "any")
                
            self.assertIsInstance(res, tuple, f"{f_path}에서 결과가 Tuple(SKIPPED)이 아님")
            if isinstance(res, tuple) and len(res) == 2:
                self.assertEqual(res[0], "SKIPPED", f"{f_path} 무결성 체크 실패 보고 누락")
                self.assertNotIn("NoneType", str(res[1]), f"{f_path}에서 NoneType 참조 오류 발생")

    def test_mmap_json_malformed_integrity(self):
        """디코딩은 성공하지만 JSON 파싱이 실패하는 6MB 파일에서 메시지 왜곡 없는지 확인 [v4.63.11]"""
        # 6MB의 유효한 UTF-8 문자열이지만 JSON 형식이 아님
        malformed_data = "This is a valid UTF-8 text but NOT a valid JSON object. " + ("x" * 6 * 1024 * 1024)
        
        with open(self.test_json, "w", encoding="utf-8") as f:
            f.write(malformed_data)
        
        res = search_in_json_special(self.test_json, "any")
        
        self.assertIsInstance(res, tuple)
        if isinstance(res, tuple) and len(res) == 2:
            self.assertEqual(res[0], "SKIPPED")
            # NoneType 에러가 보고되는지 확인 (v4.63.10 이전의 버그)
            self.assertNotIn("NoneType", str(res[1]), "JSON 파싱 실패 경로에서 NoneType 참조 오류 발생 (메시지 왜곡)")
            self.assertIn("JSON", str(res[1]), "정상적인 JSON 구문 오류 메시지가 보고되지 않음")
            self.assertNotIn("{}", str(res[1]), "포맷되지 않은 오류 메시지가 노출됨")


if __name__ == "__main__":
    unittest.main()
