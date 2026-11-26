import os
import shutil
import subprocess
import sys
import time
import argparse
import tempfile
from build_support import checked_cleanup_path, walk_project_tree

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

SSOT_ENGINE_PYD = os.path.join(PROJECT_ROOT, "src", "rust_engine", "sf_engine.pyd")
SSOT_ENGINE_SO = os.path.join(PROJECT_ROOT, "src", "rust_engine", "sf_engine.so")

def install_binary(source: str, destination: str) -> None:
    """Stage a complete binary beside the target, then replace it atomically."""
    directory = os.path.dirname(os.path.abspath(destination))
    os.makedirs(directory, exist_ok=True)
    fd, staged = tempfile.mkstemp(prefix=".sf-install-", suffix=".tmp", dir=directory)
    os.close(fd)
    try:
        shutil.copy2(source, staged)
        os.replace(staged, destination)
    except OSError as error:
        raise OSError(
            f"Cannot replace {destination}. Close applications using this file "
            "and retry; the previous destination was not removed."
        ) from error
    finally:
        if os.path.exists(staged):
            os.remove(staged)

def _is_target_artifact(path: str) -> bool:
    parts = {p.lower() for p in os.path.normpath(path).split(os.sep)}
    return "target" in parts


def clean_non_ssot_engine_binaries():
    """Remove duplicate Rust engine binaries outside the SSOT location."""
    src_dir = os.path.join(PROJECT_ROOT, "src")
    if not os.path.isdir(src_dir):
        return

    keep_paths = {
        os.path.normcase(os.path.abspath(SSOT_ENGINE_PYD)),
        os.path.normcase(os.path.abspath(SSOT_ENGINE_SO)),
    }

    for root, _, files in walk_project_tree(src_dir, PROJECT_ROOT):
        for file in files:
            if not (file.startswith("sf_engine.pyd") or file.startswith("sf_engine.so")):
                continue
            path = os.path.join(root, file)
            if _is_target_artifact(path):
                continue
            norm_path = os.path.normcase(os.path.abspath(path))
            if file in {"sf_engine.pyd", "sf_engine.so"} and norm_path in keep_paths:
                continue
            try:
                os.remove(checked_cleanup_path(path, PROJECT_ROOT))
                print(f"Removed non-SSOT engine binary: {path}")
            except Exception as e:
                print(f"Debug: Non-SSOT engine cleanup skipped for {path}: {e}")


def _home_dir(env: dict, name: str, default_leaf: str) -> str:
    value = env.get(name)
    if value:
        return os.path.abspath(value)
    return os.path.join(os.path.expanduser("~"), default_leaf)


def path_remap_rustflags(env: dict) -> list[str]:
    """빌드한 PC의 경로(계정 이름 포함)가 엔진 바이너리에 남지 않도록 바꿔 넣는 옵션.

    Rust는 외부 라이브러리 오류 위치를 빌드한 PC의 절대 경로로 기록합니다. Cargo·rustup 폴더와
    프로젝트 폴더를 고정 이름으로 바꿔, 누가 어디서 빌드했는지 바이너리에서 알 수 없게 합니다.
    """
    pairs = [
        (_home_dir(env, "CARGO_HOME", ".cargo"), "/cargo"),
        (_home_dir(env, "RUSTUP_HOME", ".rustup"), "/rustup"),
        (PROJECT_ROOT, "/stringfinder"),
    ]
    return [f"--remap-path-prefix={src}={dst}" for src, dst in pairs]


def rust_build_env(base: dict) -> dict:
    """엔진 빌드용 환경 변수. 기존 RUSTFLAGS는 유지하고 경로 치환 옵션을 덧붙입니다."""
    env = dict(base)
    # PySide6를 불러온 프로세스(build.py)는 PATH 앞에 PySide6·shiboken6 폴더가 붙습니다. pyo3의 빌드 스크립트는
    # PATH가 바뀌면 다시 돌아, build_rust.py와 build.py가 번갈아 pyo3부터 엔진을 처음부터 다시 컴파일했습니다.
    # 엔진 빌드에는 쓰지 않는 그 폴더들을 빼 두 경로의 환경을 같게 맞춥니다.
    if env.get("PATH"):
        env["PATH"] = os.pathsep.join(
            entry for entry in env["PATH"].split(os.pathsep)
            if os.path.basename(os.path.normpath(entry)).lower() not in ("pyside6", "shiboken6")
        )
    # Python 3.14 등 상위 버전 호환성을 위한 환경 변수 설정
    env["PYO3_USE_ABI3_FORWARD_COMPATIBILITY"] = "1"
    existing = env.pop("CARGO_ENCODED_RUSTFLAGS", None)
    flags = existing.split("\x1f") if existing else env.get("RUSTFLAGS", "").split()
    flags = [flag for flag in flags if flag] + path_remap_rustflags(env)
    # 공백이 든 경로도 깨지지 않도록 구분 문자(0x1F)로 잇는 형식을 씁니다.
    env["CARGO_ENCODED_RUSTFLAGS"] = "\x1f".join(flags)
    env.pop("RUSTFLAGS", None)
    return env


