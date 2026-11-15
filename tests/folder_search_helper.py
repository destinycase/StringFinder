"""
[folder_search_helper.py]

테스트에서 파일 하나를 앱과 같은 폴더 검색 경로(`search_directory_fast`)로 검색합니다.

앱은 폴더 검색만 쓰므로, 예전 파일 단위 검색 함수(`search_in_file` 등)를 쓰던 테스트는 이 도우미로
같은 결과 형식을 받습니다.

- 일치가 있으면 ``(경로, 일치 수, 일치 목록)``
- 건너뛰었으면 ``(Constants.STATUS_SKIPPED, 사유)``
- 일치가 없으면 ``None``

폴더 검색은 결과가 있는 파일의 부분 검색 안내(예: 해석하지 못한 바이트)를 건너뛴 목록에 따로
보냅니다. 도우미는 예전 형식처럼 이 안내를 일치 목록 끝에 ``(-2, 안내, None, None)`` 행으로 붙이고,
원래 건너뛴 목록은 ``last_skipped``에 남깁니다.
"""

import os
from typing import Any, List, Optional, Tuple

from core.search_engine import EXCEL_EXTS, render_skip_reason, search_directory_fast
from sf_utils.constants import Constants

last_skipped: List[Tuple[str, str]] = []


def search_one_file(
    file_path: str,
    search_string: str,
    special_mode: Optional[str] = None,
    stop_event: Any = None,
    exclude_binary: bool = False,
    **kwargs: Any,
) -> Optional[Tuple[Any, ...]]:
    """파일 하나를 폴더 검색 경로로 검색해 예전 파일 단위 결과 형식으로 돌려줍니다."""
    kwargs.pop("file_size", None)
    response = search_directory_fast(
        [str(file_path)],
        search_string,
        special_mode=special_mode,
        stop_event=stop_event,
        exclude_binary=exclude_binary,
        **kwargs,
    )
    # 엔진 연결은 사유를 코드로 돌려줍니다(7.2.5). 도우미는 사용자가 보는 문장으로 바꿔 돌려줍니다.
    response = {**response, "skipped": [(path, render_skip_reason(reason)) for path, reason in response["skipped"]]}
    last_skipped[:] = list(response["skipped"])
    if response["results"]:
        path, count, matches = response["results"][0]
        # 예전 파일 단위 형식처럼 같은 파일의 부분 검색 안내를 마지막 행(-2)으로 붙입니다.
        notices = [(-2, reason, None, None) for skipped_path, reason in response["skipped"] if skipped_path == path]
        return (path, count, list(matches) + notices)
    if response["skipped"]:
        return (Constants.STATUS_SKIPPED, response["skipped"][0][1])
    return None


def search_in_file(
    file_path: str,
    search_string: str,
    file_size: Optional[int] = None,
    special_mode: Optional[str] = None,
    stop_event: Any = None,
    **kwargs: Any,
) -> Optional[Tuple[Any, ...]]:
    """예전 ``search_in_file``과 같은 인수로 폴더 검색 경로를 부릅니다.

    예전 함수처럼 Excel 파일은 앱의 Excel 검색 모드로 검색합니다.
    """
    if os.path.splitext(str(file_path))[1].lower() in EXCEL_EXTS:
        if not special_mode or Constants.MODE_EXCEL not in special_mode:
            exact = bool(special_mode and Constants.MODE_EXCEL in special_mode) or bool(
                special_mode and Constants.MODE_EXACT in special_mode
            )
            special_mode = f"{Constants.MODE_EXCEL}|{Constants.MODE_EXACT}" if exact else Constants.MODE_EXCEL
    return search_one_file(file_path, search_string, special_mode=special_mode, stop_event=stop_event, **kwargs)


def _special(mode: str):
    def search(file_path: str, search_string: str, exact_match: bool = False, stop_event: Any = None):
        special_mode = f"{mode}|{Constants.MODE_EXACT}" if exact_match else mode
        return search_one_file(file_path, search_string, special_mode=special_mode, stop_event=stop_event)

    return search


