"""
[test_chaos_fs_edge.py]

이 테스트는 파일 시스템의 극단적인 구조 또는 엣지 케이스 상황에서 시스템의 복원력을 검증하는 카오스 테스트입니다.

- 테스트 목적:
  1. 파일 시스템 순회 중 발생할 수 있는 교착 상태 또는 런타임 오류 차단.
  2. 동시적으로 변화하는 파일 시스템 구조에 대한 안정적인 결과 반환.
"""

import os

import pytest

from core.search_engine import search_in_files_batch


@pytest.mark.chaos
@pytest.mark.skipif(os.name != "nt", reason="Windows-only long path behavior")
def test_chaos_long_path_search(tmp_path):
    deep_dir = str(tmp_path)
    for i in range(20):
        deep_dir = os.path.join(deep_dir, f"deep_folder_{i}_1234567890")

    try:
        os.makedirs(deep_dir, exist_ok=True)
    except OSError:
        deep_dir = "\\\\?\\" + os.path.abspath(deep_dir)
        os.makedirs(deep_dir, exist_ok=True)

    file_path = os.path.join(deep_dir, "target_file.txt")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("FindMeInTheDeep")

    st = os.stat(file_path)
    result = search_in_files_batch([(file_path, st.st_size)], "FindMe", None)

    assert "results" in result
    assert "skipped" in result
