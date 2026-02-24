# StringFinder

StringFinder는 대용량 파일에서 문자열을 빠르게 탐색하기 위한 Windows 데스크톱 도구입니다.
UI는 Python(PySide6), 검색 코어는 Python + Rust(`sf_engine`) 하이브리드 구조를 사용합니다.

---

## 요약
StringFinder는 "빠른 검색기"가 아니라 "운영 가능한 검색 시스템"을 목표로 설계되었습니다.  
핵심 설계는 다음 네 가지입니다.

- 성능 경로 분리: 일반 워크로드는 Rust, 정밀 유니코드 검색은 Python
- 통합 파이프라인: 첫 번째 결과 도달 시간(Zero-TTFR) 최소화를 위해 스캔과 본문 검색을 단일 단계로 오케스트레이션 수행
- 안정성 고도화: `catch_unwind` 기반 파닉 방어 및 Mmap 실패 시 Read 폴백 메커니즘
- 작업 지속성: 탭 세션, 상태 복원, 로그 기반 진단을 포함한 운영 UX

이 선택의 결과는 최고 단일 벤치마크 점수보다, 이질적 사용자 환경에서의 안정적인 완료율과 예측 가능한 동작입니다.

## 문제 정의
실무 검색 워크로드는 단일 형태가 아닙니다.

- 수십만 파일 수준의 대규모 트리 검색
- XML/JSON/Archive/Excel 같은 구조 파일 포함
- 권한 오류, 잠금 파일, 인코딩 혼재 같은 운영 노이즈
- 바이너리 파일 혼재로 인한 스캔 오버헤드
- "결과를 찾는 것" 이후의 확인/필터링/내보내기 단계

대부분의 검색 도구는 한 축에 특화되어 있습니다.

- CLI 도구: 최고 속도와 자동화에 강점
- 파일 인덱서: 파일명 탐색에 강점
- GUI grep: 사용성/정규식 편의성에 강점

StringFinder의 설계 목표는 단일 축 최적화가 아니라, 검색부터 결과 소비까지 이어지는 종단(end-to-end) 흐름 최적화입니다.

## 시스템 아키텍처
### 1) UI 및 오케스트레이션 계층 (Python/PySide6)
- 세션/탭 중심 워크플로우
- 결과/매치/미리보기 분리 렌더링
- 페이지네이션 기반 대량 결과 표시
- 트레이, 전역 단축키, 단일 인스턴스 제어

### 2) 엔진 계층 (Rust + Python Hybrid)
- 기본 검색 경로: Rust 엔진(`sf_engine`)
- 안정성 레이어: `std::panic::catch_unwind`를 통한 병렬 루프 보호 (특정 파일 오류가 시스템 전체로 파급되지 않음)
- 폴백 경로: Mmap 매핑 실패 시(잠금 파일 등) 일반 Read 모드로 자동 폴백
- 복합 검색 경로: Full CaseFolding 정밀도 확보를 위한 Python 레이어 독립 구동
- API 호환성 가드: `sf_engine.API_VERSION >= 4` 확인 후 활성화

### 3) 동시성 모델
- UI 반응성: Qt 스레드풀(`QThreadPool`) 기반 비동기 작업 처리
- 병렬 엔진: `Rayon` 기반 글로벌 스레드풀 활용 (Rust 코어) 및 적응형(Adaptive) 워커 할당 정책이 적용된 `ProcessPoolExecutor` (Python 경로)
- 작업 제어: `multiprocessing.Manager().Event()`를 통한 프로세스 간 중단 신호 전파 및 메모리 가드(Memory Guard) 모니터링

### 4) 검색 상태 머신
- 상태: `IDLE -> SCANNING -> IDLE`
- 중지 요청: `STOPPING`으로 전환 후 통합 `SearchWorker` 및 하위 엔진 구조에 중단 신호 전파

## 핵심 설계 결정과 트레이드오프
### 결정 1. "하나의 엔진"이 아닌 "정책 기반 다중 경로"
- 일반 검색: Rust 경로로 처리량 극대화
- 복합 검색: Python 경로로 유니코드 정밀도 보장

이 구조는 구현 복잡도를 증가시키지만, 실제 사용자 입력 분포를 기준으로 보면 더 합리적입니다.  

### 결정 2. 바이너리 파일 스캐닝 전략
- **조기 배제**: '바이너리 파일 제외' 옵션 활성화 시 Rust 엔진 레이어에서 즉시 필터링하여 불필요한 I/O 및 메모리 점유 방지
- **최상위 성능**: Mmap 기반 대용량 파일 고속 매핑 및 청크 단위 병렬 처리 최적화

### 결정 3. UI 응답성 우선 설계
- 대량 결과를 단일 테이블에 즉시 풀렌더링하지 않음
- 페이지 단위 표시/탐색으로 메모리 사용과 렌더링 부하를 제어

