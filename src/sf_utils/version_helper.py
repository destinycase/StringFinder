import os
import tomllib


def get_app_version() -> str:
    """
    애플리케이션 버전을 읽어옵니다.
    _version.py를 우선적으로 읽으며, 없을 경우 pyproject.toml에서 로드합니다.
    """
    # 1. _version.py 우선 시도 (빌드된 환경 또는 명시적 생성 시)
    try:
        from . import _version

        return _version.VERSION
    except (ImportError, AttributeError):
        pass

    # 2. pyproject.toml 시도 (개발 환경)
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        project_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
        toml_path = os.path.join(project_root, "pyproject.toml")
        if os.path.exists(toml_path):
            with open(toml_path, "rb") as f:
                data = tomllib.load(f)
                version = data.get("project", {}).get("version")
                if isinstance(version, str):
                    return version
    except Exception:
        pass

    return "0.0.0-unknown"  # 버전 정보 상실 시 알 수 없음으로 표시
