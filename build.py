import os
import shutil
import subprocess
import sys

try:
    from PIL import Image
except ImportError:
    print("Error: 'Pillow' library is required for building with icon. Please install it using 'pip install pillow'.")
    sys.exit(1)

try:
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtCore import QSize, Qt, QBuffer, QIODevice
except ImportError:
    print("Error: 'PySide6' is required for SVG icon conversion.")
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
    svg_icon_path = os.path.join("src", "assets", "icon.svg")
    ico_icon_path = os.path.join("src", "assets", "icon.ico")

    # SVG를 ICO로 변환 (Qt + Pillow 조합)
    print(f"--- Converting {svg_icon_path} to {ico_icon_path} ---")
    try:
        if os.path.exists(svg_icon_path):
            renderer = QSvgRenderer(svg_icon_path)
            if not renderer.isValid():
                raise ValueError("Invalid SVG file")
            
            # 윈도우 탐색기가 선호하는 표준 사이즈 (고해상도 256px 포함)
            icon_sizes = [256, 128, 64, 48, 32, 16]
            images = []
            
            for size in icon_sizes:
                image = QImage(QSize(size, size), QImage.Format_ARGB32)
                image.fill(Qt.transparent)
                painter = QPainter(image)
                renderer.render(painter)
                painter.end()
                
                buffer = QBuffer()
                buffer.open(QIODevice.ReadWrite)
                image.save(buffer, "PNG")
                import io
                pil_img = Image.open(io.BytesIO(buffer.data().data()))
                images.append(pil_img)

            # 첫 번째 이미지(256)를 메진으로 하여 ICO 생성
            images[0].save(ico_icon_path, format="ICO", append_images=images[1:])
            print(f"Icon conversion successful: {ico_icon_path}")
        else:
            print(f"Error: {svg_icon_path} not found.")
            sys.exit(1)
    except Exception as e:
        print(f"Icon conversion failed: {e}")
        sys.exit(1)

    # PyInstaller 명령 실행
    assets_dir = os.path.abspath(os.path.join("src", "assets"))
    main_path = os.path.abspath(os.path.join("src", "main.py"))
    
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--noconsole",
        "--clean",
        "--name",
        "StringFinder",
        f"--icon={os.path.abspath(ico_icon_path)}",
        f"--add-data={assets_dir};assets",
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
        sys.exit(1)

    # Note: 빌드 후 생성된 ICO 파일을 프로젝트 자산으로 유지합니다.
    print(f"Icon preserved at: {ico_icon_path}")

    # 최종 정리 실행
    cleanup()


if __name__ == "__main__":
    build()
