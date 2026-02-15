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


def get_project_version():
    """pyproject.toml에서 프로젝트 버전을 추출합니다."""
    try:
        with open("pyproject.toml", "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("version = "):
                    return line.split("=")[1].strip().strip('"').strip("'")
    except Exception as e:
        print(f"Warning: Could not read version from pyproject.toml: {e}")
    return "unknown"


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

    # 4. 임시 버전 파일 삭제
    version_file = os.path.join("src", "utils", "_version.py")
    if os.path.exists(version_file):
        os.remove(version_file)
        print(f"Removed temporary version file: {version_file}")


def build_rust_wrapper():
    """Rust 엔진빌드 위임 (build_rust.py)"""
    try:
        from build_rust import build_rust_engine

        return build_rust_engine()
    except ImportError:
        print("Error: build_rust.py not found.")
        sys.exit(1)
    except Exception as e:
        print(f"Rust build failed: {e}")
        sys.exit(1)


def build():
    print("--- Starting Build Process ---")

    # pyproject.toml에서 버전 획득
    app_version = get_project_version()
    print(f"Build Version: {app_version}")

    # 임시 _version.py 파일 생성 (Constants에서 로드됨)
    version_file_content = f'VERSION = "{app_version}"\n'
    version_file_path = os.path.join("src", "utils", "_version.py")
    with open(version_file_path, "w", encoding="utf-8") as f:
        f.write(version_file_content)
    print(f"Created temporary version file: {version_file_path}")

    # 시작 시 기존 build/dist 폴더 정리
    for folder in ["build", "dist", "rust_bin"]:
        if os.path.exists(folder):
            shutil.rmtree(folder)
            print(f"Cleaned up {folder} folder.")

    # Rust 엔진 빌드 실행
    rust_pyd_path = build_rust_wrapper()

    # 아이콘 및 리소스 경로 정의
    svg_icon_path = os.path.join("src", "assets", "icon.svg")
    ico_icon_path = os.path.join("src", "assets", "icon.ico")

    # SVG를 ICO로 변환
    print(f"--- Converting {svg_icon_path} to {ico_icon_path} ---")
    try:
        if os.path.exists(svg_icon_path):
            renderer = QSvgRenderer(svg_icon_path)
            if not renderer.isValid():
                raise ValueError("Invalid SVG file")

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
        # Rust 이진 파일 명시적 추가 (경로 세미콜론 주의 - Windows)
        f"--add-binary={os.path.abspath(rust_pyd_path)};.",
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

    # 최종 정리 실행 (src/sf_engine.pyd 임시 파일 포함)
    cleanup()
    if os.path.exists(rust_pyd_path):
        os.remove(rust_pyd_path)
        print(f"Removed temporary binary: {rust_pyd_path}")


if __name__ == "__main__":
    build()
