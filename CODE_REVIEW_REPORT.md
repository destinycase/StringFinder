# StringFinder 전체 코드 리뷰 보고서

## 1. 범위

- 대상 브랜치: `main`
- 검토일: 2026-08-29
- 범위: Python UI·검색 로직·설정 관리, Rust 검색 엔진, 테스트 및 정적 검사
- 원칙: 기본 동작을 보존하고, 위험도가 높은 변경은 단계적으로 적용

## 2. 주요 발견 사항

### P1 — 청크 경계에서 검색어가 누락될 수 있음

위치: `src/core/search_engine.py`의 `_fast_existence_check()`

대용량 파일을 64KiB 단위로 읽으면서 이전 청크의 끝부분을 다음 청크에 이어 붙이지 않아, 검색어가 두 청크에 걸쳐 있으면 매치를 놓칠 수 있었습니다.

조치: 이전 청크의 끝부분을 보존하는 overlap 처리를 추가하고, 경계 조건 회귀 테스트를 추가했습니다.

### P1 — Rust 검색 경로가 일부 고급 설정을 무시함

사용자가 설정할 수 있는 파일당 최대 매치 수, Excel 셀 검사 한도, JSON 깊이 제한이 Rust 호출부에 전달되지 않아 Rust 기본값이 사용될 수 있었습니다.

조치: 해당 세 값을 단일 파일·특수 파일·디렉터리·파일 목록 검색 경로에 전달하고, 잘못된 설정값을 양의 정수로 보정했습니다. 기본값은 Rust의 기존 기본값과 동일하게 유지했습니다.

잔여 사항: Rust의 JSON DOM 크기 제한은 아직 `500MB` 하드코딩 상태입니다. API 인자 추가와 Rust API 버전 계약 변경이 필요하므로 별도 단계로 남겼습니다.

### P1 — 검색 타임아웃이 무응답 시간이 아니라 전체 경과 시간처럼 동작함

위치: `src/core/worker.py`의 `_run_batch_search()`

`loop_start_time`이 검색 루프 시작 시 한 번만 설정됩니다. 정상적으로 진행 중인 장시간 검색도 설정 시간 이후 타임아웃으로 처리될 수 있습니다.

권고: 전체 검색 시간과 마지막 진행 시점 또는 마지막 Future 완료 시점을 분리하고, `time.monotonic()` 기반으로 타임아웃을 측정합니다.

### P1 — ProcessPoolExecutor 종료 및 회수가 불완전할 수 있음

중지·타임아웃 시 `shutdown(wait=False, cancel_futures=True)`만 호출하면 이미 실행 중인 프로세스가 즉시 종료된다는 보장이 없습니다. 이전 풀이 남은 상태에서 새 검색이 시작될 가능성이 있습니다.

권고: 실행기 소유권과 retiring 상태를 분리하고, 협력적 중지·대기·강제 종료를 단계적으로 수행합니다. 정상 종료, 사용자 중지, 타임아웃을 각각 통합 테스트해야 합니다.

## 3. P2 유지보수·안정성 이슈

- `ConfigManager`가 내부 mutable 객체를 직접 노출하거나 UI가 내부 설정을 직접 수정할 가능성
- 고급 설정값 검증이 JSON 크기와 JSON 깊이 등 일부 항목에만 적용됨
- `CON`, `NUL`, `COM1`, `LPT1` 등 Windows 예약 장치명이 확장자 처리 후에도 남을 가능성
- 멀티프로세스 import 시 로그 파일을 `mode="w"`로 초기화해 파일 충돌 또는 덮어쓰기가 발생할 가능성

## 4. 유지보수성 이슈

- `search_engine.py`에 검색·인코딩·특수 파일·Rust 연동 책임이 집중되어 함수 복잡도가 높음
- `file_helper.py`, `result_view.py`에 mypy 오류 10개
- 여러 파일의 포맷 및 줄바꿈 일관성 개선 필요
- Python·Rust 테스트와 정적 검사를 CI에서 자동화할 필요

## 5. 현재까지의 수정

- `_fast_existence_check()` 청크 경계 처리 보완
- Rust 검색 API 호출부에 `max_per_file`, `max_check_cells`, `max_json_depth` 전달
- 설정 전달 계약 테스트 3개 추가
- 청크 경계 회귀 테스트 추가
- Rust API가 지원하지 않는 JSON DOM 크기, 타임아웃, ProcessPool 종료 정책은 이번 단계에서 변경하지 않음

## 6. 검증 결과

- Python 전체 테스트: `223 passed, 10 deselected`
- Ruff: 통과
- Python compileall: 통과
- Rust unit tests: `9 passed`
- mypy: 기존 오류 10개 유지 (`file_helper.py`, `result_view.py`)

## 7. 권장 후속 순서

1. Rust JSON DOM 크기 제한을 Python 설정과 통일
2. 타임아웃을 전체 시간과 무응답 시간으로 분리
3. ProcessPoolExecutor의 중지·회수·재사용 정책 개선
4. 장시간 검색, 중지 후 재검색, 타임아웃 후 재검색 통합 테스트

각 단계는 별도 커밋으로 분리해 문제 발생 시 해당 변경만 롤백할 수 있도록 진행하는 것을 권장합니다.
