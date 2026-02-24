import pytest
from core.search_engine import search_in_file, HAS_RUST_ENGINE

"""
[test_panic_repro.py]

Rust 엔진의 'byte index out of bounds' 패닉 이슈를 재현합니다.
보고된 이슈는 약 50MB 이상의 UTF-16 XML 파일에서 발생했습니다.
"""


@pytest.mark.skipif(not HAS_RUST_ENGINE, reason="Rust 엔진이 로드된 경우에만 패닉 테스트가 가능합니다.")
def test_panic_large_utf16_xml(tmp_path):
    # 1. 약 50MB 이상의 대용량 UTF-16 XML 생성
    xml_path = tmp_path / "large_utf16.xml"

    # 약 1MB의 조각을 만들어 60번 반복하여 60MB 이상 생성
    chunk = '<?xml version="1.0" encoding="utf-16"?><Root>' + ('<Item id="123">Content Data</Item>\n' * 30000)

    # UTF-16-LE로 인코딩 (BOM 포함)
    with open(xml_path, "wb") as f:
        f.write(b"\xff\xfe")  # BOM
        f.write(chunk.encode("utf-16-le"))
        # 패닉 발생 지점에 가까운 위치에 타겟 배치
        f.write('<Item id="target_uid">Target Dialogue Content</Item></Root>'.encode("utf-16-le"))

    # 2. 검색 수행 (일반 검색 모드, use_complex_search=False)
    # 현재 정책상 Rust 엔진이 기본적으로 XML 형식을 인식하여 처리함 (mode_bits 제어)
    try:
        # special_mode=None 이면 파일 확장자(.xml)를 보고 Rust 엔진의 XML 파서가 전담함
        result = search_in_file(str(xml_path), "target_uid", use_complex_search=False)

        # 패닉이 발생하지 않으면 통과 (수정 후 기대 상태)
        assert result is not None
        assert result[1] > 0
    except Exception as e:
        # 패닉(pyo3_runtime.PanicException)이 발생하면 테스트 실패 (재현 성공)
        if "PanicException" in str(type(e)) or "out of bounds" in str(e):
            pytest.fail(f"Rust Panic Reproduced: {e}")
        raise e
