# StringFinder

**StringFinder**는 대용량 로그 파일, 소스 코드, 엑셀 문서 등 다양한 데이터에서 원하는 문자열을 신속하고 정확하게 찾아주는 **전문가용 고성능 검색 도구**입니다. Rust 엔진의 강력한 성능과 Python의 유연성을 결합하여 최상의 사용자 경험을 제공합니다.

---

## 👩‍💻 사용자 가이드 (For Users)

### 🚀 주요 특징
1. **압도적인 속도**: Rust 기반 하이브리드 엔진과 멀티프로세싱 기술로 대용량 파일도 순식간에 검색합니다.
2. **최상의 안정성 및 무결성**: 
    - **실행기 자동 복구**: 프로세스 풀 오류 발생 시 자동으로 재생성하여 연속적인 검색을 보장합니다.
    - **고성능 하이브리드 캐시 검증**: 대규모 트리에서도 0.002s 내외의 초고속 변경 감지를 수행하는 지능형 캐시 시스템을 탑재했습니다.
    - **통계 및 검색 무결성**: 하이브리드 엔진 검색 시 모든 결과 파일 수와 스킵 카운트를 정밀하게 합산하여 100% 무결성을 보장합니다.
3. **편리한 사용성**: 다크 모드, 탭(Tab) 기반 UI, 실시간 결과 미리보기, 단일 인스턴스 실행 기능을 제공합니다.
4. **다양한 지원 포맷**: 텍스트(.txt, .log), 소스 코드(.py, .java), 데이터(.json, .xml)는 물론 **엑셀(.xlsx)**까지 특수 모드로 지원합니다.

### 📥 설치 및 실행
특별한 설치 과정 없이 배포된 실행 파일(`StringFinder.exe`)을 더블 클릭하면 바로 실행됩니다. 중복 실행 방지 기능으로 언제나 하나의 창에서 관리됩니다.

### 💡 사용 팁
- **검색 모드**:
    - `일반`: 대소문자를 구분하지 않고 빠르게 검색합니다.
    - `정확히 일치`: 입력한 문자열과 정확히 일치하는(Case-Folded) 내용을 찾습니다.
    - `정규식`: 정규표현식(Regex)을 사용하여 복잡한 패턴을 검색할 수 있습니다.
- **단축키**:
    - `Ctrl + F`: 검색창으로 포커스 이동
    - `Enter`: 검색 시작
    - `Ctrl + W`: 현재 탭 닫기
    - `Alt + Shift + Space`: 프로그램 활성화 (전역 단축키)

---

## 👨‍🔧 개발자 가이드 (For Developers)

### 🛠️ 개발 환경 설정
본 프로젝트는 **Python 3.14** 및 **Rust** 환경에서 개발되었습니다.

1. **필수 요구사항**:
    - Python 3.12+ (3.14 권장)
    - Rust (latest stable)
    - Visual C++ Build Tools (Windows)

2. **설치**:
    ```bash
    git clone https://github.com/your-repo/StringFinder.git
    cd StringFinder
    pip install -e ".[dev]"
    ```
    * `maturin`을 통해 Rust 확장이 자동으로 빌드됩니다.

### 🧪 테스트 및 검증
프로젝트의 안정성을 보장하기 위해 엄격한 테스트 슈트가 마련되어 있습니다.

- **전체 테스트 실행**:
    ```bash
    pytest
    ```
- **성능 벤치마크**:
    ```bash
    pytest tests/test_performance_benchmarks.py
    ```

### 🏗️ 빌드 (Build)
단일 실행 파일(.exe)로 배포하려면 다음 명령어를 사용합니다. `PyInstaller`를 기반으로 최적화된 빌드를 수행합니다.

```bash
python build.py
```
빌드 결과물은 `dist/` 디렉토리에 생성됩니다.

### 🏗️ 기술 스택 (Tech Stack)
| 분류 | 기술 | 설명 |
| :--- | :--- | :--- |
| **Language** | Python 3.14 | 전체 로직 및 UI 제어 |
| **Accelerator** | Rust (Maturin/PyO3) | 고성능 검색 엔진 가속화 |
| **GUI** | PySide6 (Qt 6) | 크로스 플랫폼 네이티브 UI |
| **Concurrency** | Multiprocessing | CPU 바운드 작업 병렬 처리 |
| **I/O** | Memory Mapping (mmap) | 대용량 파일 초고속 읽기 |
| **Caching** | Hybrid Cache v3 | LRU + 재귀적 시그너처 + 원자적 쓰기 |
| **Testing** | Pytest / Benchmark | 143개 이상의 자동화 테스트 |
| **Build** | PyInstaller | 단일 실행 파일(.exe) 빌드 |

### 📂 프로젝트 구조 (Project Structure)
```
StringFinder/
├── src/                 # 소스 코드
│   ├── assets/          # 아이콘 및 리소스
│   ├── core/            # 핵심 로직 (Engine, Worker, Cache)
│   ├── rust_engine/     # Rust 가속 엔진 소스 코드
│   ├── sf_utils/        # 유틸리티 (Logger, Config, Instance Control)
│   ├── ui/              # PySide6 UI 컴포넌트
│   ├── sf_main.py       # 애플리케이션 진입점
│   └── sf_engine.pyd    # 컴파일된 Rust 확장 모듈
├── tests/               # 유닛 테스트 및 통합 테스트
├── build.py             # PyInstaller 빌드 스크립트
├── pyproject.toml       # 프로젝트 설정 및 의존성
└── README.md            # 프로젝트 문서
```

---

## 📜 라이선스
- **License**: Private / Proprietary

---

## 📅 Changelog

### v4.34.0 (2026-02-19)
- **Features**:
    - **JSON/XML 구조적 검색 가속**: Rust 엔진 기반의 고성능 JSON/XML 파싱 검색 기능을 일괄 검색(Batch Search) 및 스마트 스캔에도 전면 도입했습니다.
    - **미리보기 정밀도 개선**: '앵커 기반 줄 추적(Anchor-based Line Tracking)' 기술을 통해 미리보기 화면의 줄 번호와 하이라이트 위치를 100% 정밀하게 일치시켰습니다.
- **Stability**:
    - **내결함성 강화**: Excel(.xls) 파일 처리 중 발생하는 라이브러리 내부 패닉(Panic)을 엔진 레벨에서 캡처하여 전체 프로세스 강제 종료를 방지했습니다.
    - **코드 정리**: 잔여 하드코딩 문자열을 `AppStrings`로 통합하고 미사용 디버깅 파일 및 코드를 정리했습니다.

### v4.33.3 (2026-02-18)
- **Refactoring**:
    - `AppStrings` 클래스의 하드코딩된 문자열을 모두 상수화하고 카테고리별로 재정렬하여 가독성과 유지보수성을 개선했습니다.
- **Cache Integrity**:
    - 캐시 로드 시 파일 존재 여부 및 데이터 구조를 검증하여 유효하지 않은 엔트리를 자동으로 제거하도록 수정했습니다.
    - 캐시 키 생성 시 `casefold()`를 적용하여 검색 엔진과 동일한 유니코드 정규화 기준을 따르도록 통일했습니다.
- **Quality Assurance**:
    - `pytest` 수집 대상에 `audit_*.py` 및 `repro_*.py`를 포함하여 테스트 커버리지를 확대했습니다.
