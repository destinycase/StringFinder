

def test_adaptive_batching_logic():
    """적응형 배치 로직이 파일 크기 임계치에 따라 올바르게 분할되는지 검증"""
    max_size = 100 * 1024 * 1024
    
    files = [
        ("file1.txt", 60 * 1024 * 1024),
        ("file2.txt", 50 * 1024 * 1024),
        ("file3.txt", 110 * 1024 * 1024),
        ("file4.txt", 10 * 1024 * 1024)
    ]

    def simulate_batching(files, max_size):
        batches = []
        curr: list[tuple[str, int]] = []
        curr_size = 0
        for f_info in files:
            f_path, f_size = f_info
            if f_size >= max_size and not curr:
                batches.append([f_info])
                continue
            if len(curr) >= 100 or (curr_size + f_size) > max_size:
                if curr:
                    batches.append(curr)
                curr = [f_info]
                curr_size = f_size
            else:
                curr.append(f_info)
                curr_size += f_size
        if curr:
            batches.append(curr)
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
    assert "## 기본 시스템 정보" in report
    assert "## 검색 코어 엔진 무결성" in report
    assert "## 저장 경로 및 파일 권한" in report
