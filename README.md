# StringFinder

**Production-Ready 고성능 문자열 검색 도구**

Python의 가용 자원을 최대 활용하여 극강의 검색 속도를 제공하는 문자열 검색 유틸리티입니다.

[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-57%20passed-brightgreen.svg)](tests/)

---

## ✨ 주요 기능

### 🚀 성능 최적화
- **mmap 기반 검색**: 대용량 파일(GB급)도 전체 메모리 로드 없이 고속 처리
- **멀티프로세싱**: CPU 코어 수에 따라 자동 확장되는 병렬 검색 아키텍처
- **Lazy Loading 미리보기**: 500MB+ 파일도 2초 이내 즉시 열람
- **배치 처리**: IPC 오버헤드 최소화로 대량 파일 검색 최적화

### 🛡️ 안정성
- **포괄적 예외 처리**: JSON 재귀 제한, 인코딩 오류 자동 복구
- **자동 인코딩 감지**: UTF-8, CP949(EUC-KR) 자동 판별로 한글 깨짐 방지

### 🎯 특수 기능
- **XML/JSON 특수 검색**: 주석 제외, 구조화된 데이터(Value)만 검색
- **Excel 하이브리드 엔진**: 파일 크기별 최적 라이브러리 자동 전환
- **실시간 필터링**: 수만 건 결과 내 파일명/폴더명 즉시 필터링
- **가상 스크롤 UI**: 100만 건 이상 결과도 부드러운 스크롤/정렬

---

## 🚀 설치 및 실행

### 요구사항
- **OS**: Windows 10/11 (권장)
- **Python**: 3.12 이상

### 설치
```bash
# 기본 설치
pip install -r requirements.txt

# 개발 환경 설치 (테스트 포함)
pip install -e ".[dev]"
```

### 실행
```bash
python run.py
```

### 테스트 실행
```bash
pytest tests/
```

---

## 📁 프로젝트 구조

```
StringFinder/
├── src/
│   ├── core/          # 검색 엔진 (mmap, 멀티프로세싱)
│   │   ├── search_engine.py
│   │   └── worker.py
│   ├── ui/            # PySide6 GUI 컴포넌트
│   │   ├── search_tab.py
│   │   ├── models.py
│   │   └── widgets.py
│   └── utils/         # 설정 관리 및 헬퍼
│       ├── config_manager.py
│       └── logger.py
├── tests/             # 57개 테스트 케이스
├── pyproject.toml     # 프로젝트 메타데이터
├── requirements.txt   # 의존성 목록
└── run.py             # 엔트리 포인트
```

---

## 🔧 기술 스택

- **UI Framework**: PySide6 (Qt 6.10+)
- **검색 엔진**: mmap + regex (바이트 레벨)
- **병렬 처리**: ProcessPoolExecutor (멀티프로세싱)
- **Excel 처리**: python-calamine + openpyxl
- **테스트**: pytest + pytest-qt

---

## 📝 버전 히스토리

### v3.6.0 (2026-02-12) [Latest]
- ✅ **Lazy Loading 미리보기**: 대용량 파일 즉시 열람
- ✅ **테스트 커버리지 확대**: 57개 테스트 (UI, 유틸리티 포함)
- ✅ **안정성 강화**: JSON 재귀 제한, 인코딩 자동 감지
- ✅ **성능 최적화**: Regex 캐싱, UI Throttling

### v3.5.1
- 비동기 백그라운드 파일 스캔
- XML/JSON 특수 검색 모드
- 고해상도 아이콘 빌드

---

## 📄 라이선스

MIT License

---

## 🤝 기여

이슈 및 PR은 언제나 환영합니다!

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

**Made with ❤️ by N2**
