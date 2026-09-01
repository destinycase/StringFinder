# StringFinder Engine Performance Baseline

측정일: 2026-09-01 23:45 KST  
환경: Windows, Python 3.12.9, PySide6 6.10.1, release Rust extension 5.7.2

## 측정 방법

`python tools/benchmark_engine.py`를 실행해 임시 파일을 생성하고 각 검색 경로를 1회 warm-up 후 3회 측정했다. 결과는 median wall-clock time이며, RSS delta는 각 반복 전후 프로세스 RSS 차이의 최대값이다.

## 결과

| 경로 | 파일 크기 | median | max RSS delta |
|---|---:|---:|---:|
| plain text | 1.04 MiB | 0.0079 s | 1.19 MiB |
| plain text | 33.28 MiB | 0.0078 s | 0.81 MiB |
| JSON Visitor streaming | 6.42 MiB | 0.0457 s | 0.73 MiB |
| XML streaming | 8.02 MiB | 0.0936 s | 0.18 MiB |

## 해석 및 한계

- JSON은 전체 DOM materialization 없이 처리되어 측정 RSS 증가가 낮았다.
- plain text 결과는 파일 초반에 충분한 매치가 있고 결과 상한이 적용되므로 파일 전체 처리량 benchmark로 해석하면 안 된다.
- 단일 장비·단일 패턴·합성 데이터 기준이다. 실제 운영 데이터에 대한 전후 비교, cold-cache 측정, CPU/메모리 peak 측정은 별도 수행이 필요하다.
- 재측정 명령은 `python tools/benchmark_engine.py`다.
