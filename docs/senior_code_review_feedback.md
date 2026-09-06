# StringFinder 시니어 코드 리뷰 및 아키텍처 피드백 보고서

- **검토 일자**: 2026년 9월 6일
- **검토 대상 버전**: v5.8.9 (StringFinder Desktop)
- **검토자 관점**: 10년 차 이상의 시니어 Python & Rust 시스템 소프트웨어 엔지니어
- **검토 범위**: 전체 코드베이스 (`src/core/`, `src/rust_engine/`, `src/sf_utils/`, `src/ui/`, `tests/`)

---

## 1. 종합 총평 (Executive Summary)

StringFinder 프로젝트는 PySide6 기반의 GUI 레이어와 Rust(PyO3, Rayon, Calamine, Aho-Corasick) 기반의 네이티브 검색 엔진이 유기적으로 결합된 하이브리드 고성능 텍스트 탐색 도구입니다. 

최근 진행된 v5.8.9 클린업을 통해 미사용 시그널 정리, 예외 전파 표준화(`ERR_*|detail`), 세션 손상 복구 방어, `clippy` 0 경고 달성 등 상용 소프트웨어 수준의 높은 기본기와 완성도를 확보하였습니다.

그러나 **시스템 레벨(OS System Call, CPU 캐시 및 메모리 할당 패턴, 다중 스레드 디스크 I/O 경합, 잠재적 공격 표면)** 관점에서 깊이 분석한 결과, 대규모 파일 검색 및 특수 환경(다중 사용자 세션, 비-Windows OS 등)에서 성능을 극대화하고 런타임 오류를 완벽히 차단하기 위해 개선해야 할 핵심 과제들이 도출되었습니다.

특히 검색 소프트웨어의 가장 핵심적인 생명선인 **"검색 정확도 및 오탐(False Positive) / 미탐(False Negative) 방지"** 원칙을 최우선으로 하여, 모든 피드백 항목에 대해 검색 결과 왜곡 가능성을 전수 정밀 검증하였습니다.

본 보고서는 시니어 엔지니어의 시각에서 **소스 코드 직접 수정 없이**, 향후 프로젝트의 아키텍처 성숙도를 한 단계 끌어올릴 수 있는 5대 핵심 영역의 정밀 피드백과 구체적인 리팩토링 구현 코드, 그리고 오탐/미탐 안전성 검증 결과를 제시합니다.

---

## 2. 검토 항목 요약 대시보드

