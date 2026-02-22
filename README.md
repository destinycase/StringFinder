# StringFinder

StringFinder는 대용량 파일에서 문자열을 빠르게 탐색하기 위한 Windows 데스크톱 도구입니다.
UI는 Python(PySide6), 검색 코어는 Python + Rust(`sf_engine`) 하이브리드 구조를 사용합니다.

---

## 요약
StringFinder는 "빠른 검색기"가 아니라 "운영 가능한 검색 시스템"을 목표로 설계되었습니다.  
핵심 설계는 다음 네 가지입니다.

- 성능 경로 분리: 일반 워크로드는 Rust, 정밀 유니코드 검색은 Python
- 2단계 파이프라인: `ScanWorker`(대상 추출) + `SearchWorker`(본문 검색) 분리
- 실패 격리: Rust 로드/런타임 실패 시 Python 경로로 자동 전환
- 작업 지속성: 탭 세션, 상태 복원, 로그 기반 진단을 포함한 운영 UX

이 선택의 결과는 최고 단일 벤치마크 점수보다, 이질적 사용자 환경에서의 안정적인 완료율과 예측 가능한 동작입니다.

## 문제 정의
실무 검색 워크로드는 단일 형태가 아닙니다.

- 수십만 파일 수준의 대규모 트리 검색
- XML/JSON/Archive/Excel 같은 구조 파일 포함
- 권한 오류, 잠금 파일, 인코딩 혼재 같은 운영 노이즈
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
- 폴백 경로: Rust 예외 시 Python 엔진 자동 전환
- 복합 검색 경로: Full CaseFolding 정밀도 확보를 위한 Python 우선
- API 호환성 가드: `sf_engine.API_VERSION >= 4` 확인 후 활성화

### 3) 동시성 모델
- UI 반응성: Qt 스레드풀 기반 작업 분리
- 배치 처리: `ProcessPoolExecutor` 기반 병렬 처리
- 중단 신호: `multiprocessing.Manager().Event()` 기반 취소 전파

### 4) 검색 상태 머신 (현재 구현)
- 상태: `IDLE -> SCANNING -> SEARCHING -> IDLE`
- 중지 요청: `STOPPING`으로 전환 후 Scan/Search 워커에 중단 신호 전파
- 안정성 보강: 워커 종료 콜백(`_on_scan_thread_finished`, `_on_worker_finished`)의 `finally`에서 상태를 `IDLE`로 복구

## 핵심 설계 결정과 트레이드오프
### 결정 1. "하나의 엔진"이 아닌 "정책 기반 다중 경로"
- 일반 검색: Rust 경로로 처리량 극대화
- 복합 검색: Python 경로로 유니코드 정밀도 보장

이 구조는 구현 복잡도를 증가시키지만, 실제 사용자 입력 분포를 기준으로 보면 더 합리적입니다.  
빈도 높은 일반 케이스는 저지연 처리하고, 저빈도 국제 문자 케이스는 정확도 우선 경로로 분리합니다.

### 결정 2. 결과 없음(No-match)과 오류(Error)의 분리
- "매치 없음"은 정상 시나리오
- I/O/권한/파싱 실패는 스킵 사유와 함께 별도 수집

### 결정 3. UI 응답성 우선 설계
- 대량 결과를 단일 테이블에 즉시 풀렌더링하지 않음
- 페이지 단위 표시/탐색으로 메모리 사용과 렌더링 부하를 제어

## 안정성 전략
### 1) 장애 격리(Fault Isolation)
- Rust 엔진 로드 실패: Python 경로로 전환
- Rust 런타임 오류: 작업 중단 대신 폴백 경로 재시도
- 파일 단위 예외: 전체 배치 실패로 확대하지 않고 건별 스킵

### 2) 무결성(Integrity) 보호
- 복합 검색 시 정밀 경로 강제
- JSON/Archive 검색에서 메모리 가드 정책 적용
- 설정 저장 실패 감지 및 실패 로그 남김

