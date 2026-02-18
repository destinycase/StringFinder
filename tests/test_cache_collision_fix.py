import os
from core.search_cache import HybridSearchCache


def test_directory_signature_collision_fix(tmp_path):
    """
    파일의 위치가 바뀌었을 때(이동/재배치) 시그니처가 변하는지 검증합니다.
    (기존 entry.name 방식에서는 충돌 가능했던 시나리오)
    """
    root = tmp_path / "test_root"
    root.mkdir()

    dir_a = root / "dir_a"
    dir_b = root / "dir_b"
    dir_a.mkdir()
    dir_b.mkdir()

    # 두 디렉토리에 동일한 파일 생성 (내용, 이름 동일)
    file_1 = dir_a / "test.txt"
    file_2 = dir_b / "test.txt"

    content = "same content"
    file_1.write_text(content)
    file_2.write_text(content)

    # 시간 정합성을 위해 명시적으로 동일한 timestamp 설정
    os.utime(file_1, (1000, 1000))
    os.utime(file_2, (1000, 1000))

    cache = HybridSearchCache(str(tmp_path / "cache"), persist=False)

    # 1. 초기 시그니처 계산
    meta_initial = cache._get_paths_metadata([str(root)])
    sig_initial = meta_initial[str(root)]["sig"]

    # 2. 파일 위치 스왑 (dir_a/test.txt <-> dir_b/test.txt)
    # 실제로는 '재배치'를 시뮬레이션:
    # dir_a 에는 다른 파일명을 두거나, 트리 구조만 바뀜

    # 케이스: dir_a/test.txt 제거, dir_b/sub/test.txt 생성 (이름/mtime/크기 동일 유지)
    file_1.unlink()

    sub_dir = dir_b / "sub"
    sub_dir.mkdir()
    file_new = sub_dir / "test.txt"
    file_new.write_text(content)
    os.utime(file_new, (1000, 1000))  # 동일한 mtime 유지

    # 3. 새로운 시그니처 계산
    meta_after = cache._get_paths_metadata([str(root)])
    sig_after = meta_after[str(root)]["sig"]

    # 검증: 시그니처가 달라야 함 (상대 경로가 다르므로)
    assert sig_initial != sig_after, "Directory signature failed to detect file relocation"


def test_mtime_ns_precision(tmp_path):
    """나노초 단위 수정이 시그니처에 반영되는지 확인합니다."""
    root = tmp_path / "ns_root"
    root.mkdir()
    f = root / "file.txt"
    f.write_text("initial")

    cache = HybridSearchCache(str(tmp_path / "cache"), persist=False)

    # 1. 초기 시그니처
    sig_1 = cache._get_paths_metadata([str(root)])[str(root)]["sig"]

    # 2. 아주 빠른 시간 내에 수정 (초 단위 mtime은 같을 수 있음)
    stat = os.stat(f)
    new_mtime_ns = stat.st_mtime_ns + 1000  # 1마이크로초 추가
    os.utime(f, ns=(stat.st_atime_ns, new_mtime_ns))

    # 3. 새로운 시그니처
    sig_2 = cache._get_paths_metadata([str(root)])[str(root)]["sig"]

    assert sig_1 != sig_2, "Signature failed to capture ns-level mtime change"
