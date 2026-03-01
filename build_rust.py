import os
import shutil
import subprocess
import sys
import time
import argparse

SSOT_ENGINE_PYD = os.path.join("src", "rust_engine", "sf_engine.pyd")
SSOT_ENGINE_SO = os.path.join("src", "rust_engine", "sf_engine.so")


def _is_target_artifact(path: str) -> bool:
    parts = {p.lower() for p in os.path.normpath(path).split(os.sep)}
    return "target" in parts


def clean_non_ssot_engine_binaries():
    """Remove duplicate Rust engine binaries outside the SSOT location."""
    src_dir = "src"
    if not os.path.isdir(src_dir):
        return

    keep_paths = {
        os.path.normcase(os.path.abspath(SSOT_ENGINE_PYD)),
        os.path.normcase(os.path.abspath(SSOT_ENGINE_SO)),
    }

    for root, _, files in os.walk(src_dir):
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
                os.remove(path)
                print(f"Removed non-SSOT engine binary: {path}")
            except Exception as e:
                print(f"Debug: Non-SSOT engine cleanup skipped for {path}: {e}")


def build_rust_engine(clean_target=False):
    """Rust 엔진(sf_engine) 컴파일 및 배포"""
    # 빌드 시작 시 기존 부산물(.old_*) 선제적 정리
    clean_old_binaries()
    clean_non_ssot_engine_binaries()

    rust_dir = os.path.join("src", "rust_engine")
    if clean_target:
        target_dir = os.path.join(rust_dir, "target")
        if os.path.exists(target_dir):
            print(f"[빌드] 기존 target 폴더 삭제 중: {target_dir}")
            try:
                shutil.rmtree(target_dir)
            except Exception as e:
                print(f"Warning: target 폴더 삭제 실패: {e}")

    print("\n--- Building Rust Engine (sf_engine) ---")
    if not os.path.exists(rust_dir):
        print(f"Error: Rust directory {rust_dir} not found.")
        sys.exit(1)

    # Python 3.14 등 상위 버전 호환성을 위한 환경 변수 설정
    env = os.environ.copy()
    env["PYO3_USE_ABI3_FORWARD_COMPATIBILITY"] = "1"

    try:
        # [OS Error 32 방어] 백그라운드 프로세스(rustc, rust-analyzer) 정리
        try:
            # sys.platform 확인 후 윈도우 환경인 경우만 실행
            if sys.platform == "win32":
                subprocess.call(
                    [
                        "powershell",
                        "-Command",
                        "Get-Process | Where-Object { $_.Path -match 'rustc|cargo|rust-analyzer' } | Stop-Process -Force",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
        except Exception as e:
            print(f"Debug: Failed to kill lock processes: {e}")

        # 1. Cargo를 사용하여 컴파일 실행 (단일 스레드 -j 1 및 5회 재시도 루프)
        cargo_cmd = ["cargo", "build", "--release", "-j", "1"]
        print(f"Running cargo: {' '.join(cargo_cmd)}")

        retry_count = 0
        max_retries = 5

        while retry_count < max_retries:
            try:
                subprocess.check_call(cargo_cmd, cwd=rust_dir, env=env)
                break
            except subprocess.CalledProcessError as e:
                retry_count += 1
                if retry_count < max_retries:
                    print(f"[경고] 빌드 실패 (OS Error 32 가능성). {retry_count}회 재시도 중... 대기 중...")
                    time.sleep(2)
                else:
                    print(f"Rust build failed after {max_retries} retries: {e}")
                    raise

        # 2. 생성된 dll을 .pyd로 변경하여 단일 경로(src/rust_engine)로 복사 (SSOT)
        src_dll = os.path.join(rust_dir, "target", "release", "sf_engine.dll")
        dst_pyd = SSOT_ENGINE_PYD

        if os.path.exists(src_dll):
            # 윈도우에서 사용 중인 .pyd 파일 교체 문제 해결
            if os.path.exists(dst_pyd):
                try:
                    os.remove(dst_pyd)
                except (PermissionError, OSError):
                    print(f"Warning: {dst_pyd} is currently in use. Attempting to rename...")
                    timestamp = int(time.time())
                    old_pyd = f"{dst_pyd}.old_{timestamp}"
                    os.rename(dst_pyd, old_pyd)
                    print(f"Renamed locked file to: {old_pyd}")

            shutil.copy2(src_dll, dst_pyd)
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
    src_dir = os.path.join("src", "rust_engine")
    if not os.path.exists(src_dir):
        return
    for file in os.listdir(src_dir):
        if file.startswith("sf_engine.pyd.old_") or ".pyd.old_" in file:
            try:
                path = os.path.join(src_dir, file)
                os.remove(path)
                print(f"Removed old binary: {file}")
            except Exception as e:
                print(f"Debug: Old binary removal skipped: {e}")


def clean_binary(clean_all=False):
    """배포된 바이너리 및 빌드 산출물 정리"""
    dst_pyd = SSOT_ENGINE_PYD
    if os.path.exists(dst_pyd):
        try:
            os.remove(dst_pyd)
            print(f"Removed binary: {dst_pyd}")
        except Exception as e:
            print(f"Error removing {dst_pyd}: {e}")

    # .old 파일 정리 호출
    clean_old_binaries()
    clean_non_ssot_engine_binaries()

    if clean_all:
        target_dir = os.path.join("src", "rust_engine", "target")
        if os.path.exists(target_dir):
            print(f"Cleaning rust target: {target_dir}")
            shutil.rmtree(target_dir, ignore_errors=True)


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
