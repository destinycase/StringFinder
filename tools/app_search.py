"""예전 경로 호환용: 실제 코드는 `src/core/app_search.py`에 있습니다(검수 D-5).

화면 코드가 `tools/`를 가져다 쓰지 않도록 제품 모듈로 옮겼습니다. 이 파일은 도구·명령줄에서
예전처럼 `tools.app_search`로 가져다 쓰거나 직접 실행할 수 있게 같은 모듈을 그대로 가리킵니다.
"""
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[1] / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from core import app_search as _module  # noqa: E402

sys.modules[__name__] = _module