search_in_json_special = _special(Constants.MODE_JSON)
search_in_xml_special = _special(Constants.MODE_XML)
search_in_archive_special = _special(Constants.MODE_ARCHIVE)


def search_in_excel_special(file_path: str, search_string: str, exact_match: bool = False, stop_event: Any = None):
    special_mode = f"{Constants.MODE_EXCEL}|{Constants.MODE_EXACT}" if exact_match else Constants.MODE_EXCEL
    return search_one_file(file_path, search_string, special_mode=special_mode, stop_event=stop_event)


class FakeSearchMatch:
    """엔진의 결과 객체(``SearchMatch``)를 흉내 냅니다. 종류·코드·상세는 엔진(``types.rs``)과 같은 규칙으로 정합니다."""

    def __init__(self, line, content, offset=None, length=None):
        self.line, self.content, self.offset, self.length = line, content, offset, length
        self.kind, self.code, self.detail = _classify(str(content))

    def __iter__(self):  # 예전 튜플처럼 풀어 쓰는 시험을 위해
        return iter((self.line, self.content, self.offset, self.length))


def _classify(content: str):
    if not content.startswith(("__SF_", "ERR_")):
        return "match", None, None
    if content == "__SF_TRUNCATED__":
        return "truncated", "TRUNCATED", None
    for prefix, kind, code in (
        ("__SF_JSON_DEPTH_LIMIT__|", "partial", "JSON_DEPTH_LIMIT"),
        ("__SF_BINARY_MATCH__|", "binary", "BINARY"),
        ("__SF_LONG_LINE__|", "long_line", "LONG_LINE"),
        ("__SF_DECODE_PARTIAL__|", "partial", "DECODE_PARTIAL"),
        ("__SF_EXCEL_SHEET_ERR__|", "sheet_error", "EXCEL_SHEET_ERROR"),
        ("__SF_EXCEL_PANIC__|", "error", "ERR_EXCEL_PANIC"),
    ):
        if content.startswith(prefix):
            return kind, code, content[len(prefix):]
    code, sep, detail = content.partition("|")
    if sep and code.startswith("ERR_"):
        return "error", code, detail
    return "match", None, None


def fake_match(item):
    """``(줄, 내용, 오프셋, 길이)`` 튜플을 가짜 결과 객체로 바꿉니다(이미 객체면 그대로)."""
    if isinstance(item, (tuple, list)):
        values = list(item) + [None] * (4 - len(item))
        return FakeSearchMatch(*values[:4])
    if not hasattr(item, "kind"):  # 줄·내용 속성만 가진 시험용 객체
        return FakeSearchMatch(getattr(item, "line", 1), getattr(item, "content", ""),
                               getattr(item, "offset", None), getattr(item, "length", None))
    return item


def fake_matches(items):
    return [fake_match(item) for item in items]


class FakeSearchOptions:
    def __init__(self, **values):
        self.__dict__.update(values)


def fake_dir_engine(matches: Any = None, skipped: Any = None, error: Optional[BaseException] = None,
                    path: Optional[str] = None) -> Any:
    """``search_dir``만 가진 가짜 엔진을 만듭니다. 첫 검색 대상의 결과로 ``matches``를 돌려줍니다."""

    class FakeEngine:
        SearchOptions = FakeSearchOptions
        SearchMatch = FakeSearchMatch

        def search_dir(self, root_paths, pattern, options=None, **_kwargs):
            if error is not None:
                raise error
            results = [(path or root_paths[0], [fake_match(m) for m in matches])] if matches is not None else []
            return results, list(skipped or [])

    return FakeEngine()


def rendered_skips(skipped):
    """``[(경로, 사유 코드)]``를 사용자가 보는 ``[(경로, 문장)]``으로 바꿉니다(7.2.5)."""
    return [(path, render_skip_reason(reason)) for path, reason in skipped]