| ID | 카테고리 | 심각도 / 권고 판정 | 항목명 | 관련 파일 | 오탐/미탐 위험도 |
|:---|:---|:---:|:---|:---|:---:|
| **SEC-01** | 보안 취약점 | **HIGH** | `os.startfile` 호출 시 실행 파일 무검증 실행 위험 | [`src/sf_utils/file_helper.py`](file:///d:/Project/StringFinder/src/sf_utils/file_helper.py#L69-L70) | **위험 없음 (0%)** |
| **SEC-02** | 보안 취약점 | **MEDIUM** | 공용 임시 디렉터리 고정 파일명 사용 (TOCTOU/Symlink 공격 위험) | [`src/core/doctor.py`](file:///d:/Project/StringFinder/src/core/doctor.py#L118-L122) | **위험 없음 (0%)** |
| **SEC-03** | 보안 취약점 | **LOW** | 다중 사용자 세션(RDS/Terminal Server) 인스턴스 락 충돌 | [`src/sf_utils/single_instance.py`](file:///d:/Project/StringFinder/src/sf_utils/single_instance.py#L34) | **위험 없음 (0%)** |
| **BUG-01** | 버그 및 오류 | **HIGH** | 비-Windows(Linux) 환경에서 시스템 진단서 열기 실패 | [`src/core/doctor.py`](file:///d:/Project/StringFinder/src/core/doctor.py#L128) | **위험 없음 (0%)** |
| **BUG-02** | 버그 및 오류 | **LOW** | `MainWindow.closeEvent`의 `event.accept()` 명시적 호출 누락 | [`src/ui/main_window.py`](file:///d:/Project/StringFinder/src/ui/main_window.py#L86-L88) | **위험 없음 (0%)** |
| **PERF-01** | 성능 및 효율성 | **HIGH** | 다중 루트 경로 검색 시 Rayon과 Ignore 워커 풀 중첩 (Thread Oversubscription) | [`src/rust_engine/src/lib.rs`](file:///d:/Project/StringFinder/src/rust_engine/src/lib.rs#L1419-L1434) | **위험 없음 (0%)** |
| **PERF-02** | 성능 및 효율성 | **HIGH (주의)** | Excel 셀 순회 시 매칭 전 전수 힙 할당 (Zero-Allocation 부재) | [`src/rust_engine/src/excel_search.rs`](file:///d:/Project/StringFinder/src/rust_engine/src/excel_search.rs#L209) | **조건부 주의 (가드 필수)** |
| **PERF-03** | 성능 및 효율성 | **[보류 권고]** | ASCII 문자열 비교 시 이중 변환 제거 및 `eq_ignore_ascii_case` 도입 | [`src/rust_engine/src/excel_search.rs`](file:///d:/Project/StringFinder/src/rust_engine/src/excel_search.rs#L230), [`lib.rs`](file:///d:/Project/StringFinder/src/rust_engine/src/lib.rs#L548) | **🚨 잠재 미탐 위험 (현행 유지 권고)** |
| **PERF-04** | 성능 및 효율성 | **MEDIUM** | `ConfigManager.get()`의 불변 원시 값 대상 무조건적인 `deepcopy` 오버헤드 | [`src/sf_utils/config_manager.py`](file:///d:/Project/StringFinder/src/sf_utils/config_manager.py#L306) | **위험 없음 (0%)** |
| **PERF-05** | 성능 및 효율성 | **LOW** | 단일 파일 검색(`search_file`) 시 OS 모니터 스레드 생성/종료 비용 | [`src/rust_engine/src/lib.rs`](file:///d:/Project/StringFinder/src/rust_engine/src/lib.rs#L375-L403) | **위험 없음 (0%)** |
| **PERF-06** | 성능 및 효율성 | **LOW** | 파일 스냅샷 로드 시 불필요한 OS 핸들 복제 (`try_clone`) 및 버퍼 재할당 | [`src/rust_engine/src/lib.rs`](file:///d:/Project/StringFinder/src/rust_engine/src/lib.rs#L333-L336) | **위험 없음 (0%)** |
| **PERF-07** | 성능 및 효율성 | **LOW** | Excel 시트 이름 순회 시 불필요한 벡터 복제 (`to_vec()`) | [`src/rust_engine/src/excel_search.rs`](file:///d:/Project/StringFinder/src/rust_engine/src/excel_search.rs#L40) | **위험 없음 (0%)** |
| **CONV-01** | 가독성 및 컨벤션 | **LOW** | 비직관적인 메서드 명칭 (`cancel_start`) | [`src/sf_utils/config_manager.py`](file:///d:/Project/StringFinder/src/sf_utils/config_manager.py#L233) | **위험 없음 (0%)** |
| **CONV-02** | 가독성 및 컨벤션 | **LOW** | Excel 컬럼 알파벳 변환 시 O(N) 버퍼 시프트 (`col_letter.insert(0, ...)`) | [`src/rust_engine/src/excel_search.rs`](file:///d:/Project/StringFinder/src/rust_engine/src/excel_search.rs#L215) | **위험 없음 (0%)** |
| **CONV-03** | 가독성 및 컨벤션 | **LOW** | Sublime Text 에디터 실행 인자 구성 분기 은닉 | [`src/sf_utils/file_helper.py`](file:///d:/Project/StringFinder/src/sf_utils/file_helper.py#L131) | **위험 없음 (0%)** |

---

## 3. 심층 기술 분석 및 리팩토링 제안

### [카테고리 1] 보안 취약점 (Security Vulnerabilities)

#### 1. SEC-01: `os.startfile` 호출 시 실행 파일 무검증 실행 위험 (Arbitrary Code Execution)
- **위치**: [`src/sf_utils/file_helper.py:69-70`](file:///d:/Project/StringFinder/src/sf_utils/file_helper.py#L69-L70)
- **심각도**: **HIGH**
- **오탐/미탐 영향도**: **0% (검색 엔진과 완전히 분리된 UI 결과 클릭 핸들러)**
- **현상 및 원인 분석**:
  `open_file(file_path)` 함수는 Windows 환경에서 `os.startfile(file_path)`을 직접 호출합니다. `os.startfile`은 Windows 내부적으로 `ShellExecute`를 호출하므로, 전달된 파일이 실행 파일(`.exe`, `.bat`, `.cmd`, `.ps1`, `.vbs`, `.js`, `.msi`, `.scr`)일 경우 별도의 텍스트 뷰어나 편집기가 아닌 **프로세스로 즉시 실행**됩니다.
  사용자가 다운로드 폴더나 네트워크 공유 폴더 등 신뢰할 수 없는 디렉터리를 검색한 후, 검색 결과 목록에서 악성 스크립트나 트로이목마 실행 파일을 단순 텍스트로 오인하여 더블클릭할 경우 임의 코드가 실행될 수 있는 공격 표면이 됩니다.
- **개선 제안**:
  StringFinder는 텍스트/문서 검색 도구이므로, 실행 파일 확장자에 대해서는 직접 실행을 방지하고 기본 텍스트 뷰어(예: 메모장)로 열거나 사용자에게 실행 위험 경고 대화상자를 띄우는 보안 게이트키퍼(Gatekeeper)를 배치해야 합니다.

```python
# [개선안] src/sf_utils/file_helper.py
DANGEROUS_EXTENSIONS = {
    ".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".jse", 
    ".wsf", ".wsh", ".msi", ".scr", ".pif", ".com", ".cpl"
}

def open_file(file_path: str, allow_executable: bool = False) -> bool:
    """시스템 기본 프로그램으로 지정된 파일을 실행합니다."""
    try:
        ext = os.path.splitext(file_path)[1].lower()
        if not allow_executable and ext in DANGEROUS_EXTENSIONS:
            logger.warning("실행 위험 확장자 직접 실행 차단: %s", file_path)
            # 안전하게 시스템 기본 텍스트 편집기(메모장 등)로 열도록 리다이렉트
            return open_in_external_editor(file_path, line=1)

        if os.name == "nt":
            os.startfile(file_path)
        elif os.name == "posix":
            opener_name = "open" if sys.platform == "darwin" else "xdg-open"
            opener = shutil.which(opener_name)
            if not opener:
                return False
            completed = subprocess.run(
                [opener, file_path],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return completed.returncode == 0
    except (OSError, PermissionError, FileNotFoundError, subprocess.SubprocessError):
        return False
    return True
```

---

#### 2. SEC-02: 공용 임시 디렉터리 고정 파일명 사용 (TOCTOU / Symlink 공격 위험)
- **위치**: [`src/core/doctor.py:118-122`](file:///d:/Project/StringFinder/src/core/doctor.py#L118-L122)
- **심각도**: **MEDIUM**
- **오탐/미탐 영향도**: **0% (진단 보고서 내보내기 로직)**
- **현상 및 원인 분석**:
  `temp_path = os.path.join(tempfile.gettempdir(), "sf_doctor_report.md")`와 같이 시스템 공용 임시 폴더에 고정된 파일명을 생성하고 있습니다.
  다중 사용자 환경(Windows Terminal Server, Linux 공용 서버 등)에서는 악의적인 타 사용자가 동일한 경로에 심볼릭 링크를 미리 생성해 두어 시스템 진단 보고서 작성 시 중요 파일을 덮어쓰도록 유도(Symlink Race / TOCTOU)하거나, 이전 사용자가 생성해 둔 파일의 권한으로 인해 다른 사용자가 진단 기능을 실행할 때 `PermissionError`가 발생하며 실패하게 됩니다.
- **개선 제안**:
  `tempfile.NamedTemporaryFile`을 사용하여 예측 불가능한 난수 파일명을 가진 임시 파일을 안전하게 생성하도록 개선합니다.

```python
# [개선안] src/core/doctor.py
def run_doctor_and_open():
    """진단을 실행하고 결과를 임시 텍스트 파일로 저장한 후 시스템 기본 편집기로 연다."""
    doctor = SystemDoctor()
    report_content = doctor.run_full_diagnosis()
    
    import tempfile
    from sf_utils.file_helper import open_file

    try:
        # 안전한 고유 임시 파일 생성
        with tempfile.NamedTemporaryFile(
            mode="w", 
            prefix="sf_doctor_", 
            suffix=".md", 
            delete=False, 
            encoding="utf-8"
        ) as f:
            temp_path = f.name
            f.write(report_content)
        
        # 시스템 기본 편집기로 열기 (file_helper 활용으로 크로스 플랫폼 지원)
        return open_file(temp_path)
    except Exception as e:
        logger.error(f"Failed to save or open doctor report: {e}")
        return False
```

---

#### 3. SEC-03: 다중 사용자 환경(RDS/Terminal Server) 인스턴스 락 충돌
- **위치**: [`src/sf_utils/single_instance.py:34`](file:///d:/Project/StringFinder/src/sf_utils/single_instance.py#L34)
- **심각도**: **LOW**
- **오탐/미탐 영향도**: **0% (프로세스 시작 시점 단일 인스턴스 보장 로직)**
- **현상 및 원인 분석**:
  `lock_path = os.path.join(QDir.tempPath(), "stringfinder.lock")`
  `QDir.tempPath()`는 시스템 전역 임시 디렉터리를 가리킬 수 있습니다. 원격 데스크톱 서비스(RDS)나 다중 사용자 세션 서버에서 사용자 A가 StringFinder를 실행 중이면, 독립된 세션에서 로그인한 사용자 B는 `stringfinder.lock` 파일을 잠글 수 없거나 파일 접근 권한이 없어 앱을 실행하지 못하고 "이미 실행 중" 경고를 보게 됩니다.
- **개선 제안**:
  락 파일 이름에 현재 로그인한 사용자 계정명(또는 계정명의 해시)을 포함시켜 사용자 세션 간 완전한 격리를 보장합니다.

```python
# [개선안] src/sf_utils/single_instance.py
import getpass
import hashlib

def ensure_single_instance() -> None:
    global _lock_file
    user_hash = hashlib.md5(getpass.getuser().encode("utf-8", errors="ignore")).hexdigest()[:8]
    lock_filename = f"stringfinder_{user_hash}.lock"
    lock_path = os.path.join(QDir.tempPath(), lock_filename)
    _lock_file = QLockFile(lock_path)
    ...
```

---

### [카테고리 2] 버그 및 플랫폼 호환성 오류 (Bugs & Runtime Errors)

#### 1. BUG-01: 비-Windows(Linux) 환경에서 시스템 진단서 열기 실패
- **위치**: [`src/core/doctor.py:125-128`](file:///d:/Project/StringFinder/src/core/doctor.py#L125-L128)
- **심각도**: **HIGH**
- **오탐/미탐 영향도**: **0% (플랫폼 유틸리티 호환성 오류)**
- **현상 및 원인 분석**:
  ```python
  if os.name == "nt":
      os.startfile(temp_path)
  else:
      subprocess.run(["open", temp_path])
  ```
  `open` 명령어는 macOS(Darwin) 전용 CLI입니다. Linux 배포판(Ubuntu, Fedora 등)에는 `open` 바이너리가 없거나 다른 용도로 쓰이며, 데스크톱 기본 파일 오프너는 `xdg-open`입니다.
  따라서 Linux 환경에서 '시스템 진단(Doctor)'을 실행할 경우 `FileNotFoundError: [Errno 2] No such file or directory: 'open'`이 발생하여 진단 결과 문서가 열리지 않습니다.
  동일 프로젝트 내 [`src/sf_utils/file_helper.py:72`](file:///d:/Project/StringFinder/src/sf_utils/file_helper.py#L72)에는 `opener_name = "open" if sys.platform == "darwin" else "xdg-open"` 처리가 정상적으로 구현되어 있으나, 이를 재사용하지 않아 발생한 코드 중복(DRY 위반) 버그입니다.
- **개선 제안**:
  중복된 OS 호출을 제거하고 SEC-02에서 제시한 바와 같이 `file_helper.open_file(temp_path)`을 호출하도록 일원화합니다.

---

#### 2. BUG-02: `MainWindow.closeEvent`의 `event.accept()` 명시적 호출 누락
- **위치**: [`src/ui/main_window.py:86-88`](file:///d:/Project/StringFinder/src/ui/main_window.py#L86-L88)
- **심각도**: **LOW**
- **오탐/미탐 영향도**: **0% (UI 이벤트 루프 수명주기)**
- **현상 및 원인 분석**:
  ```python
  def closeEvent(self, event):
      """창을 닫을 때 호출되어 애플리케이션 종료 절차를 수행합니다."""
      self._quit_application()
  ```
  Qt 이벤트 루프에서 `closeEvent` 오버라이드 시 `event.accept()` 또는 `super().closeEvent(event)`를 명시하지 않으면, 이벤트 필터나 커스텀 프레임워크 환경에서 이벤트 전파 처리가 모호해질 수 있습니다.
- **개선 제안**:
  `self._quit_application()` 완료 후 `event.accept()`를 명시적으로 호출합니다.

---

### [카테고리 3] 성능 및 효율성 병목 (Performance & Efficiency)

#### 1. PERF-01: 다중 루트 경로 검색 시 Rayon과 Ignore 워커 풀 중첩 (Thread Oversubscription)
- **위치**: [`src/rust_engine/src/lib.rs:1419-1434`](file:///d:/Project/StringFinder/src/rust_engine/src/lib.rs#L1419-L1434)
- **심각도**: **HIGH**
- **오탐/미탐 영향도**: **0% (스레드 분배 최적화, 방문 대상 파일 집합 100% 동일)**
- **현상 및 원인 분석**:
  ```rust
  paths.into_par_iter().for_each(|root| {
      let mut builder = WalkBuilder::new(&root);
      builder.hidden(exclude_hidden).ignore(false).git_ignore(false);
      let walker = builder.build_parallel();
      ...
      walker.run(move || { ... });
  });
  ```
  사용자가 여러 개의 검색 대상 폴더(다중 경로)를 지정할 경우, 바깥쪽에서 Rayon의 `paths.into_par_iter()`가 시스템 코어 수만큼의 Rayon 워커 스레드를 사용하여 경로들을 병렬 처리합니다.
  그런데 각 Rayon 워커 스레드 내부에서 `builder.build_parallel()`을 호출하면, `ignore` 크레이트가 **내부적으로 또다시 CPU 코어 수(예: 8~16개)만큼의 스레드 풀을 각각 생성**합니다.
  예를 들어 코어가 8개인 머신에서 4개 경로를 검색하면, $4 \times 8 = 32$개의 스레드가 동시에 디스크 I/O를 경합(Oversubscription)하게 됩니다. 이는 NVMe SSD에서도 디스크 큐 depth 포화 및 커널 컨텍스트 스위칭 오버헤드를 유발하며, 물리적 탐색 지연이 있는 HDD나 네트워크 드라이브에서는 극심한 I/O 성능 저하를 초래합니다.
- **개선 제안**:
  `ignore::WalkBuilder`는 단일 빌더 인스턴스에 복수의 루트 경로를 등록할 수 있는 `.add(path)` 메서드를 제공합니다. Rayon 병렬 반복문을 걷어내고, 단일 `WalkBuilder`에 모든 경로를 등록한 뒤 단일 병렬 워커 풀로 순회해야 시스템 리소스가 완벽하게 통제됩니다.

```rust
// [개선안] src/rust_engine/src/lib.rs
let mut iter = paths.into_iter();
if let Some(first_path) = iter.next() {
    let mut builder = WalkBuilder::new(first_path);
    for next_path in iter {
        builder.add(next_path); // 다중 경로를 단일 빌더에 추가
    }
    builder.hidden(exclude_hidden).ignore(false).git_ignore(false);
    let walker = builder.build_parallel(); // 시스템 코어 수에 맞춘 단일 풀 생성

    let res_ref = Arc::clone(&results);
    let skipped_ref = Arc::clone(&skipped);
    let ac_ref = Arc::clone(&ac_shared);
    let stop_ref = Arc::clone(&stop_flag);
    let kw_orig = norm_keyword.clone();
    let ext_s = exts.clone();
    let glob_s = glob_set.clone();
    let limiter_ref = structured_limiter.clone();
    let tx_kw = results_dispatcher.as_ref().map(|(tx, _)| tx.clone());

    walker.run(move || {
        // 모든 경로가 스레드 낭비 없이 최적의 코어 수로 병렬 처리됨
        ...
    });
}
```

---

#### 2. PERF-02: Excel 셀 순회 시 매칭 전 전수 힙 할당 (Zero-Allocation 부재)
- **위치**: [`src/rust_engine/src/excel_search.rs:208-219, 240-253`](file:///d:/Project/StringFinder/src/rust_engine/src/excel_search.rs#L208)
- **심각도**: **HIGH (주의: `is_empty` 가드 필수)**
- **오탐/미탐 영향도**: **가드 미준수 시 오탐/미탐 가능 → 엄격한 가드 준수 시 0%**
- **현상 및 원인 분석**:
  `cell_to_string` 함수는 `Data::String(s)`에 대해 `s.to_string()`을 호출하여 매번 새 `String`을 힙에 할당합니다. 수십만 행의 엑셀 문서를 검색할 때 키워드가 매칭될 확률은 일반적으로 1% 미만(99% 이상 불일치)입니다. 그럼에도 불구하고 불일치하는 수십만 개의 텍스트 셀에 대해 메모리 할당(malloc)과 즉시 해제(free)가 반복되면서 CPU 캐시 오염 및 메모리 대역폭 낭비가 발생합니다.
- **🚨 오탐/미탐 방지 수칙**:
  1. 기존 `cell_to_string`은 `if s.is_empty() { None }` 가드를 통해 내용이 없는 빈 셀을 사전에 차단했습니다. 만약 Zero-Allocation을 적용하면서 **`s.is_empty()` 체크를 빠뜨리면 빈 셀이 매칭으로 잘못 검출(False Positive)되는 오탐이 발생**합니다.
  2. 실수/숫자 셀(`Data::Float`, `Data::Int`)은 `10.0`을 `10`으로 자연스럽게 표시하는 정규화 로직이 있습니다. 이를 섣불리 단순화하면 숫자 검색 시 결과가 누락되는 **미탐(False Negative)**이 발생합니다. 따라서 `Data::String` 외의 타입은 기존 변환 함수를 100% 보존해야 합니다.
- **안전한 개선 제안**:

```rust
// [안전 개선안] src/rust_engine/src/excel_search.rs
fn match_cell(cell: &Data, sheet_name: &str, row_idx: usize, col_idx: usize, ctx: &ExcelCtx<'_>) -> Option<RawMatch> {
    match cell {
        // [무결성 보장] Data::String에 대해서만 무할당 검사 수행
        Data::String(s) => {
            // ★ 필수 가드: 빈 셀 오탐(False Positive) 원천 차단
            if s.is_empty() || !cell_matches_val(s, ctx) { 
                return None; 
            }
            let col_letter = col_index_to_letter(col_idx);
            Some((row_idx + 1, format!("{}\t{}{}\t{}", sheet_name, col_letter, row_idx + 1, s), None, None))
        }
        Data::Empty => None,
        _ => {
            // [무결성 보장] 숫자/날짜/불리언 셀은 기존의 검증된 정규화 함수를 100% 그대로 통과
            let val = cell_to_string(cell)?;
            if !cell_matches_val(&val, ctx) { 
                return None; 
            }
            let col_letter = col_index_to_letter(col_idx);
            Some((row_idx + 1, format!("{}\t{}{}\t{}", sheet_name, col_letter, row_idx + 1, val), None, None))
        }
    }
}
```

---

#### 3. PERF-03: ASCII 문자열 비교 시 이중 변환 제거 및 `eq_ignore_ascii_case` 도입
- **위치**: [`src/rust_engine/src/excel_search.rs:228-231`](file:///d:/Project/StringFinder/src/rust_engine/src/excel_search.rs#L228-L231) 및 [`lib.rs:548, 554`](file:///d:/Project/StringFinder/src/rust_engine/src/lib.rs#L548)
- **심각도 / 권고 판정**: **[보류 권고 (유니코드 무결성 우선)]**
- **오탐/미탐 영향도**: **🚨 잠재적 미탐(False Negative) 위험 존재**
- **기술적 심층 분석 (왜 적용을 보류해야 하는가?)**:
  현재 StringFinder 엔진은 완전 일치(`is_exact`) 검색 시 다음 유니코드 정규화 파이프라인을 탑니다:
  ```rust
  // 현행 코드
  val.trim().to_lowercase().to_uppercase() == ctx.pat_upper
  ```
  이 로직은 파이썬의 `casefold()`와 일치하도록 **유니코드 특수 대소문자 매핑**을 충실히 처리합니다.
  - **구체적 사례**: 켈빈 온도 기호 `'K'` (U+212A, 비-ASCII)는 대소문자 변환 시 영문 알파벳 대문자 `'K'` (0x4B, ASCII)로 정규화됩니다.
  - 만약 성능을 위해 이를 `eq_ignore_ascii_case`로 단순 치환하고 검색어 원본을 비교하게 되면, **유니코드 특수 기호와 영문자가 대등하게 매칭되어야 하는 정밀한 케이스에서 불일치 판정이 나며 검색 결과가 누락(미탐)되는 치명적인 무결성 결함**이 발생할 수 있습니다.
  - 이 최적화로 얻을 수 있는 이득은 기껏해야 셀당 수 마이크로초 수준이지만, 그 대가로 검색 정확도를 희생하는 것은 소프트웨어 신뢰성에 치명적입니다.
- **시니어 최종 판정**:
  👉 **"검색 결과 무결성을 100% 지키기 위해 이 최적화는 적용하지 않고, 현행 유니코드 정규화 파이프라인을 그대로 유지하는 것을 강력히 권고합니다."**

---

#### 4. PERF-04: `ConfigManager.get()`의 불변 원시 값 대상 무조건적인 `deepcopy` 오버헤드
- **위치**: [`src/sf_utils/config_manager.py:303-306`](file:///d:/Project/StringFinder/src/sf_utils/config_manager.py#L303-L306)
- **심각도**: **MEDIUM**
- **오탐/미탐 영향도**: **0% (불변 객체 방어적 복사 생략)**
- **현상 및 원인 분석**:
  `get()` 메서드는 검색 루프, UI 이벤트 핸들러, 타이머 등 애플리케이션 전역에서 매우 빈번하게 호출됩니다.
  대부분의 설정 값은 불변(Immutable) 객체인 `bool`, `int`, `float`, `str`입니다. Python의 `copy.deepcopy()`는 내부 타입 검사와 메모(memo) 딕셔너리를 생성하므로 원시 값 단순 반환에 비해 수십 배 이상의 CPU 연산 오버헤드를 발생시킵니다.
  불변 객체는 외부에서 값을 수정(mutate)하는 것이 물리적으로 불가능하므로 deepcopy를 생략해도 검색 옵션(`is_exact`, `case_sensitive` 등)이 오염될 위험이 전무합니다.
- **개선 제안**:
  가변(Mutable) 컨테이너 타입(`dict`, `list`, `set`)에 대해서만 `deepcopy`를 수행하고, 불변 타입은 락 보호 하에 즉시 반환하도록 최적화합니다.

```python
# [개선안] src/sf_utils/config_manager.py
def get(self, key, default=None):
    """설정 값을 조회한다. 가변 객체만 방어적 복사를 수행하여 성능을 극대화한다."""
    with self._config_lock:
        val = self._config.get(key, default)
        if isinstance(val, (dict, list, set)):
            return copy.deepcopy(val)
        return val
```

---

#### 5. PERF-05: 단일 파일 검색(`search_file`) 시 OS 모니터 스레드 생성/종료 비용
- **위치**: [`src/rust_engine/src/lib.rs:375-403`](file:///d:/Project/StringFinder/src/rust_engine/src/lib.rs#L375-L403)
- **심각도**: **LOW**
- **오탐/미탐 영향도**: **0% (취소 이벤트 폴링 방식 경량화)**
- **현상 및 원인 분석**:
  단일 파일 1개를 검색하는 `search_file` 함수 호출마다 `std::thread::spawn`을 통해 별도의 OS 스레드를 생성하고, 100ms마다 GIL을 획득하며 `stop_event`를 폴링합니다.
  작은 텍스트 파일(수 KB~수십 KB)의 실제 검색 시간은 0.05ms 미만인데, OS 스레드 생성 및 종료(스택 할당, 커널 스케줄러 등록) 오버헤드가 검색 시간보다 훨씬 커질 수 있습니다.
- **개선 제안**:
  단일 파일 검색의 경우 함수 진입 전 Python 단에서 `stop_event.is_set()`을 확인하거나, 필요 시에만 스레드를 띄우도록 경량화합니다.

---

#### 6. PERF-06: 파일 스냅샷 로드 시 불필요한 OS 핸들 복제 (`try_clone`) 및 버퍼 재할당
- **위치**: [`src/rust_engine/src/lib.rs:333-336`](file:///d:/Project/StringFinder/src/rust_engine/src/lib.rs#L333-L336)
- **심각도**: **LOW**
- **오탐/미탐 영향도**: **0% (바이너리 버퍼 읽기 시스템 콜 최적화, 읽는 데이터 100% 동일)**
- **현상 및 원인 분석**:
  ```rust
  let mut bytes = Vec::new();
  let mut reader = file.try_clone().map_err(|e| e.to_string())?;
  reader.read_to_end(&mut bytes).map_err(|e| e.to_string())?;
  ```
  `open_snapshot_file` 함수 내부에서 `file: File`은 이미 단독 소유권을 가지고 있으므로 `file.try_clone()`을 호출할 이유가 없습니다. Windows에서 `try_clone`은 `DuplicateHandle` 시스템 콜을 유발합니다. 또한 파일 크기(`expected_len`)를 이미 알고 있음에도 `Vec::new()`로 시작하여 `read_to_end` 동안 지수적 버퍼 재할당(Realloc)이 발생합니다.
- **개선 제안**:
  `file` 소유권을 직접 사용하고 `Vec::with_capacity(expected_len as usize)`로 단 1회의 할당으로 버퍼를 준비합니다.

---

#### 7. PERF-07: Excel 시트 이름 순회 시 불필요한 벡터 복제 (`to_vec()`)
- **위치**: [`src/rust_engine/src/excel_search.rs:40, 91`](file:///d:/Project/StringFinder/src/rust_engine/src/excel_search.rs#L40)
- **심각도**: **LOW**
- **오탐/미탐 영향도**: **0% (순회하는 시트 목록 및 순서 100% 동일)**
- **현상 및 원인 분석**:
  `for sheet_name in wb.sheet_names().to_vec()`
  `calamine`의 `sheet_names()`는 이미 `&[String]` 슬라이스를 반환하므로 `.to_vec()`을 호출하여 힙에 벡터를 복제할 필요가 없습니다.
- **개선 제안**:
  `for sheet_name in wb.sheet_names()`로 슬라이스를 직접 순회합니다.

---

### [카테고리 4 & 5] 가독성, 컨벤션 및 클린 코드 (Readability, Conventions & Clean Code)

#### 1. CONV-01: 비직관적인 메서드 명칭 (`cancel_start`)
- **위치**: [`src/sf_utils/config_manager.py:233`](file:///d:/Project/StringFinder/src/sf_utils/config_manager.py#L233)
- **심각도**: **LOW**
- **오탐/미탐 영향도**: **0%**
- **개선 제안**:
  의도가 명확한 `cancel_scheduled_save()`로 명명하고, 기존 명칭은 하위 호환성을 위한 alias로 유지합니다.

```python
def cancel_scheduled_save(self):
    """예약된 저장 타이머를 취소한다."""
    with self._save_lock:
        self._cancel_start_unlocked()

# 하위 호환성 유지용 alias
cancel_start = cancel_scheduled_save
```

---

#### 2. CONV-02: Excel 컬럼 알파벳 변환 시 O(N) 버퍼 시프트
- **위치**: [`src/rust_engine/src/excel_search.rs:212-217`](file:///d:/Project/StringFinder/src/rust_engine/src/excel_search.rs#L212-L217)
- **심각도**: **LOW**
- **오탐/미탐 영향도**: **0%**
- **개선 제안**:
  스택 배열 버퍼를 사용한 관용적 헬퍼 함수로 분리합니다.

```rust
fn col_index_to_letter(col_idx: usize) -> String {
    let mut buf = [0u8; 4];
    let mut len = 0;
    let mut temp = col_idx as i32;
    while temp >= 0 && len < 4 {
        buf[len] = b'A' + (temp % 26) as u8;
        len += 1;
        temp = temp / 26 - 1;
    }
    buf[..len].reverse();
    unsafe { String::from_utf8_unchecked(buf[..len].to_vec()) }
}
```

---

#### 3. CONV-03: Sublime Text 에디터 실행 인자 구성 분기 은닉
- **위치**: [`src/sf_utils/file_helper.py:123-132`](file:///d:/Project/StringFinder/src/sf_utils/file_helper.py#L123-L132)
- **심각도**: **LOW**
- **오탐/미탐 영향도**: **0%**
- **개선 제안**:
  `elif editor_type == "sublime":` 분기를 명시적으로 작성하여 가독성을 높입니다.

---

## 4. 검색 정확도 및 오탐/미탐(False Positive / False Negative) 정밀 검증 보고

검색 소프트웨어에서 성능보다 선행되어야 하는 절대 원칙은 **"단 1개의 매칭도 빠뜨리지 않고(Zero False Negative), 일치하지 않는 내용을 보여주지 않는(Zero False Positive) 무결성"**입니다. 

본 검토를 통해 확인된 오탐/미탐 분류 체계는 다음과 같습니다:

```
[피드백 항목 무결성 분류]
 ├── 🛡️ 안전 최적화 (오탐/미탐 위험 0%): 
 │     ├── PERF-01 (WalkBuilder 단일 풀)
 │     ├── PERF-04 (Config deepcopy 최적화)
 │     ├── PERF-06 (try_clone 제거 및 사전할당)
 │     ├── PERF-07 (시트명 슬라이스 순회)
 │     └── SEC-01~03, BUG-01~02 (외부 레이어)
 ├── ⚠️ 조건부 안전 적용 (가드 필수):
 │     └── PERF-02 (Excel Zero-Allocation) -> if s.is_empty() { return None; } 필수 배치
 └── 🚨 보류 권고 (잠재적 미탐 위험):
       └── PERF-03 (ASCII eq_ignore_ascii_case) -> 유니코드 특수 대소문자(K↔K 등) 일관성을 위해 현행 유지
```

---

## 5. 마스터 버전 승인 판정 및 단계별 로드맵

### 종합 판정: **[조건부 승인 (Conditionally Approved for Master Release)]**

1. **현재 v5.8.9 배포 적합성**:
   - 기존의 모든 핵심 기능(356개 단위/통합 테스트 통과, Clippy 0 warning, 세션 복구 무결성)이 결점 없이 통과하고 있으므로, **일반적인 Windows 데스크톱 사용자 대상 마스터 버전 배포는 안전합니다.**
2. **무결성 우선 3단계 조치 계획 (Safe Roadmap)**:
   - **1단계: 마스터 버전 릴리스 (v5.8.9 즉시 배포 가능)**:
     - 현행 안정화 빌드(`dist/StringFinder.exe`) 그대로 배포 선언.
   - **2단계: 보안 및 안정성 패치 (v5.8.10 권장)**:
     - [SEC-01] 실행 파일 직접 실행 차단 게이트키퍼
     - [SEC-02 / BUG-01] `doctor.py` 임시 파일 고유화 및 Linux `xdg-open` 지원 (`file_helper`로 일원화)
   - **3단계: 안전한 엔진 고도화 릴리스 (v5.9.0 권장)**:
     - [PERF-01] Rust 다중 경로 `WalkBuilder.add()` 단일화 (스레드 경합 해소, 무결성 0% 영향)
     - [PERF-02] Excel 셀 `s.is_empty()` 가드를 엄수한 제로카피 매치 적용
     - [PERF-04] `ConfigManager.get()` 불변 타입 fast-path 적용
     - [PERF-06 / 07] 핸들 복제 제거 및 슬라이스 순회
     - ❌ **[PERF-03 제외]** 유니코드 무결성을 위해 `eq_ignore_ascii_case`는 반영하지 않고 현행 유지
