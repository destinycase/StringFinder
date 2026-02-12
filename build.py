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


def build_rust():
    """Rust 엔진(sf_engine) 컴파일"""
    print("\n--- Building Rust Engine (sf_engine) ---")
    rust_dir = os.path.join("src", "sf_engine")
    if not os.path.exists(rust_dir):
        print(f"Error: Rust directory {rust_dir} not found.")
        sys.exit(1)

    # maturin build --release
    # 결과를 임시 디렉토리에 저장
    rust_bin_dir = os.path.abspath("rust_bin")
    if os.path.exists(rust_bin_dir):
        shutil.rmtree(rust_bin_dir)
    os.makedirs(rust_bin_dir)

    # Python 3.14 등 상위 버전 호환성을 위한 환경 변수 설정
    env = os.environ.copy()
    env["PYO3_USE_ABI3_FORWARD_COMPATIBILITY"] = "1"

    try:
        # 현재 Python 인터프리터에 맞는 .pyd 파일 생성 (Windows)
        cmd = ["maturin", "build", "--release", "--interpreter", sys.executable, "--out", rust_bin_dir]
        print(f"Running maturin: {' '.join(cmd)}")
        subprocess.check_call(cmd, cwd=rust_dir, env=env)

        # 생성된 wheel 파일에서 .pyd 파일 추출 (또는 단순히 위치 파악)
        # maturin build --out {dir} 은 {dir}에 .whl을 생성함.
        # 더 간단하게 pyd만 얻기 위해 maturin sdist 대신 maturin build 가 권장됨.
        # 여기서는 단순히 PyInstaller가 찾을 수 있도록 .pyd 파일의 위치를 반환하거나
        # 특정 위치로 복사하는 로직이 필요함.
        # maturin build가 생성한 wheel 안의 pyd를 찾는 것은 복잡하므로,
        # maturin develop --release를 실행하여 site-packages에 설치하게 하거나
        # cargo build를 직접 사용하여 pyd를 얻는 방법이 있음.

        # 여기서는 가장 확실한 방법인 cargo build 후 파일명 변경 방식을 사용 (PyO3 지원)
        cargo_cmd = ["cargo", "build", "--release"]
        print(f"Running cargo: {' '.join(cargo_cmd)}")
        subprocess.check_call(cargo_cmd, cwd=rust_dir, env=env)

        # 생성된 dll을 .pyd로 변경하여 src 폴더로 복사 (PyInstaller가 자동으로 찾도록)
        src_dll = os.path.join(rust_dir, "target", "release", "sf_engine.dll")
        dst_pyd = os.path.join("src", "sf_engine.pyd")

        if os.path.exists(src_dll):
            shutil.copy2(src_dll, dst_pyd)
            print(f"Rust binary copied to: {dst_pyd}")
            return dst_pyd
        else:
            print("Error: Rust binary (.dll) not found after cargo build.")
            sys.exit(1)

    except subprocess.CalledProcessError as e:
        print(f"Rust build failed: {e}")
        sys.exit(1)
    finally:
        # Rust 빌드 부산물 정리 (target 폴더)
        rust_target_dir = os.path.join(rust_dir, "target")
        if os.path.exists(rust_target_dir):
            shutil.rmtree(rust_target_dir)
            print(f"Cleaned up Rust build artifacts: {rust_target_dir}")


def build():
    print("--- Starting Build Process ---")

    # 시작 시 기존 build/dist 폴더 정리
    for folder in ["build", "dist", "rust_bin"]:
        if os.path.exists(folder):
            shutil.rmtree(folder)
            print(f"Cleaned up {folder} folder.")

    # Rust 엔진 빌드 실행
    rust_pyd_path = build_rust()

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
