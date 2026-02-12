# 🔍 StringFinder v3.7.4

**StringFinder**는 Python과 Rust의 하이브리드 아키텍처를 기반으로 설계된 초고속 텍스트 검색 유틸리티입니다. 수만 개의 파일이나 기가바이트 단위의 대용량 파일에서도 지연 없는 검색 성능을 제공하며, 특히 개발 시 자주 접하는 JSON, XML, Excel 파일에 특화된 스마트 검색 기능을 제공합니다.

---

## 🚀 주요 특징

### 1. 극강의 성능 (Hybrid Architecture)
- **Rust Parallel Engine**: `ignore` 및 `rayon` 크레이트를 활용한 멀티 스레드 디렉토리 스캔 및 내용 검색을 통해 Python 대비 **3~4배** 이상의 성능을 발휘합니다.
- **Memory Mapping (mmap)**: 파일을 메모리에 직접 매핑하여 대용량 파일 검색 시에도 RAM 사용량을 최소화합니다.
- **Binary Pre-check & Protection**: 파일을 텍스트로 디코딩하기 전 바이트 단위로 사전 검사하여 불필요한 IO를 줄이며, 바이너리 파일 감지 시 깨진 글자 대신 플레이스홀더를 표시하여 UI를 보호합니다.

### 2. 지능형 검색 (Smart Scan)
- **Smart Filter**: 특수 검색(XML/JSON) 시 Rust 엔진이 1차적으로 키워드 포함 여부를 필터링하여, 내용이 있는 파일만 Python 파서에 전달함으로써 전체 검색 효율을 극대화합니다.
- **Excel 전문 엔진**: `python-calamine` 엔진을 활용하여 여러 시트로 구성된 엑셀 파일 내의 셀 데이터를 시트명과 좌표(예: Sheet1!A1)와 함께 정확하게 탐색합니다.
- **주석 제외 검색**: 소스 코드(C, Python, JS 등) 및 설정 파일(JSON, XML) 검색 시 주석을 제외한 실제 데이터 영역에서만 검색어 위치를 정확히 찾아냅니다.

### 3. 현대적인 사용자 인터페이스
- **PySide6(Qt) 기반**: 직관적이고 반응성이 뛰어난 GUI를 제공하며, 한글화된 로그 시스템을 통해 작업 단계를 명확히 안내합니다.
- **Dark Mode**: 시스템 테마와 연동되는 세련된 다크 테마를 기본 지원합니다.
- **Lazy Loading**: 수백만 라인의 검색 결과도 스크롤 시점에 필요한 만큼만 로드하여 UI 프리징을 방지합니다.

---

## � 설치 및 실행

### 일반 사용자 (Windows 실행 파일)
1. `dist/StringFinder.exe` 파일을 다운로드합니다.
2. 별도의 설치 과정 없이 파일을 실행하여 바로 사용합니다.

### 개발자 (소스 코드 실행)
본 프로젝트는 **Python 3.12+** 및 **Rust** 환경이 필요합니다.

```bash
# 1. 저장소 클론
git clone https://github.com/your-repo/StringFinder.git
cd StringFinder

# 2. 의존성 설치
pip install -r requirements.txt

# 3. Rust 엔진 컴파일 (maturin 활용)
cd src/sf_engine
maturin develop --release

# 4. 애플리케이션 실행
cd ../..
python src/main.py
```

---

## �️ 개발 및 빌드

### 로컬 빌드 (PyInstaller)
Rust 엔진이 포함된 단일 실행 파일을 생성하려면 프로젝트 루트에서 제공되는 빌드 스크립트를 사용합니다.

```bash
python build.py
```
*이 명령은 자동으로 Rust 바이너리를 릴리스 모드로 컴파일하고, 모든 자산(Asset)과 함께 단일 실행 파일로 패키징합니다.*

---

## 🧪 테스트 및 벤치마크
프로젝트의 안정성과 성능을 보장하기 위해 60개 이상의 테스트 케이스가 포함되어 있습니다.

```bash
# 전체 테스트 실행
pytest tests/

# 성능 벤치마크만 실행
pytest tests/test_performance_benchmarks.py --benchmark-only
```

---

## � 라이선스
본 프로젝트는 [MIT License](LICENSE)를 따릅니다.

---

**StringFinder**와 함께 대규모 프로젝트의 검색 생산성을 한 단계 높여보세요!
