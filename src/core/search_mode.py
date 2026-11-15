"""검색 모드 값(7.2.3).

검색 모드는 언어와 상관없는 고정 값으로 저장·판별합니다. 화면에 보이는 문구는
``AppStrings.SPECIAL_SEARCH_MENU_ITEMS``를 표시할 때만 붙입니다.

값은 ``"Normal"``(기본)과 ``"<형식>"``·``"<형식> exact"``이며 형식은 XML·JSON·Excel·archive입니다.
"""
from typing import Any, Optional

NORMAL = "Normal"
EXACT = "exact"
KIND_XML = "XML"
KIND_JSON = "JSON"
KIND_EXCEL = "Excel"
KIND_ARCHIVE = "archive"
KINDS = (KIND_XML, KIND_JSON, KIND_EXCEL, KIND_ARCHIVE)

# 화면 메뉴 문구(SPECIAL_SEARCH_MENU_ITEMS)와 같은 순서입니다.
VALUES = (
    NORMAL,
    KIND_XML, f"{KIND_XML} {EXACT}",
    KIND_JSON, f"{KIND_JSON} {EXACT}",
    KIND_EXCEL, f"{KIND_EXCEL} {EXACT}",
    KIND_ARCHIVE, f"{KIND_ARCHIVE} {EXACT}",
)


def make(kind: Optional[str], exact: bool = False) -> str:
    if kind not in KINDS:
        return NORMAL
    return f"{kind} {EXACT}" if exact else kind


def normalize(value: Any) -> str:
    """어떤 모드 표기든 고정 값으로 바꿉니다. 모르는 값은 기본입니다.

    고정 값과, 사용자가 손으로 고친 입력·예전 문구처럼 형식 이름과 "정확"/"exact"가 든 글자를 받습니다.
    """
    if not isinstance(value, str):
        return NORMAL
    text = value.strip()
    if text in VALUES or text == EXACT:
        return text
    exact = "정확" in text or EXACT in text.casefold()
    # 형식 이름은 고정 값과 같은 대소문자로 찾습니다(예전 판별과 같음).
    for kind in (KIND_XML, KIND_JSON, KIND_EXCEL, KIND_ARCHIVE):
        if kind in text:
            return make(kind, exact)
    # 형식 없이 "정확히 일치"만 있으면 일반 텍스트 정확히 일치입니다.
    return EXACT if exact else NORMAL


def kind(value: Any) -> Optional[str]:
    """형식(XML·JSON·Excel·archive) 또는 기본이면 None."""
    normalized = normalize(value)
    if normalized in (NORMAL, EXACT):
        return None
    return normalized.split(" ", 1)[0]


def is_exact(value: Any) -> bool:
    normalized = normalize(value)
    return normalized == EXACT or normalized.endswith(f" {EXACT}")


def is_normal(value: Any) -> bool:
    """형식이 없는(일반 텍스트) 검색인지입니다. 일반 텍스트 정확히 일치도 포함합니다."""
    return kind(value) is None


def menu_label(value: Any) -> str:
    """지금 언어의 메뉴 문구입니다."""
    from sf_utils.app_strings import AppStrings

    normalized = normalize(value)
    return AppStrings.SPECIAL_SEARCH_MENU_ITEMS[VALUES.index(NORMAL if normalized == EXACT else normalized)]
