import os
from unittest.mock import MagicMock, patch
from core.search_engine import FileScanner
from utils.config_manager import ConfigManager

def test_file_scanner_cancellation():
    """FileScanner가 stop_check_callback에 의해 즉시 중단되는지 검증"""
    # 폴더 구조 모킹
    with patch("os.walk") as mock_walk, \
         patch("os.path.exists", return_value=True), \
         patch("os.path.getsize", return_value=100):
        
        # 무한히 많은 파일이 있는 상황 시뮬레이션
        mock_walk.return_value = [("root", [], ["file1.txt", "file2.txt", "file3.txt"])] * 10
        
        # 1번째 호출에서는 False(계속), 2번째 호출부터 True(중단) 반환하는 콜백
        stop_callback = MagicMock(side_effect=[False, True, True, True, True])
        
        scanner = FileScanner(["dummy_root"], ["txt"], stop_check_callback=stop_callback)
        scanner.scan()
        
        # 중단되었으므로 적어도 콜백이 호출되었음을 확인
        assert stop_callback.call_count >= 2

def test_config_manager_atomic_save(tmp_path):
    """ConfigManager가 임시 파일을 거쳐 저장하는지 검증 (Atomic Save)"""
    # APPDATA 환경변수 조작
    os.environ["APPDATA"] = str(tmp_path)
    cm = ConfigManager()
    cm.save()
    
    assert os.path.exists(cm.config_path)
    # 임시 파일은 남아있지 않아야 함
    assert not os.path.exists(cm.config_path + ".tmp")
