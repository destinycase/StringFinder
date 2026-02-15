import os
import tempfile
import sys

# 프로젝트 루트를 path에 추가하여 모듈 임포트 가능하게 함
sys.path.append(os.path.join(os.path.dirname(__file__), "../src"))

from core.search_engine import FileScanner, search_in_file


def create_test_files(base_dir):
    # 1. UTF-8 파일
    with open(os.path.join(base_dir, "test_utf8.txt"), "w", encoding="utf-8") as f:
        f.write("This is a test file.\n안녕하세요.\nTargetString is here.")

    # 2. EUC-KR 파일
    with open(os.path.join(base_dir, "test_euckr.txt"), "w", encoding="euc-kr") as f:
        f.write("This is a test file.\n안녕하세요.\nTargetString is here.")

    # 3. UTF-16LE 파일
    with open(os.path.join(base_dir, "test_utf16.txt"), "w", encoding="utf-16") as f:
        f.write("This is a test file.\n안녕하세요.\nTargetString is here.")

    # 4. 검색어 없는 파일 (대량)
    for i in range(10):
        with open(os.path.join(base_dir, f"dummy_{i}.txt"), "w", encoding="utf-8") as f:
            f.write("Just some random text without the target.\n" * 100)

    # 5. JSON 파일
    with open(os.path.join(base_dir, "data.json"), "w", encoding="utf-8") as f:
        f.write('{"key": "value", "target": "TargetString is here"}')


def test_file_scanner(base_dir):
    print("\n[Test] FileScanner...")
    scanner = FileScanner([base_dir], [".txt", ".json"])
    files = scanner.scan()
    print(f"Found {len(files)} files.")
    expected_count = 3 + 10 + 1  # utf8, euckr, utf16, 10 dummies, 1 json
    if len(files) == expected_count:
        print("PASS: File count matches.")
    else:
        print(f"FAIL: Expected {expected_count}, got {len(files)}")


def test_search(base_dir):
    print("\n[Test] Search...")
    target = "TargetString"

    # 1. UTF-8
    res = search_in_file(os.path.join(base_dir, "test_utf8.txt"), target)
    print(f"UTF-8 Search: {'PASS' if res else 'FAIL'}")

    # 2. EUC-KR
    res = search_in_file(os.path.join(base_dir, "test_euckr.txt"), target)
    print(f"EUC-KR Search: {'PASS' if res else 'FAIL'}")

    # 3. UTF-16
    res = search_in_file(os.path.join(base_dir, "test_utf16.txt"), target)
    print(f"UTF-16 Search: {'PASS' if res else 'FAIL'}")

    # 4. Dummy (Should be None)
    res = search_in_file(os.path.join(base_dir, "dummy_0.txt"), target)
    print(f"Dummy Search: {'PASS' if res is None else 'FAIL'}")

    # 5. JSON (Special Mode)
    res = search_in_file(os.path.join(base_dir, "data.json"), target, special_mode=["JSON"])
    print(f"JSON Search: {'PASS' if res else 'FAIL'}")


def main():
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"Created temp dir: {temp_dir}")
        create_test_files(temp_dir)

        test_file_scanner(temp_dir)
        test_search(temp_dir)


if __name__ == "__main__":
    main()