## 안정성 전략
### 1) 파닉 보호 및 수용력 (`Stability V2`)
- **Panic Guard**: Rust 엔진 내부의 `catch_unwind`가 예기치 못한 크래시를 포착하여 안전하게 Python 레이어로 에러 코드를 전달합니다.
- **Mmap Fallback**: Windows OS의 파일 잠금(Error 32) 등으로 Mmap 매핑이 실패할 경우, 포기하지 않고 일반 파일 Read 방식으로 자동 전환하여 검색을 완수합니다.

### 2) 무결성(Integrity) 및 정규화
- **유니코드 정규화**: 입력 검색어와 대상 텍스트의 NFC 정규화 및 Case Folding 일관성 유지.
- **Excel/JSON 가드**: 구조화된 파일의 형식이 손상되었을 경우 시스템이 멈추지 않고 'SKIPPED' 상태로 안전하게 식별합니다.

### 3) 운영 가시성(Observability)
- 상세 로깅: 엔진 전환, 폴백 발생, 스킵 사유 등을 `stringfinder.log`에 명시적으로 기록합니다.

---

## 사용자 가이드
### 1. 무엇을 할 수 있나
- 여러 폴더를 동시에 검색
- 확장자 기반 검색 대상 제어
- 파일명 필터 검색 (`npc`, `data_*.json`, `*.txt, *.log` 형태 지원)
- **바이너리 파일 제외 옵션** (성능 향상 및 가독성 확보)
- 특수 검색 모드:
  - XML / JSON / Archive / Excel (구조 기반 정밀 검색)
- 검색 결과/매치 상세/미리보기 분리 UI (**상세 뷰 페이지네이션 지원**)
- 숨김 파일/폴더 제외 옵션
- 결과 내보내기 (`.xlsx`, `.txt`)
- 탭 기반 세션 저장/복원
- 전역 단축키, 트레이 최소화, 단일 인스턴스 실행

### 2. 실행 방법
- 배포본 사용: `StringFinder.exe` 실행
- 소스 실행: `python run.py` (Maturin으로 빌드된 `sf_engine.pyd` 필요)

### 3. 기본 사용 순서
1. `폴더 목록`에 검색 폴더를 추가합니다.
2. `확장자 목록`에서 검색 대상 확장자를 선택합니다.
3. **'바이너리 제외'** 옵션 등을 필요에 따라 설정합니다.
4. 검색어를 입력하고 검색을 시작합니다.
5. 결과 뷰에서 파일을 선택하여 상세 매치 정보를 확인합니다.

### 4. 검색 옵션 상세
- **일반 검색**: Rust 엔진 기반 초고속 탐색. 대부분의 케이스에 권장.
- **특별한 문자열 검색 (Complex Search)**: Python 엔진 구동. `ß` <-> `ss` 같은 복잡한 유니코드 변형까지 검색해야 할 때 사용.
- **특수 검색**: JSON 키/값, XML 태그/속성 등 파일 구조별 필드 검색.

### 5. 단축키
- `Enter`: 검색 시작 (검색어 입력창 포커스 기준)
- `Ctrl+T`: 새 탭
- `Ctrl+C`: 결과/매치 항목 복사 (해당 테이블 포커스 기준)
- 전역 단축키 기본값: `Alt+Shift+Space` (설정에서 변경 가능)

### 6. 문제 해결 (FAQ)
- **검색 중 일부 파일이 스킵됨**: 파일 잠금, 권한 부족, 또는 바이너리 파일(옵션 활성화 시)일 경우 발생하며 로그 탭에서 상세 사유를 확인할 수 있습니다.
- **Rust 엔진 오류 발생**: 시스템이 크래시되지 않고 자동으로 해당 파일을 건너뛰며, 필요시 로깅된 에러 코드를 통해 원인을 파악할 수 있습니다.
- **Mmap 폴백 알림**: 로그에 `FALLBACK_TO_READ`가 표시되면 파일 잠금 등으로 인해 Mmap 대신 일반 읽기 방식으로 검색을 완수했다는 의미입니다.

### 7. 설정/데이터 저장 위치
기본 저장 경로:

```text
%APPDATA%\StringFinder
```

주요 파일:
- `config.json`: 사용자 설정
- `sessions\*.json`: 탭 세션
- `stringfinder_*.log`: 실행 로그

`APPDATA`가 없거나 접근 불가하면 임시 경로로 폴백합니다.

### 7. 문제 해결
- Rust 엔진은 고속 검색을 담당하며, 인코딩 감지 오류나 로드 실패 시에도 핵심 검색 성능 유지를 위해 최적화되어 있습니다.
- **Python 엔진(정밀 검색)**: 유니코드 정규화가 필요한 복합 검색(`use_complex_search=True`) 시에만 명시적으로 구동됩니다.
- 일반적인 검색 중 Rust 엔진 내부 오류가 발생할 경우, 해당 파일은 안전하게 스킵되며 전체 애플리케이션의 안정성을 우선합니다.
- 파일 잠금/권한 문제 파일은 검색 중 건너뛸 수 있습니다.
- 검색이 느리면 폴더 범위, 확장자 범위, 파일명 필터를 먼저 줄여서 실행하세요.
- 중지 후 UI가 즉시 복귀하지 않으면 로그 탭에서 `[제어] 중지 요청`, `[워커] 중지 신호`, `STATUS_READY` 흐름을 확인하세요.

