import sys
import os
import multiprocessing

# src 디렉토리를 최우선 경로로 추가하여 타 프로젝트와의 임포트 충돌 방지
# [Fix] I-2: 절대 경로 사용 및 중복 추가 방지
src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)
from sf_main import main  # noqa: E402

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
