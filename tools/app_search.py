"""벤치마크·성능 진단 도구가 앱과 같은 검색 경로로 파일 하나를 검색하는 도우미입니다.

앱은 폴더 검색 함수(`search_directory_fast`)만 씁니다. 파일 하나를 검색 대상으로 넘기면
Rust 엔진이 그 파일만 검색합니다.
"""

import sys
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from core.search_engine import EXCEL_EXTS, search_directory_fast  # noqa: E402
from sf_utils.constants import Constants  # noqa: E402


def search_file_like_app(
    path: str,
    query: str,
    special_mode: Optional[str] = None,
    exclude_binary: bool = False,
    stop_event: Any = None,
) -> Optional[tuple]:
    """파일 하나를 앱과 같은 폴더 검색 함수로 검색합니다.

    돌려주는 값: 일치가 있으면 (경로, 일치 수, 일치 목록), 건너뛰었으면 (SKIPPED, 사유), 없으면 None.
    """
    response = search_directory_fast(
        [str(path)], query, special_mode=special_mode, exclude_binary=exclude_binary, stop_event=stop_event
    )
    if response["results"]:
        return tuple(response["results"][0])
    if response["skipped"]:
        return (Constants.STATUS_SKIPPED, response["skipped"][0][1])
    return None


def excel_mode(exact: bool = False) -> str:
    """앱의 Excel 검색 모드 문자열입니다."""
    return f"{Constants.MODE_EXCEL}|{Constants.MODE_EXACT}" if exact else Constants.MODE_EXCEL


__all__ = ["EXCEL_EXTS", "excel_mode", "search_file_like_app"]