def build_rust_engine(clean_target=False):
    """Rust 엔진(sf_engine) 컴파일 및 배포"""
    # 빌드 시작 시 기존 부산물(.old_*) 선제적 정리
    clean_old_binaries()
    clean_non_ssot_engine_binaries()

    rust_dir = os.path.join(PROJECT_ROOT, "src", "rust_engine")
    if clean_target:
        target_dir = os.path.join(rust_dir, "target")
        if os.path.exists(target_dir):
            print(f"[빌드] 기존 target 폴더 삭제 중: {target_dir}")
            try:
                shutil.rmtree(checked_cleanup_path(target_dir, PROJECT_ROOT))
            except Exception as e:
                print(f"Warning: target 폴더 삭제 실패: {e}")

    print("\n--- Building Rust Engine (sf_engine) ---")
    if not os.path.exists(rust_dir):
        print(f"Error: Rust directory {rust_dir} not found.")
        sys.exit(1)

    env = rust_build_env(os.environ)

    try:
        # 1. Cargo로 컴파일합니다. --locked: Cargo.lock을 빌드 중에 바꾸지 않습니다(CI와 같은 조건).
        #    다른 프로세스가 파일을 잡고 있을 때(OS Error 32)만 다시 시도하고, 컴파일 오류는 바로 멈춥니다.
        #    4.50.0에서 OS Error 32를 피하려고 한 작업씩(-j 1) 빌드하게 했으나, 그 오류는 아래 재시도가 맡습니다.
        #    -j 1은 전체 빌드를 94초→38초(이 장비, 78개 묶음)로 늘리기만 해서 7.7.5 정리 때 뺐습니다.
        cargo_cmd = ["cargo", "build", "--release", "--locked"]
        print(f"Running cargo: {' '.join(cargo_cmd)}")

        max_retries = 5
        for attempt in range(1, max_retries + 1):
            result = subprocess.run(cargo_cmd, cwd=rust_dir, env=env, capture_output=True, text=True,
                                    encoding="utf-8", errors="replace")
            sys.stdout.write(result.stdout)
            sys.stderr.write(result.stderr)
            if result.returncode == 0:
                break
            output = (result.stdout + result.stderr).lower()
            locked = "os error 32" in output or "being used by another process" in output
            if not locked or attempt == max_retries:
                print(f"Rust build failed (exit {result.returncode}, attempt {attempt})")
                raise subprocess.CalledProcessError(result.returncode, cargo_cmd, result.stdout, result.stderr)
            print(f"[경고] 파일이 다른 프로세스에 잡혀 있습니다(OS Error 32). {attempt}회 재시도 중...")
            time.sleep(2)

        # 2. 생성된 dll을 .pyd로 변경하여 단일 경로(src/rust_engine)로 복사 (SSOT)
        src_dll = os.path.join(rust_dir, "target", "release", "sf_engine.dll")
        dst_pyd = SSOT_ENGINE_PYD

        if os.path.exists(src_dll):
            install_binary(src_dll, dst_pyd)
            print(f"\n[Success] Rust binary deployed to single path: {dst_pyd}")

            # 복사 완료 후 잠기지 않은 .old 파일들이 있다면 한 번 더 정리 시도
            clean_old_binaries()
            clean_non_ssot_engine_binaries()

            print("Now you can use high-speed Rust engine in development mode (run.py).")
            return dst_pyd
        else:
            print("Error: Rust binary (.dll) not found after cargo build.")
            sys.exit(1)

    except subprocess.CalledProcessError as e:
        print(f"Rust build failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        sys.exit(1)


def clean_old_binaries():
    """배포된 바이너리의 .old 부산물들만 정리"""
    src_dir = os.path.join(PROJECT_ROOT, "src", "rust_engine")
    if not os.path.exists(src_dir):
        return
    for file in os.listdir(src_dir):
        if file.startswith("sf_engine.pyd.old_") or ".pyd.old_" in file:
            try:
                path = os.path.join(src_dir, file)
                os.remove(checked_cleanup_path(path, PROJECT_ROOT))
                print(f"Removed old binary: {file}")
            except Exception as e:
                print(f"Debug: Old binary removal skipped: {e}")


def clean_binary(clean_all=False):
    """배포된 바이너리 및 빌드 산출물 정리"""
    dst_pyd = SSOT_ENGINE_PYD
    if os.path.exists(dst_pyd):
        try:
            os.remove(checked_cleanup_path(dst_pyd, PROJECT_ROOT))
            print(f"Removed binary: {dst_pyd}")
        except Exception as e:
            print(f"Error removing {dst_pyd}: {e}")

    # .old 파일 정리 호출
    clean_old_binaries()
    clean_non_ssot_engine_binaries()

    if clean_all:
        target_dir = os.path.join(PROJECT_ROOT, "src", "rust_engine", "target")
        if os.path.exists(target_dir):
            print(f"Cleaning rust target: {target_dir}")
            shutil.rmtree(checked_cleanup_path(target_dir, PROJECT_ROOT), ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build and deploy Rust engine for StringFinder development.")
    parser.add_argument("--clean", action="store_true", help="Remove deployed binary from src folder.")
    args = parser.parse_args()

    # 상위 디렉토리로 작업 경로 설정 (프로젝트 루트 보장)
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    if args.clean:
        clean_binary(clean_all=True)
    else:
        # 빌드 전 가벼운 클린업 수행 (선택 가능)
        build_rust_engine(clean_target=False)
