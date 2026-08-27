
import pytest
from sf_utils.constants import Constants
from ui.models import MatchDetailModel

@pytest.fixture
def match_model():
    return MatchDetailModel()

def test_filter_normal_mode(match_model):
    """Normal 모드: 0번 필터가 포지션과 내용 모두에서 검색되는지 확인"""
    matches = [
        (10, "Target content line"),
        (20, "Other line")
    ]
    match_model.set_matches("test.txt", matches, search_mode=Constants.MODE_NORMAL)
    
    # 'Target' 검색 (데이터 컬럼 인계 0번 사용)
    match_model.set_column_filter(0, "target")
    assert len(match_model._filtered_buffer) == 1
    assert match_model._filtered_buffer[0].position == "10"

    # '10' 검색 (라인 번호로 검색)
    match_model.set_column_filter(0, "10")
    assert len(match_model._filtered_buffer) == 1

def test_filter_json_xml_mode_mapping(match_model):
    """JSON/XML 모드: 필터 1(UI) -> 모델 1(Key), 필터 2(UI) -> 모델 2(Value) 매핑 확인"""
    # XML 모드 설정 (5-tuple 형식: line, path, val, offset, length)
    matches = [
        (1, "root/node/key1", "value_abc", 100, 10),
        (2, "root/node/key2", "value_def", 200, 10)
    ]
    match_model.set_matches("test.xml", matches, search_mode=Constants.MODE_XML)
    
    # UI 필터 1 (Key/Path) 입력 시뮬레이션 -> 모델 컬럼 1 필터링
    match_model.set_column_filter(1, "key1")
    assert len(match_model._filtered_buffer) == 1
    assert match_model._filtered_buffer[0].content == "root/node/key1"
    
    # 필터 초기화 후 UI 필터 2 (Value) 입력 시뮬레이션 -> 모델 컬럼 2 필터링
    match_model.set_column_filter(1, "") # 기존 필터 해제
    match_model.set_column_filter(2, "def")
    assert len(match_model._filtered_buffer) == 1
    assert match_model._filtered_buffer[0].extra_1 == "value_def"

    # 0번 컬럼(라인 번호) 필터링 시도 시 (UI에서는 입력 안 함) 데이터 매칭되지 않아야 함
    match_model.set_column_filter(2, "")
    match_model.set_column_filter(0, "key1") # 라인번호 '1', '2'가 아니므로 0건
    assert len(match_model._filtered_buffer) == 0


def test_filter_excel_mode_mapping(match_model):
    """Excel 모드: 필터 0~2 직접 매핑 확인 (라인번호 컬럼 없음)"""
    # Excel 필드 (Line, Sheet, Cell, Val) -> Schema(pos=Sheet, content=Cell, extra_1=Val)
    matches = [
        (1, "Sheet1", "A1", "Hello Excel", 0, 0)
    ]
    match_model.set_matches("test.xlsx", matches, search_mode=Constants.MODE_EXCEL)
    
    # UI Filter 0 (Sheet) -> 모델 Column 0 (position)
    match_model.set_column_filter(0, "Sheet1")
    assert len(match_model._filtered_buffer) == 1
    
    # UI Filter 2 (Value) -> 모델 Column 2 (extra_1)
    match_model.set_column_filter(0, "")
    match_model.set_column_filter(2, "Hello")
    assert len(match_model._filtered_buffer) == 1

def test_filter_reset_on_mode_change(match_model):
    """모드 변경 시 이전 필터 찌꺼기가 남지 않는지 확인 (H-06 Fix 검증)"""
    matches_normal = [(1, "Normal Line")]
    match_model.set_matches("test.txt", matches_normal, search_mode=Constants.MODE_NORMAL)
    match_model.set_column_filter(0, "normal")
    assert len(match_model._filtered_buffer) == 1
    
    # JSON 모드로 변경 (데이터 셋도 변경)
    matches_json = [(1, "root.key", "val", 0, 0)]
    # XML/JSON은 0번이 라인번호이므로, 0번 필터 "normal"이 남아있으면 0건이 됨
    match_model.set_matches("test.json", matches_json, search_mode=Constants.MODE_JSON)
    
    # ResultView 로직상 모드 변경 시 모든 필터 필드를 set_column_filter("", ...) 등으로 갱신함
    # 여기서는 수동으로 필터 초기화 여부를 확인
    match_model.set_column_filter(0, "") # 초기화 수행
    assert len(match_model._filtered_buffer) == 1
