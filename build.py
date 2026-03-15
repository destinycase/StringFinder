import os
import shutil
import sys
import argparse

# [격리 강화] 프로젝트 루트를 스크립트 위치 기준으로 고정하여 작업 디렉토리 영향을 차단
PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
os.chdir(PROJECT_ROOT)

# [보호 가드] 프로젝트 마커 확인
if not os.path.exists(os.path.join(PROJECT_ROOT, "pyproject.toml")) or not os.path.exists(
    os.path.join(PROJECT_ROOT, "src", "sf_main.py")
):
    print(f"Error: Invalid project root: {PROJECT_ROOT}")
    print("Please run this script from the StringFinder project root.")
    sys.exit(1)

# [격리 강화] sys.path에서 현재 가상 환경 및 프로젝트 외부 경로 유입을 차단합니다.
python_home_early = os.path.normpath(sys.prefix).lower()
isolated_initial_path = []
for path in sys.path:
    norm_path = os.path.normpath(path).lower()
    # 파이썬 홈이나 현재 프로젝트 경로 내부에 있는 것만 허용
    if norm_path.startswith(python_home_early) or norm_path.startswith(PROJECT_ROOT.lower()):
        isolated_initial_path.append(path)
    else:
        print(f"Purging external path from sys.path: {path}")
sys.path = isolated_initial_path

try:
    from PIL import Image
except ImportError:
    print("Error: 'Pillow' library is required for building with icon. Please install it using 'pip install pillow'.")
    sys.exit(1)

try:
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtCore import QSize, Qt
    import tempfile
except ImportError:
    print("Error: 'PySide6' is required for SVG icon conversion.")
    sys.exit(1)


def get_project_version():
    """pyproject.toml에서 프로젝트 버전을 추출합니다."""
    try:
        config_path = os.path.join(PROJECT_ROOT, "pyproject.toml")
        with open(config_path, "r", encoding="utf-8") as f:
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
    targets = ["build", ".pytest_cache", ".ruff_cache"]
    for target in targets:
        abs_target = os.path.join(PROJECT_ROOT, target)
        if os.path.exists(abs_target):
            shutil.rmtree(abs_target, ignore_errors=True)
            print(f"Removed folder: {abs_target}")

    # 2. PyInstaller 생성 .spec 파일 삭제
    for file in os.listdir(PROJECT_ROOT):
        if file.endswith(".spec") and file != "StringFinder.spec":
            abs_file = os.path.join(PROJECT_ROOT, file)
            os.remove(abs_file)
            print(f"Removed spec file: {abs_file}")

    # 3. 모든 __pycache__ 폴더 재귀적 삭제
    excluded_roots = {
        os.path.normpath(os.path.join(PROJECT_ROOT, "venv312")),
        os.path.normpath(os.path.join(PROJECT_ROOT, ".venv")),
        os.path.normpath(os.path.join(PROJECT_ROOT, "venv")),
    }
    for root, dirs, _ in os.walk(PROJECT_ROOT, topdown=False):
        norm_root = os.path.normpath(root)
        if any(norm_root == ex or norm_root.startswith(ex + os.sep) for ex in excluded_roots):
            continue
        for name in dirs:
            if name == "__pycache__":
                pycache_path = os.path.join(root, name)
                shutil.rmtree(pycache_path, ignore_errors=True)
                print(f"Removed: {pycache_path}")


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


