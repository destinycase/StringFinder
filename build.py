import os
import shutil
import subprocess
import sys

def cleanup():
    """빌드 과정에서 발생한 모든 부산물 정리"""
    print("\n--- Cleaning up build byproducts ---")
    
    # 1. 고정적인 캐시 및 빌드 폴더 삭제 
    # (dist는 결과물이므로 제외하고 build 전용 폴더 및 캐시 삭제)
    targets = ['build', '.pytest_cache', '.ruff_cache']
    for target in targets:
        if os.path.exists(target):
            shutil.rmtree(target)
            print(f"Removed folder: {target}")

    # 2. 모든 PyInstaller 생성 .spec 파일 삭제
    for file in os.listdir('.'):
        if file.endswith('.spec'):
            os.remove(file)
            print(f"Removed spec file: {file}")

    # 3. 모든 __pycache__ 폴더 재귀적 삭제
    for root, dirs, _ in os.walk('.', topdown=False):
        for name in dirs:
            if name == "__pycache__":
                pycache_path = os.path.join(root, name)
                shutil.rmtree(pycache_path)
                print(f"Removed: {pycache_path}")

def build():
    print("--- Starting Build Process ---")
    
    # 시작 시 기존 build/dist 폴더 정리 (dist는 새로 만들기 위함)
    for folder in ['build', 'dist']:
        if os.path.exists(folder):
            shutil.rmtree(folder)
            print(f"Cleaned up {folder} folder.")

    # PyInstaller 명령 실행
    cmd = [
        "pyinstaller",
        "--onefile",
        "--noconsole",
        "--clean",
        "--name", "StringFinder",
        "--paths", "src",
        "--workpath", "build",
        "--distpath", "dist",
        os.path.join("src", "main.py")
    ]
    
    print(f"Running command: {' '.join(cmd)}")
    
    try:
        subprocess.check_call(cmd)
        print("\n--- Build Successful! ---")
        print(f"Executable location: {os.path.abspath(os.path.join('dist', 'StringFinder.exe'))}")
    except subprocess.CalledProcessError as e:
        print(f"\n--- Build Failed! --- \n{e}")
        # 실패하더라도 cleanup은 실행하도록 구성 가능하나, 
        # 디버깅을 위해 에러 발생 시엔 정지를 유지
        sys.exit(1)

    # 최종 정리 실행
    cleanup()

if __name__ == "__main__":
    build()
