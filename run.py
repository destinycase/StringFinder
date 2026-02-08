import sys
import os
import multiprocessing

# src 디렉토리를 최우선 경로로 추가하여 타 프로젝트와의 임포트 충돌 방지
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from main import main

if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
