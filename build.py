import os
import shutil
import subprocess
import sys

try:
    from PIL import Image
except ImportError:
    print("Error: 'Pillow' library is required for building with icon. Please install it using 'pip install pillow'.")
    sys.exit(1)


def cleanup():
    """빌드 과정에서 발생한 모든 부산물 정리"""
    print("\n--- Cleaning up build byproducts ---")

    # 1. 고정적인 캐시 및 빌드 폴더 삭제
    # (dist는 결과물이므로 제외하고 build 전용 폴더 및 캐시 삭제)
    targets = ["build", ".pytest_cache", ".ruff_cache"]
    for target in targets:
        if os.path.exists(target):
            shutil.rmtree(target)
            print(f"Removed folder: {target}")

    # 2. 모든 PyInstaller 생성 .spec 파일 삭제
    for file in os.listdir("."):
        if file.endswith(".spec"):
            os.remove(file)
            print(f"Removed spec file: {file}")

    # 3. 모든 __pycache__ 폴더 재귀적 삭제
    for root, dirs, _ in os.walk(".", topdown=False):
        for name in dirs:
            if name == "__pycache__":
                pycache_path = os.path.join(root, name)
                shutil.rmtree(pycache_path)
                print(f"Removed: {pycache_path}")


def build():
    print("--- Starting Build Process ---")

    # 시작 시 기존 build/dist 폴더 정리 (dist는 새로 만들기 위함)
    for folder in ["build", "dist"]:
        if os.path.exists(folder):
            shutil.rmtree(folder)
            print(f"Cleaned up {folder} folder.")

    # 아이콘 및 리소스 경로 정의
    png_icon_path = os.path.join("src", "resources", "icon.png")
    ico_icon_path = os.path.join("src", "resources", "icon.ico")

    # PNG를 ICO로 변환 (Windows 탐색기 아이콘 호환성 향상)
    print(f"--- Converting {png_icon_path} to {ico_icon_path} ---")
    try:
        img = Image.open(png_icon_path)
        # 여러 사이즈를 포함하는 ICO 생성
        icon_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        img.save(ico_icon_path, format="ICO", sizes=icon_sizes)
        print("Icon conversion successful.")
    except Exception as e:
        print(f"Icon conversion failed: {e}")
        # 실패 시 PNG 그대로 사용 시도 (작동 안 할 가능성 높음)
        ico_icon_path = png_icon_path

    # PyInstaller 명령 실행
    # 윈도우 환경에서 PATH 이슈 방지를 위해 python -m PyInstaller 방식 사용
    # PyInstaller 명령 실행
    # 윈도우 환경에서 경로 잠금을 방지하기 위해 절대 경로를 사용하고 따옴표 처리를 보강합니다.
    src_res = os.path.abspath(os.path.join("src", "resources"))
    main_path = os.path.abspath(os.path.join("src", "main.py"))
    icon_path = os.path.abspath(ico_icon_path)
    
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--noconsole",
        "--clean",
        "--name",
        "StringFinder",
        f"--icon={icon_path}",
        f"--add-data={src_res};resources",
        "--paths",
        os.path.abspath("src"),
        "--workpath",
        os.path.abspath("build"),
        "--distpath",
        os.path.abspath("dist"),
        main_path,
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

    # 임시 ICO 파일 삭제
    if os.path.exists(ico_icon_path) and ico_icon_path.endswith(".ico"):
        os.remove(ico_icon_path)
        print(f"Removed temporary icon file: {ico_icon_path}")

    # 최종 정리 실행
    cleanup()


if __name__ == "__main__":
    build()
