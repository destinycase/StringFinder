# StringFinder

**StringFinder**는 개발자와 파워 유저를 위해 설계된 고성능 문자열 검색 도구입니다. Rust 엔진의 속도와 Python의 유연성을 결합하여 대용량 로그 파일, 소스 코드, 그리고 엑셀 파일까지 신속하게 검색할 수 있습니다.

## 🚀 주요 기능 (Key Features)

### 1. 강력한 검색 성능
- **Hybrid Engine**: 순수 텍스트 검색에는 **Rust 엔진**을 사용하여 네이티브 수준의 속도를 제공하며, 복잡한 로직은 Python으로 유연하게 처리합니다.
- **Multiprocessing**: 멀티코어 CPU를 적극 활용하여 다수의 파일을 병렬로 검색합니다.
- **Memory Mapping (mmap)**: 대용량 파일도 메모리 부하 없이 초고속으로 읽어들입니다.

### 2. 광범위한 인코딩 및 포맷 지원
- **스마트 인코딩 감지**: UTF-8(BOM 포함), **UTF-16LE**, CP949(EUC-KR) 등을 자동으로 판별하여 한글 깨짐 없이 검색합니다.
- **다양한 파일 포맷**:
  - 일반 텍스트 및 소스 코드 (.txt, .log, .py, .java, .cpp 등)
  - 구조화된 데이터 (**XML**, **JSON**)
  - **엑셀 파일** (.xlsx, .xlsm, .xls) - *Calamine 라이브러리 기반 고속 파싱*

### 3. 사용자 중심 UI/UX
- **탭(Tab) 기반 인터페이스**: 여러 검색 작업을 동시에 진행하고 관리할 수 있습니다.
- **다크 모드**: 장시간 작업에도 눈이 편안한 모던 다크 테마를 기본 지원합니다.
- **실시간 미리보기 & 하이라이팅**: 검색된 키워드를 즉시 확인하고 강조 표시합니다.
- **세션 관리**: 작업 중인 탭 상태를 자동으로 저장하고 복구합니다.

### 4. 고급 필터링 및 보안
- **정교한 필터링**: 파일명 패턴(Glob), 확장자, 폴더 제외 설정 등을 지원합니다.
- **입력값 정규화**: 파일명 및 세션 저장 시 특수문자를 자동으로 처리하여 시스템 안정성을 보장합니다.

---

## 🛠️ 기술 스택 (Tech Stack)

본 프로젝트는 안정성과 성능을 위해 다음 기술들을 기반으로 구축되었습니다.

| 분류 | 기술 | 비고 |
| :--- | :--- | :--- |
| **Core Language** | **Python 3.14** | 최신 Python 런타임 |
| **Performance** | **Rust** (pyo3/maturin) | 핵심 검색 엔진 가속화 |
| **GUI Framework** | **PySide6** (Qt 6) | 크로스 플랫폼 데스크톱 UI |
| **Concurrency** | `multiprocessing`, `concurrent.futures` | 병렬 처리 구조 |
| **Optimization** | `mmap` | 메모리 매핑 파일 I/O |
| **Data Parsing** | `python-calamine`, `openpyxl` | 고성능 엑셀 파싱 |
| **Validation** | `chardet` | 인코딩 분석 |
| **Code Quality** | `ruff`, `mypy`, `pytest` | 린팅, 정적 분석 및 테스트 |
| **Distribution** | `PyInstaller` | 실행 파일 빌드 |