def build(clean_first=False):
    print("--- Starting Build Process ---")
    if clean_first:
        cleanup()

    # pyproject.toml에서 버전 획득
    app_version = get_project_version()
    print(f"Build Version: {app_version}")

    # 임시 _version.py 파일 생성 (Constants에서 로드됨)
    version_file_content = f'VERSION = "{app_version}"\n'
    version_file_path = os.path.join("src", "sf_utils", "_version.py")
    with open(version_file_path, "w", encoding="utf-8") as f:
        f.write(version_file_content)
    print(f"Created temporary version file: {version_file_path}")

    # 시작 시 기존 build/dist 폴더 정리
    for folder in ["build", "dist", "rust_bin"]:
        abs_folder = os.path.join(PROJECT_ROOT, folder)
        if os.path.exists(abs_folder):
            shutil.rmtree(abs_folder, ignore_errors=True)
            print(f"Cleaned up {abs_folder} folder.")

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
                image = QImage(QSize(size, size), QImage.Format.Format_ARGB32)
                image.fill(Qt.GlobalColor.transparent)
                painter = QPainter(image)
                renderer.render(painter)
                painter.end()

                # PySide6/Qt 버전에 따른 QBuffer 호환성 문제를 해결하기 위해 가장 안정적인 임시 파일 방식 사용
                with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tf:
                    temp_path = tf.name
                try:
                    # 확장자를 통해 포맷 자동 감지 (.png)
                    image.save(temp_path)
                    pil_img = Image.open(temp_path).copy()
                    images.append(pil_img)
                finally:
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except Exception:
                            pass

            images[0].save(ico_icon_path, format="ICO", append_images=images[1:])
            print(f"Icon conversion successful: {ico_icon_path}")
        else:
            print(f"Error: {svg_icon_path} not found.")
            sys.exit(1)
    except Exception as e:
        print(f"Icon conversion failed: {e}")
        sys.exit(1)

    # PyInstaller 명령 실행
    # (API 호출로 전환하기 위해 cmd 리스트에서 sys.executable 및 -m PyInstaller 제거)
    assets_dir = os.path.join(PROJECT_ROOT, "src", "assets")
    main_path = os.path.join(PROJECT_ROOT, "src", "sf_main.py")
    dist_dir = os.path.join(PROJECT_ROOT, "dist")
    build_dir = os.path.join(PROJECT_ROOT, "build")

    pyi_args = [
        "--onefile",
        "--noconsole",
        "--name",
        "StringFinder",
        f"--icon={os.path.abspath(ico_icon_path)}",
        f"--add-data={assets_dir}{os.pathsep}assets",
        f"--add-binary={os.path.abspath(rust_pyd_path)}{os.pathsep}.",
        "--paths",
        os.path.join(PROJECT_ROOT, "src"),
        "--workpath",
        build_dir,
        "--specpath",
        build_dir,
        "--distpath",
        dist_dir,
        main_path,
    ]

    # Qt 바인딩 충돌 방지
    qt_excludes = [
        "PyQt5",
        "PyQt5.QtCore",
        "PyQt5.QtGui",
        "PyQt5.QtWidgets",
        "PyQt6",
        "PyQt6.QtCore",
        "PyQt6.QtGui",
        "PyQt6.QtWidgets",
        "PySide2",
        "PySide2.QtCore",
        "PySide2.QtGui",
        "PySide2.QtWidgets",
    ]
    for module_name in qt_excludes:
        pyi_args.extend(["--exclude-module", module_name])

    # [격리 강화] 화이트리스트 방식으로 sys.path 재구성 (외부 site-packages 차단)
    # 표준 라이브러리 경로와 현재 프로젝트 경로, 그리고 PyInstaller 관련 경로만 허용합니다.
    original_path = list(sys.path)
    try:
        # 안전한 경로 패턴: 파이썬 홈 디렉토리, 프로젝트 루트, 빌드 임시 디렉토리
        python_home = os.path.normpath(sys.prefix).lower()
        project_root = os.path.normpath(os.getcwd()).lower()

        isolated_path = []
        for p in sys.path:
            norm_p = os.path.normpath(p).lower()

            # 화이트리스트: 현재 파이썬 환경(venv 포함) 내부이거나 프로젝트 소스 내부인 경우만 허용
            # 이를 통해 시스템 전역에 설치된 타 프로젝트의 site-packages나 사용자 레벨의 불필요한 경로를 차단합니다.
            if norm_p.startswith(python_home) or norm_p.startswith(project_root):
                isolated_path.append(p)

        sys.path = isolated_path

        print("\n--- Starting Isolated Build via PyInstaller API ---")
        import PyInstaller.__main__

        # [격리 강화] 빌드 환경 변수 강제 제어
        # PYTHONPATH, PYTHONHOME이 설정되어 있으면 PyInstaller가 외부 모듈을 참조할 수 있음
        os.environ.pop("PYTHONPATH", None)
        os.environ.pop("PYTHONHOME", None)
        os.environ["PYTHONNOUSERSITE"] = "1"  # 사용자 레벨 site-packages 무시

        # PyInstaller API 호출
        PyInstaller.__main__.run(pyi_args)

        print("\n--- Build Successful! ---")
        print(f"Executable location: {os.path.abspath(os.path.join('dist', 'StringFinder.exe'))}")
    except Exception as e:
        print(f"\n--- Build Failed! --- \n{e}")
        sys.exit(1)
    finally:
        # sys.path 복구 (빌드 이후 사후 정리를 위해)
        sys.path = original_path

    # 최종 정리 실행
    cleanup()
    print(f"Preserved binary for development: {rust_pyd_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="StringFinder build script")
    parser.add_argument("--clean", action="store_true", help="Clean build byproducts before building")
    parser.add_argument("--clean-only", action="store_true", help="Only clean byproducts and exit")
    args = parser.parse_args()

    if args.clean_only:
        cleanup()
        sys.exit(0)

    build(clean_first=args.clean)
