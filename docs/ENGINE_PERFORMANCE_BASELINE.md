# StringFinder Engine Performance Baseline

측정일: 2026-09-03 08:25 KST  
환경: Windows, Python 3.12.9, PySide6 6.10.1, release Rust extension 5.8.2

## 측정 방법

`python tools/benchmark_engine.py`를 3회 실행했다. 각 실행은 임시 파일을 생성하고 검색 경로별로 1회 warm-up 후 3회 측정한다. 아래 시간은 세 실행에서 얻은 median wall-clock time의 중앙값이며, RSS delta는 전체 실행 중 관측한 최대값이다.

GUI·워커 통합 경로는 `python scripts/benchmark_performance.py`로 기존 표준 A–J 데이터셋을 측정했다. 상세 행은 [벤치마크 기록](benchmark_history.md)에 누적한다.

## 결과

| 경로 | 파일 크기 | median | max RSS delta |
|---|---:|---:|---:|
| plain text | 1.04 MiB | 0.0080 s | 1.14 MiB |
| plain text | 33.28 MiB | 0.0079 s | 0.60 MiB |
| JSON Visitor streaming | 6.42 MiB | 0.1258 s | 0.59 MiB |
| XML streaming | 8.02 MiB | 0.0990 s | 0.70 MiB |

## 직전 기준선 대비

| 경로 | v5.7.2 | v5.8.2 | 변화 |
|---|---:|---:|---:|
| plain text 1.04 MiB | 0.0079 s | 0.0080 s | +1.3% |
| plain text 33.28 MiB | 0.0078 s | 0.0079 s | +1.3% |
| JSON Visitor streaming | 0.0457 s | 0.1258 s | +175.3% |
| XML streaming | 0.0936 s | 0.0990 s | +5.8% |

## 해석 및 한계

- A–J 통합 벤치마크는 모든 성능 임계치를 통과했다. 적중 수는 직전 기록과 같고 스킵은 0건이며, 전체 시간 합계는 1.530초에서 1.527초로 사실상 동일했다(-0.2%).
- 일반 텍스트와 XML 경로는 직전 기준선과 유사하다. 측정 차이가 작아 뚜렷한 회귀로 판단하지 않는다.
- JSON은 전체 DOM materialization 없이 처리되어 RSS 증가는 0.59MiB로 낮게 유지됐다.
- JSON 실행 시간은 세 번의 독립 실행에서 0.1184~0.1258초로 재현됐으며, 직전 기준선보다 2.75배 느리다. 절대 증가는 약 80ms이고 현재 임계치(5초)에는 충분한 여유가 있지만, 성능 회귀로 기록한다.
- 코드 대조상 JSON의 전체 문서 무결성 검증과 정확한 위치 보고를 위해 모든 키·스칼라를 직렬화하고 원본에서 위치를 추적하는 처리가 증가한 것이 주된 원인으로 판단된다. 손상 문서를 정상 결과로 오인하지 않는 정확성 개선의 비용이지만, 토큰별 할당과 재검색을 줄이는 최적화 여지가 있다.
- 종합 평가는 **조건부 양호**다. 기능 정확성·메모리·통합 성능은 기준을 충족하지만, 다음 성능 작업에서 JSON 위치 추적 경로를 우선 프로파일링해야 한다.
- plain text 결과는 파일 초반에 충분한 매치가 있고 결과 상한이 적용되므로 파일 전체 처리량 benchmark로 해석하면 안 된다.
- 단일 장비·단일 패턴·합성 데이터 기준이다. 실제 운영 데이터에 대한 전후 비교, cold-cache 측정, CPU/메모리 peak 측정은 별도 수행이 필요하다.
- 재측정 명령은 `python tools/benchmark_engine.py`다.
