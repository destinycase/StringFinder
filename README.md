# String Finder

Everything SDK와 Multiprocessing을 결합하여 극강의 검색 속도를 제공하는 문자열 검색 도구입니다.

## 주요 특징

- **고속 파일 시스템 스캔**: 다중 스레드 기반의 `os.scandir` 최적화를 통해 수만 개의 파일을 빠르게 인덱싱하고 후보를 선별합니다.
- **병렬 내용 검색**: `ProcessPoolExecutor`와 `mmap`을 활용하여 CPU 자원을 최대 활용한 초고속 문자열 검색을 수행합니다.
- **Excel 파일 지원**: 일반 텍스트 파일은 물론, `.xlsx`, `.xls` 등 엑셀 파일 내의 문자열도 정확하게 찾아냅니다.
- **탭 기반 멀티 태스킹**: 여러 검색 작업을 개별 탭에서 독립적으로 수행하며 각 탭의 상태를 보존합니다.
- **모던 UI 및 히스토리 관리**: `qdarktheme` 기반의 세련된 디자인과 콤보박스 내 개별 기록 삭제 기능 등 사용자 편의 기능을 제공합니다.
- **실시간 프리뷰**: 검색된 결과의 앞뒤 텍스트를 즉시 확인하고 기본 프로그램으로 연결할 수 있습니다.

## 설치 및 실행

### 요구사항
- Windows OS (추천)
- Python 3.12 이상

### 설치
```bash
pip install -r requirements.txt
```

### 실행
```bash
python run.py
```

## 프로젝트 구조
```
StringFinder/
├── src/
│   ├── core/       # 핵심 검색 엔진 및 필터 로직
│   ├── ui/         # PySide6 GUI 컴포넌트
│   └── utils/      # 설정 관리 및 헬퍼 함수
├── tests/          # 유닛 테스트
├── pyproject.toml  # 프로젝트 설정
└── run.py          # 엔트리 포인트
```
