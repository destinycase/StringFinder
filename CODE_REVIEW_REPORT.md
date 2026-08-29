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

상태: 완료. Rust API에 JSON 크기 제한 인자를 추가하고 API 버전을 6으로 갱신해 Python 설정과 통일했습니다.

### P1 — 검색 타임아웃이 무응답 시간이 아니라 전체 경과 시간처럼 동작함

위치: `src/core/worker.py`의 `_run_batch_search()`

`loop_start_time`이 검색 루프 시작 시 한 번만 설정됩니다. 정상적으로 진행 중인 장시간 검색도 설정 시간 이후 타임아웃으로 처리될 수 있습니다.

상태: 완료. `time.monotonic()` 기반으로 마지막 Future 진행 시점을 갱신하고, 설정값을 한 번 읽어 무응답 시간을 측정하도록 수정했습니다. 짧은 타임아웃에서 불필요하게 최대 1초를 기다리지 않도록 대기 시간도 남은 제한 시간에 맞춥니다.

### P1 — ProcessPoolExecutor 종료 및 회수가 불완전할 수 있음

중지·타임아웃 시 `shutdown(wait=False, cancel_futures=True)`만 호출하면 이미 실행 중인 프로세스가 즉시 종료된다는 보장이 없습니다. 이전 풀이 남은 상태에서 새 검색이 시작될 가능성이 있습니다.

상태: 완료. 종료 전에 `_processes` 핸들을 캡처하고, 사용자 중지·타임아웃 시 `shutdown(wait=False, cancel_futures=True)` 후 `terminate()`와 제한된 `join()`을 수행하도록 공통 종료 어댑터를 추가했습니다. worker별 executor 소유권도 추적해 동시 검색은 별도 풀을 사용하고, 정상 종료 시에만 건강한 풀을 재사용합니다. 정상 종료는 기존의 `wait=True` 동작을 유지하며, 실제 ProcessPool worker를 띄우는 통합 테스트로 회수를 확인했습니다.

## 3. P2 유지보수·안정성 이슈

- `ConfigManager`가 내부 mutable 객체를 직접 노출하거나 UI가 내부 설정을 직접 수정할 가능성
- 고급 설정값 검증이 JSON 크기와 JSON 깊이 등 일부 항목에만 적용됨
- `CON`, `NUL`, `COM1`, `LPT1` 등 Windows 예약 장치명이 확장자 처리 후에도 남을 가능성
- 멀티프로세스 import 시 로그 파일을 `mode="w"`로 초기화해 파일 충돌 또는 덮어쓰기가 발생할 가능성

## 4. 유지보수성 이슈

- `search_engine.py`에 검색·인코딩·특수 파일·Rust 연동 책임이 집중되어 함수 복잡도가 높음
- `file_helper.py`, `result_view.py`의 mypy 오류 10개
- 여러 파일의 포맷 및 줄바꿈 일관성 개선 필요
- Python·Rust 테스트와 정적 검사를 CI에서 자동화할 필요

## 5. 현재까지의 수정

- `_fast_existence_check()` 청크 경계 처리 보완
- Rust 검색 API 호출부에 `max_per_file`, `max_check_cells`, `max_json_depth` 전달
- Rust 검색 API에 `max_json_size`를 추가하고 Python의 MB 설정값을 바이트로 전달
- JSON 크기 제한 API 변경에 맞춰 Rust API 버전을 6으로 갱신
- 설정 전달 계약 테스트 3개 추가
- 청크 경계 회귀 테스트 추가
- 타임아웃을 마지막 진행 이후 무응답 시간 기준으로 수정하고 진행 갱신 회귀 테스트 추가
- ProcessPool 종료 어댑터와 실제 worker 회수 테스트 추가
- 동시 worker 간 executor 소유권 분리 및 정상 완료 시 재사용 처리

## 6. 검증 결과

- Python 전체 테스트: `230 passed, 10 deselected`
- Ruff: 통과
- Python compileall: 통과
- Rust unit tests: `9 passed`
- mypy: 통과

## 7. 권장 후속 순서

### P2 진행 상태

- `ConfigManager` getter/setter와 history 반환값의 방어적 복사 적용
- 설정 UI의 log retention 직접 수정 제거
- Windows 예약 장치명 확장자 변형(`CON.txt` 등) 보완
- 멀티프로세스 로그 파일의 프로세스별 분리 및 append 모드 적용
- 관련 회귀 테스트 추가

### 현재 남은 작업

- `search_engine.py`의 기능별 모듈 분리
- 배포 환경 반복 테스트를 CI workflow로 자동화
- mypy: 현재 전체 소스 통과

1. 장시간 검색, 중지 후 재검색, 타임아웃 후 재검색 통합 테스트를 배포 환경에서도 반복 검증

각 단계는 별도 커밋으로 분리해 문제 발생 시 해당 변경만 롤백할 수 있도록 진행하는 것을 권장합니다.