### 3) 운영 가시성(Observability)
- 엔진 선택/전환 로그
- 스킵 사유 코드 기반 추적
- 종료 시 리소스 정리 및 로그 유지정책 적용

---

## 사용자 가이드
### 1. 무엇을 할 수 있나
- 여러 폴더를 동시에 검색
- 확장자 기반 검색 대상 제어
- 파일명 필터 검색 (`npc`, `data_*.json`, `*.txt, *.log` 형태 지원)
- 특수 검색 모드:
  - XML (부분/정확)
  - JSON (부분/정확)
  - Archive (부분/정확)
  - Excel (부분/정확)
- 검색 결과/매치 상세/미리보기 분리 UI (**상세 뷰 페이지네이션 지원**)
- 숨김 파일/폴더 제외 옵션 지원 (Windows hidden 속성 기준)
- 검색 중 `중지` 요청 및 상태 복귀 (`중지 중...` 표시 후 `IDLE` 복귀)
- 결과 내보내기 (`.xlsx`, `.txt`)
- 탭 기반 세션 저장/복원
- 전역 단축키, 트레이 최소화, 단일 인스턴스 실행

### 2. 실행 방법
- 배포본 사용: `StringFinder.exe` 실행
- 소스 실행(프로젝트 루트):

```powershell
python run.py
```

### 3. 기본 사용 순서
1. `폴더 목록`에 검색 폴더를 추가합니다.
2. `확장자 목록`에서 검색 확장자를 선택/추가합니다.
3. 필요하면 `파일명 필터`를 입력합니다.
4. 검색어를 입력하고 `검색` 또는 `Enter`로 시작합니다.
5. 결과 파일을 선택해서 매치 상세/미리보기를 확인합니다.

### 4. 검색 옵션
- 일반 검색: 기본 부분 일치 검색 (Rust 엔진 기반 초고속 탐색)
- 복합 검색: 유니코드 Full CaseFolding 지원 (다양한 문자 변형 케이스 탐색)
- 특수 검색: 파일 구조(XML/JSON/Archive/Excel)에 맞춘 탐색
- 숨김 제외: `.git`, `AppData` 등 hidden 항목 스캔 제외로 성능/노이즈 개선

### 5. 단축키
- `Enter`: 검색 시작 (검색어 입력창 포커스 기준)
- `Ctrl+T`: 새 탭
- `Ctrl+C`: 결과/매치 항목 복사 (해당 테이블 포커스 기준)
- 전역 단축키 기본값: `Alt+Shift+Space` (설정에서 변경 가능)

### 6. 설정/데이터 저장 위치
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
- Rust 엔진이 로드되지 않거나 런타임 오류가 발생하면 자동으로 Python 엔진으로 동작합니다.
- 파일 잠금/권한 문제 파일은 검색 중 건너뛸 수 있습니다.
- 검색이 느리면 폴더 범위, 확장자 범위, 파일명 필터를 먼저 줄여서 실행하세요.
- 중지 후 UI가 즉시 복귀하지 않으면 로그 탭에서 `[제어] 중지 요청`, `[워커] 중지 신호`, `STATUS_READY` 흐름을 확인하세요.

### 8. 현재 검색 파이프라인
1. `ScanWorker`가 폴더/확장자/파일명 필터 조건으로 후보 파일을 수집합니다.
2. 기본 경로에서는 Rust Smart Scan(`find_files_with_keyword_fast`)으로 후보를 빠르게 축소합니다.
3. `SearchWorker`가 파일 리스트를 본문 검색합니다.
4. 일반 검색은 Rust(`search_files_list_fast`, `search_directory_fast`), 복합 검색은 Python 경로를 우선 사용합니다.
5. 스킵 파일은 사유와 함께 수집하여 결과 요약/로그에 노출합니다.
6. 완료/중지/오류 시 상태를 `IDLE`로 복구하고 UI 입력을 재활성화합니다.

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
ruff check src tests
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
- 실패 격리: Rust 로드/런타임 실패 시 Python 경로로 자동 전환

---

## 라이선스
현재 저장소 정책(내부/비공개 배포 기준)을 따릅니다.
