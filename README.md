# String Finder

Python의 가용 자원을 최대 활용하여 극강의 검색 속도를 제공하는 문자열 검색 도구입니다.

## 주요 특징 (v3.0.0 Major Update)

- **비동기 백그라운드 파일 스캔**: 파일 탐색(Step 1) 과정을 별도 스레드에서 수행하여 UI 프리징을 완벽히 제거했습니다. [NEW]
- **초고속 텍스트 검색**: `mmap`과 바이트 수준 정규식 검색, 지연 디코딩(Lazy Decoding)을 통해 대용량 파일도 즉시 스캔합니다.
- **Excel 하이브리드 엔진**: 파일 크기에 따라 Calamine과 Openpyxl을 자동 전환하여 2GB 이상의 엑셀도 안전하게 검색합니다.
- **가상 스크롤 UI**: `Model/View` 아키텍처로 100만 건 이상의 결과도 부드럽게 스크롤 및 정렬 가능합니다.
- **실시간 결과 필터링**: `QSortFilterProxyModel`을 사용하여 수만 건의 결과 내에서 파일명 실시간 필터링을 지원합니다. [NEW]

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