### 8. 현재 검색 파이프라인
1. **통합 검색 단계 (Unified Scan & Search)**: 기존의 `ScanWorker` / `SearchWorker` 분리형 대기 시간을 제거하고 `SearchWorker`가 대상 추출과 검색을 동시에 관장하여 TTFR(첫 결과 반환 대기 시간)을 최소화합니다. (기존 `ScanWorker`는 하위 호환성용으로만 지원)
2. **Rust 고속 경로**: 성능이 극대화된 기본 모드로서, `search_directory_fast` 엔진 호출 시 `ignore::WalkBuilder` 기반 디렉토리 순회와 `Aho-Corasick` 파일 검색을 Rust 런타임 내에서 병렬로 단번에 해치웁니다.
3. **Python 정밀 경로**: 고난도 유니코드 처리를 위한 복합 검색(`use_complex_search=True`) 시, `FileScanner`로 후보군만 조기 배제한 뒤 적응형 `ProcessPoolExecutor` 기반 풀을 통해 파일 본문 검색을 다중 수행합니다.
4. **결과 파이프라인**: 스킵된 파일 목록과 매치 아이템 청크 단위는 채널 및 Qt Signal로 끊임없이 메인 스레드에 보고되어, UI의 ResultView 표와 통계 요약문에 부드럽게 병합처리됩니다.
5. **상태 회복**: 검색 완료/중지/치명적 오류 발생 시 `IDLE` 상태로 즉각 단일 복귀를 관장하여 UI의 검색 창과 조건 입력을 안전하게 재활성화합니다.

---

## 개발자 가이드

### 1. 기술 스택

| 구분 | 기술 스택 및 라이브러리 |
|---|---|
| **Python (UI & Orch.)** | Python 3.12+, PySide6, pyqtdarktheme, chardet, python-calamine, openpyxl, Pillow |
| **Rust (Engine Core)** | Rust 2021, sf_engine (PyO3), aho-corasick, memmap2, ignore, rayon, quick-xml |
| **Concurrency** | QThreadPool, ProcessPoolExecutor, multiprocessing.Manager |
| **Test & Quality** | pytest, pytest-qt, ruff, mypy, cargo clippy |
| **Build & Deploy** | PyInstaller API, cargo, maturin, build.py |

### 2. 개발 환경
- OS: Windows 권장
- Python: `>=3.12`
- Rust: stable toolchain (`cargo`)
- 권장: Visual C++ Build Tools

### 3. 의존성 설치
프로젝트 루트에서:

```powershell
pip install -e ".[dev]"
```

### 4. 개발 실행

```powershell
python run.py
```

### 5. Rust 엔진 빌드 (개발용)

```powershell
python build_rust.py
```

- 산출물: `src/sf_engine.pyd`
- 정리:

```powershell
python build_rust.py --clean
```

### 6. 테스트
기본 실행(`stress`, `chaos` 제외):

```powershell
pytest
```

스트레스/카오스만 실행:

```powershell
pytest -m stress -o addopts="-v --tb=short"
pytest -m chaos -o addopts="-v --tb=short"
```

전체 테스트(마커 포함) 실행:

```powershell
pytest -o addopts="-v --tb=short"
```

정적/품질 검사:

```powershell
ruff check . --fix
mypy src
cd src/rust_engine
cargo clippy --all-targets --all-features -- -D warnings
```

### 7. 배포 빌드
아이콘 변환 + Rust 바이너리 포함 + PyInstaller onefile 빌드:

```powershell
python build.py
```

- 산출물: `dist/StringFinder.exe`

### 8. 프로젝트 구조

```text
StringFinder/
├─ src/
│  ├─ assets/           # 아이콘/리소스
│  ├─ core/             # 검색 엔진, 워커, 시스템 연동
│  ├─ rust_engine/      # Rust 가속 엔진
│  ├─ sf_utils/         # 설정/로거/리소스/싱글인스턴스
│  ├─ ui/               # 메인윈도우/검색탭/패널/결과뷰
│  └─ sf_main.py        # 앱 진입점
├─ tests/               # pytest 스위트
├─ run.py               # 개발 실행 스크립트
├─ build_rust.py        # Rust 엔진 빌드 스크립트
├─ build.py             # 배포 빌드 스크립트
└─ pyproject.toml       # 프로젝트/테스트/툴 설정
```

### 9. 개발 시 참고
- 앱은 단일 인스턴스를 강제합니다.
- 테스트 환경에서는 트레이 등 일부 시스템 연동이 제한됩니다.
- 종료 경로에서 워커/프로세스 정리를 수행하므로, 워커 로직 변경 시 종료 시나리오도 함께 검증해야 합니다.
- 성능 경로 분리: 일반 워크로드는 Rust, 정밀 유니코드 검색은 Python

---

## 라이선스
현재 저장소 정책(내부/비공개 배포 기준)을 따릅니다.
