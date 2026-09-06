import os
import time
from pathlib import Path
from unittest.mock import patch


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


def test_doctor_report_uses_unique_temporary_files(monkeypatch, tmp_path):
    """동시 진단 실행이 같은 보고서 경로를 덮어쓰지 않는지 검증합니다."""
    from core import doctor

    monkeypatch.setattr(doctor.tempfile, "gettempdir", lambda: str(tmp_path))
    opened_paths = []
    with patch("core.doctor.open_file", side_effect=lambda path: opened_paths.append(path) or True):
        assert doctor.run_doctor_and_open()
        assert doctor.run_doctor_and_open()

    assert len(opened_paths) == 2
    assert opened_paths[0] != opened_paths[1]
    for path in opened_paths:
        assert os.path.dirname(path) == str(tmp_path)
        assert os.path.basename(path).startswith("sf_doctor_")
        assert "# StringFinder" in Path(path).read_text(encoding="utf-8")
        doctor._remove_report(path)


def test_doctor_cleanup_only_removes_stale_owned_reports(tmp_path):
    from core import doctor

    stale = tmp_path / "sf_doctor_stale.md"
    recent = tmp_path / "sf_doctor_recent.md"
    unrelated = tmp_path / "other_report.md"
    for path in (stale, recent, unrelated):
        path.write_text("report", encoding="utf-8")
    old_time = time.time() - doctor._DOCTOR_REPORT_MAX_AGE_SECONDS - 60
    os.utime(stale, (old_time, old_time))
    os.utime(unrelated, (old_time, old_time))

    doctor._cleanup_stale_reports(str(tmp_path))

    assert not stale.exists()
    assert recent.exists()
    assert unrelated.exists()
