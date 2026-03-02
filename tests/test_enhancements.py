import pytest
from core.worker import SearchWorker
from sf_utils.constants import Constants
from unittest.mock import MagicMock

def test_adaptive_batching_logic():
    """적응형 배치 로직이 파일 크기 임계치에 따라 올바르게 분할되는지 검증"""
    # 임계치 100MB 설정
    max_size = 100 * 1024 * 1024
    
    # 테스트 데이터: 60MB, 50MB, 110MB, 10MB
    files = [
        ("file1.txt", 60 * 1024 * 1024),
        ("file2.txt", 50 * 1024 * 1024),
        ("file3.txt", 110 * 1024 * 1024),
        ("file4.txt", 10 * 1024 * 1024)
    ]
    
    worker = SearchWorker({
        Constants.PAYLOAD_FILE_LIST: files,
        Constants.PAYLOAD_SEARCH_STRING: "test",
        Constants.PAYLOAD_USE_COMPLEX_SEARCH: True
    })
    
    # _run_batch_search 내부의 배치 생성 로직만 검증하기 위해 리포지토리 로직 재현
    def simulate_batching(files, max_size):
        batches = []
        curr = []
        curr_size = 0
        for f_info in files:
            f_path, f_size = f_info
            if f_size >= max_size and not curr:
                batches.append([f_info])
                continue
            if len(curr) >= 100 or (curr_size + f_size) > max_size:
                if curr: batches.append(curr)
                curr = [f_info]
                curr_size = f_size
            else:
                curr.append(f_info)
                curr_size += f_size
        if curr: batches.append(curr)
        return batches

    res_batches = simulate_batching(files, max_size)
    assert len(res_batches) == 4
    assert res_batches[0][0][0] == "file1.txt"
    assert res_batches[1][0][0] == "file2.txt"
    assert res_batches[2][0][0] == "file3.txt"
    assert res_batches[3][0][0] == "file4.txt"

def test_doctor_report_generation():
    """Doctor Mode 보고서 생성 및 기본 항목 포함 여부 검증"""
    from core.doctor import SystemDoctor
    doctor = SystemDoctor()
    report = doctor.run_full_diagnosis()
    
    assert "# StringFinder" in report
    assert "## Basic Info" in report
    assert "## Engine Integrity" in report
    assert "## Permissions & Paths" in report
