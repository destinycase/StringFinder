# StringFinder

StringFinder는 Windows에서 텍스트와 구조화 문서를 빠르게 검색하는 데스크톱 애플리케이션입니다.

현재 버전은 **5.9.22**이며 한국어와 English UI를 제공합니다. 일반 텍스트·소스 코드뿐 아니라 JSON, XML, XLSX/XLSM/XLS/XLSB 파일의 검색 결과를 파일·위치·값 단위로 확인할 수 있습니다.

## 주요 기능

- Rust 기반 병렬 검색 엔진과 Python/PySide6 GUI
- 일반 검색, 누락 방지 검색(매우 느림), 존재만 확인 검색
- JSON·XML 구조 검색 및 Excel 시트/셀 결과 표시
- 검색 결과 필터링·정렬, 문맥 미리보기, 외부 편집기 연동
- 탭별 세션 저장·복원과 검색 결과 내보내기(TXT/XLSX)
- 한국어/English 현지화와 설정 기반 성능 진단 리포트

## 실행

배포본은 `dist/StringFinder.exe`입니다. 개발 환경에서 실행하려면 Python 3.12 이상과 Rust toolchain이 필요합니다.

```powershell
python run.py
```

## 기술 구성

| 구성 | 역할 |
| --- | --- |
| Python / PySide6 | GUI, 설정, 세션, 결과 표시·내보내기 |
| Rust / PyO3 | 파일 순회, 텍스트 검색, 구조화 문서 처리 |
| Calamine | Excel 통합 문서 읽기 |

검색 엔진은 검색 중 UI를 차단하지 않으며, 처리할 수 없는 파일은 전체 검색을 중단하지 않고 건너뛴 파일 목록과 로그에 기록합니다.

## 문서

- [사용자 가이드](docs/USER_GUIDE.md) — 화면, 검색 방식, 설정, 결과 및 문제 해결
- [개발자 가이드](docs/DEVELOPER_GUIDE.md) — 구조, 검색 계약, 현지화, 테스트와 릴리스 절차
- [성능 기준](docs/ENGINE_PERFORMANCE_BASELINE.md) — 공식 벤치마크 기준과 해석 방법
