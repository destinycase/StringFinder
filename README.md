# String Finder

Python의 가용 자원을 최대 활용하여 극강의 검색 속도를 제공하는 문자열 검색 도구입니다.

## 주요 특징

- **초고속 텍스트 검색**: `mmap`과 바이트 수준 검색을 통해 대용량 파일도 순식간에 스캔합니다.
- **Excel 통합 검색**: `python-calamine` 엔진을 사용하여 모든 엑셀 파일(.xlsx, .xls, .xlsb) 내의 문자열을 초고속으로 탐색합니다.
- **고속 파일 스캔**: `os.scandir` 최적화와 멀티 프로세싱(`ProcessPoolExecutor`)을 결합하여 기가바이트급 데이터도 효율적으로 처리합니다.
- **스마트 인코딩 감지**: UTF-8, CP949를 우선 시도하고 `chardet`으로 보완하는 휴리스틱 인코딩 감지 기능을 탑재했습니다.
- **모던 UI 및 편의성**: `qdarktheme` 기반의 다크/라이트 모드, 탭 중심의 멀티 태스킹, 히스토리 관리 및 실시간 프리뷰를 지원합니다.

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
