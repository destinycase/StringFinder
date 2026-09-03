# StringFinder Engine Performance Baseline

측정일: 2026-09-03 23:21 KST
환경: Windows, Python 3.12.9, PySide6 6.10.1, release Rust extension 5.8.3

## 측정 방법

`python tools/benchmark_engine.py`를 3회 실행했다. 각 실행은 임시 파일을 생성하고 검색 경로별로 1회 warm-up 후 3회 측정한다. 아래 시간은 세 실행에서 얻은 median wall-clock time의 중앙값이며, RSS delta는 전체 실행 중 관측한 최대값이다.

GUI·워커 통합 경로는 `python scripts/benchmark_performance.py`로 기존 표준 A–J 데이터셋을 측정했다. 상세 행은 [벤치마크 기록](benchmark_history.md)에 누적한다.

## 결과

| 경로 | 파일 크기 | median | max RSS delta |
|---|---:|---:|---:|
| plain text | 1.04 MiB | 0.0105 s | 1.00 MiB |
| plain text | 33.28 MiB | 0.0103 s | 0.52 MiB |
| JSON 반복 매치 | 6.42 MiB | 0.0428 s | 0.61 MiB |
| JSON 희소 매치 | 6.42 MiB | 0.0403 s | 0.02 MiB |
| JSON 존재 여부 | 6.42 MiB | 0.0322 s | 0.04 MiB |
| JSON 무매치 폴백 | 6.42 MiB | 0.2547 s | 3.09 MiB |
| XML streaming | 8.02 MiB | 0.1056 s | 0.39 MiB |

## JSON 최적화 전후 비교

| 경로 | v5.7.2 | v5.8.2 최적화 전 | v5.8.3 최적화 후 | 최적화 전 대비 |
|---|---:|---:|---:|---:|
| JSON Visitor streaming | 0.0457 s | 0.1258 s | 0.0428 s | -66.0% |

## 해석 및 한계

- v5.8.3 A–J 통합 벤치마크는 모든 성능 임계치를 통과했다. 적중 수는 직전 기록과 같고 스킵은 0건이며, 전체 시간 합계는 직전 1.592초에서 1.386초로 줄었다. 다만 A–J의 Set F는 JSON 특수 검색 모드가 아니므로 아래 JSON Visitor 측정과 구분해야 한다.
- 일반 텍스트와 XML의 절대 측정 차이는 수 ms 수준이다. 이번 변경 경로가 아니며 실행 환경 변동 범위로 판단한다.
- 매치가 있는 JSON 경로는 전체 DOM materialization 없이 처리되며 RSS delta가 최대 0.61MiB로 낮게 유지됐다.
- JSON 반복 매치 실행 시간은 세 번의 독립 실행에서 0.0416~0.0447초였다. 중앙값 0.0428초는 최적화 전 0.1258초보다 66.0% 짧고, v5.7.2 기준선 0.0457초보다도 6.3% 짧다.
- 같은 합성 JSON에서 마지막 객체에만 일치하는 희소 검색은 0.0403초, 존재 여부 검색은 0.0322초였다. 반복 일치와 결과 상한에만 의존한 개선이 아님을 확인했다.
- 무매치 항목은 Rust 검색 뒤 Python 정밀 폴백까지 수행하는 실제 애플리케이션 경로이며 중앙값은 0.2547초다. 순수 Rust streaming 수치와 직접 비교하지 않는다.
- 모든 키·스칼라를 직렬화한 뒤 원본에서 반복 검색하던 위치 추적을 단일 순차 토큰 커서로 교체했다. 존재 확인 모드와 결과 상한 도달 이후에는 위치·매칭 계산을 생략하되, 손상 문서 검출을 위한 전체 구문 검증은 계속한다.
- 종합 평가는 **양호**다. JSON 위치 정확성과 손상 문서 검증을 유지하면서 확인된 실행 시간 회귀를 해소했다.
- plain text 결과는 파일 초반에 충분한 매치가 있고 결과 상한이 적용되므로 파일 전체 처리량 benchmark로 해석하면 안 된다.
- 단일 장비·단일 패턴·합성 데이터 기준이다. 실제 운영 데이터에 대한 전후 비교, cold-cache 측정, CPU/메모리 peak 측정은 별도 수행이 필요하다.
- 재측정 명령은 `python tools/benchmark_engine.py`다.
