# [RFC] 시스템 리소스 가드 및 레거시 설정 정합성 개선 제안서
(Architecture Consistency & Legacy Cleanup RFC)

- **문서 번호:** RFC-2026-001
- **문서 버전:** 1.2 (내부 검증 및 수정안 반영)
- **작성 일자:** 2026-09-05
- **상태:** 원안 검토 완료 / 수정안 반영 (Partially Accepted)
- **대상 모듈:**
  - [`src/sf_utils/resource_guard.py`](file:///d:/Project/StringFinder/src/sf_utils/resource_guard.py)
  - [`src/ui/settings_dialog.py`](file:///d:/Project/StringFinder/src/ui/settings_dialog.py)
  - [`src/sf_utils/constants.py`](file:///d:/Project/StringFinder/src/sf_utils/constants.py)
  - [`src/core/worker.py`](file:///d:/Project/StringFinder/src/core/worker.py)
  - [`tests/test_perf_check.py`](file:///d:/Project/StringFinder/tests/test_perf_check.py)

---

> [!IMPORTANT]
> 이 문서의 1~4장은 외부 리포트 원안을 추적하기 위해 보존한 내용입니다. 내부 코드·테스트 검증 결과, IS-01과 IS-02의 원인 해석 및 원안 수정 방법은 그대로 승인하지 않았습니다. 실제 적용 기준과 결과는 5장을 우선합니다.

## 1. 배경 및 요약 (Executive Summary)

StringFinder는 v5.0 이후 Rust 네이티브 엔진(`sf_engine`)을 도입하여 Zero-Copy `mmap` 및 SAX 스트리밍 파서 기반의 고성능 아키텍처로 진화했습니다.

그러나 전체 코드베이스를 엄격하게 전수 조사한 결과, **과거 순수 Python 시절에 작성되었던 과잉 방어 로직과 레거시 설정들이 현대화된 Rust 코어와 불일치**를 일으키며 다음 **3가지 실질적인 결함**을 유발하고 있음을 확인했습니다:

1. **타 프로그램 간섭으로 인한 허위 검색 차단 (False Positive Abort):**
   StringFinder는 램을 거의 쓰지 않음에도 외부 프로그램(Chrome, Docker 등) 때문에 시스템 잔여 램이 511MB 이하가 되면 검색이 강제 중단됨.
2. **UI 환경설정의 유령 설정(Ghost Settings) 2건:**
   고급 설정에 존재하는 "소형 파일 크기"와 "JSON Mmap 기준" 설정이 실제 검색을 전담하는 Rust 엔진에는 전혀 전달되지도 않고 쓰이지도 않음.
3. **단위 테스트 환경 오염 취약성 (Test Isolation 결함):**
   사용자의 로컬 `config.json` 설정값이 테스트 코드에 그대로 스며들어 회귀 테스트(`test_perf_check.py`)가 깨지는 결함.

본 문서는 위 3가지 이슈에 대한 코드 레벨 검증 근거와 구체적인 개선 방안을 정의합니다.

---

## 2. 세부 이슈 분석 및 코드 레벨 검증 근거

```
┌────────────────────────────────────────────────────────────────────────────────────────┐
│                              식별된 아키텍처 불일치 이슈 요약                            │
├─────────┬──────────────────┬──────────────────────┬────────────────────────────────────┤
│ ID      │ 분류             │ 대상 파일 및 라인    │ 핵심 결함 내용                     │
├─────────┼──────────────────┼──────────────────────┼────────────────────────────────────┤
│ **IS-01**│ 🛡️ 허위 차단     │ `resource_guard.py:65`│ 외부 앱 때문에 512MB 미만 시 차단   │
│ **IS-02**│ 👻 유령 설정     │ `settings_dialog:280`│ Rust 엔진에 미전달되는 무효 설정 2건│
│ **IS-03**│ 🧪 테스트 격리   │ `test_perf_check.py` │ 로컬 config.json 오염 시 테스트 실패│
└─────────┴──────────────────┴──────────────────────┴────────────────────────────────────┘
```

---

### 🔍 [IS-01] 시스템 잔여 메모리 기반 검색 차단 (허위 경보)

#### 1) 현상 및 코드 증거
* **코드 위치:** [`src/sf_utils/resource_guard.py:62-67`](file:///d:/Project/StringFinder/src/sf_utils/resource_guard.py#L62-L67)
```python
def memory_pressure_detected(snapshot: Dict[str, int] | None = None) -> bool:
    ...
    minimum_available = Constants.MIN_AVAILABLE_MEMORY_BYTES # 512MB
    process_limit = int(values["total"] * Constants.PROCESS_MEMORY_THRESHOLD_PERCENT / 100)
    return (
        values["available"] < minimum_available  # <- [결함] 타 프로그램의 영향으로 차단 발동
        or (values["process_rss"] > 0 and values["process_rss"] >= process_limit)
    )
```
* **단위 테스트 증명:** [`tests/test_resource_guard.py:15-17`](file:///d:/Project/StringFinder/tests/test_resource_guard.py#L15-L17)
```python
# StringFinder가 램을 단 1바이트(process_rss: 1)만 써도,
# 시스템 가용 램이 511MB이면 강제 검색 차단(True) 발생!
assert memory_pressure_detected(
    {"available": 511 * 1024 * 1024, "total": total, "process_rss": 1, "system_percent": 20}
)
```
* **주석과 코드의 모순:** [`src/sf_utils/constants.py:143-144`](file:///d:/Project/StringFinder/src/sf_utils/constants.py#L143-L144) 주석에는 *"시스템 전체 사용률만 보면 다른 앱 때문에 정상 검색이 중단될 수 있다"*고 명시했으나, 실제 코드에는 `available < 512MB`가 그대로 살아있어 경고했던 부작용이 그대로 발생합니다.

---

### 🔍 [IS-02] UI 고급 설정의 "유령 설정(Ghost Settings)" 2건

#### 1) 현상 및 코드 증거
사용자가 `설정 -> 환경설정 -> 고급 설정`에서 값을 조정할 수 있는 8개의 스핀박스 중 아래 2개 설정은 **실제 검색을 전담하는 Rust 엔진(`sf_engine`)에 전혀 파라미터로 전달되지 않습니다.**

* **코드 위치:** [`src/ui/settings_dialog.py:280-287`](file:///d:/Project/StringFinder/src/ui/settings_dialog.py#L280-L287)
  1. `Constants.CONFIG_KEY_MAX_SMALL_FILE_SIZE`: **"소형 파일 최대 크기 (기본: 10MB)"**
  2. `Constants.CONFIG_KEY_JSON_MMAP_THRESHOLD`: **"JSON 스트리밍 Mmap 전환 기준 (기본: 5MB)"**
* **문제점:**
  - Rust 엔진 진입점인 `sf_engine.search_dir` 및 `search_file`은 오직 `max_per_file`, `max_check_cells`, `max_json_depth`, `max_json_size` 4개만 수신하며, 소형 파일 및 JSON mmap 크기는 Rust 내부 16KB 기준으로 동작합니다.
  - 이 두 설정은 과거 순수 Python 폴백 경로([`search_engine.py:1500, 2055`](file:///d:/Project/StringFinder/src/core/search_engine.py#L1500))에만 일부 잔존해 있어, **일반 사용자의 실제 검색에는 0%의 영향도 미치지 않는 유령 설정**입니다.

---

### 🔍 [IS-03] `test_perf_check.py`의 테스트 격리(Isolation) 누락

#### 1) 현상 및 코드 증거
* **코드 위치:** [`tests/test_perf_check.py:38`](file:///d:/Project/StringFinder/tests/test_perf_check.py#L38)
```python
result = search_in_file(str(file_path), "target", use_complex_search=False)
...
assert result[1] >= 5001
assert len(result[2]) <= 5001  # <- 기본값 5,000건을 하드코딩 가정
```
* **문제점:**
  `search_in_file`은 내부에서 `ConfigManager().get_advanced_settings()`를 통해 **사용자의 실제 PC에 저장된 `config.json`을 읽어옵니다.**
  만약 사용자가 앱 설정에서 `max_per_file_matches`를 10,000으로 올려두었다면, 테스트 환경에서 10,000개가 반환되어 `assert len <= 5001`에서 테스트가 실패합니다.
* **원인:** 테스트 환경이 사용자 로컬 설정 파일로부터 격리(Isolation/Mocking)되지 않았습니다.

---

## 3. 권장 개선 방안 (Actionable Proposals)

### 💡 [제안 1] `resource_guard.py` 개선 (타 프로그램 간섭 배제)
시스템 전체 잔여 램 검사(`available < 512MB`)를 제거하고, **StringFinder 자체 프로세스 트리 점유율만 감시**하도록 단순화합니다.

```python
# src/sf_utils/resource_guard.py 수정안
def memory_pressure_detected(snapshot: Dict[str, int] | None = None) -> bool:
    values = snapshot or memory_snapshot()
    if not values.get("valid", False):
        return False

    # [개선] 시스템 가용 램 검사는 제외하여 타 프로그램 간섭 차단
    # StringFinder 자체 프로세스 트리 RSS가 전체 RAM의 60%를 넘을 때만 비상 정지
    process_limit = int(values["total"] * Constants.PROCESS_MEMORY_THRESHOLD_PERCENT / 100)
    return values["process_rss"] > 0 and values["process_rss"] >= process_limit
```

---

### 💡 [제안 2] UI 고급 설정 정리 (유령 설정 제거)
* [`src/ui/settings_dialog.py`](file:///d:/Project/StringFinder/src/ui/settings_dialog.py)에서 실제 Rust 엔진에 반영되지 않는 2개 스핀박스(`max_small_file_size`, `json_mmap_threshold`)를 UI에서 제거하여 사용자 혼란을 방지합니다.
* 설정 파일(`config.json`)의 마이그레이션을 지원하여 기존 설정과의 하위 호환성을 유지합니다.

---

### 💡 [제안 3] `test_perf_check.py` 환경 격리 (Test Isolation Fix)
다른 테스트들(`test_config_manager.py` 등)과 동일하게 `tmp_path` 기반의 임시 설정 디렉토리를 주입하여 사용자 로컬 설정에 영향을 받지 않도록 격리합니다.

```python
# tests/test_perf_check.py 수정안
def test_performance_many_matches_on_one_line(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    ConfigManager._instance = None
    ...
```

---

## 4. 기대 효과 및 마일스톤

| 작업 항목 | 난이도 | 효과 |
| :--- | :---: | :--- |
| **1. Resource Guard 단순화** | 🟢 쉬움 | 타 프로그램 고점유 시 억울한 검색 차단 완전 해결 |
| **2. UI 유령 설정 2건 제거** | 🟢 쉬움 | 설정 UI의 투명성 및 아키텍처 정합성 확보 |
| **3. 성능 테스트 격리 적용** | 🟢 쉬움 | 어떤 로컬 환경에서도 `pytest` 100% 무결점 통과 보장 |

원안의 “50줄 이내이므로 매우 안전하다”는 평가는 채택하지 않습니다. 메모리 중단 조건과 FFI 오류 코드는 변경량보다 영향 범위가 중요하므로, 아래 수정안처럼 사유를 분리하고 실제 Rust 바이너리 통합 테스트와 성능 측정을 통과해야 합니다.

---

## 5. 내부 검증 결론 및 반영 결과 (Normative)

### IS-01 — 원안 반려, 안전 경계 유지

- `available < 512MB` 조건은 외부 프로그램의 영향을 받지만 실제 시스템 메모리 고갈 직전의 할당 실패와 OS 불안정을 막는 마지막 방어선이므로 제거하지 않았습니다.
- StringFinder 프로세스 트리 RSS 60% 기준과 검사 주기도 변경하지 않았습니다.
- 대신 더 직접적인 결함을 수정했습니다. 기존 Rust 엔진은 설정된 JSON 크기 한도를 넘은 파일에도 `ERR_MEMORY_GUARD`를 사용했고, Python worker가 이를 시스템 메모리 고갈로 오인해 전체 검색을 중단할 수 있었습니다.
- 파일 단위 JSON 크기 제한은 새 코드 `ERR_JSON_SIZE_LIMIT`로 분리했습니다. `ERR_MEMORY_GUARD|Large JSON`을 반환하는 구버전 확장도 파일 단위 제한으로 호환 처리합니다. 전체 중단은 실제 `ERROR_MEMORY_CRITICAL` 사유에만 적용합니다.

### IS-02 — 유령 설정 판단 반려, UI 명칭·설명 수정

- `max_small_file_size`와 `json_mmap_threshold`는 Rust 기본 검색에 전달되지 않지만 Python 정밀 검색 경로에서 실제로 사용되므로 무효 설정이 아닙니다.
- 설정 키, 값, 기본값과 범위는 그대로 유지해 설정 마이그레이션과 동작 변경을 피했습니다.
- UI에서는 두 항목을 **정밀 검색 전용 처리 기준**으로 묶고 실제 동작에 맞게 다음처럼 표시합니다.
  - `일반 텍스트 소형 파일 경로 기준`: 임계치 미만에서 64KB 인코딩 검사와 줄 단위 소형 파일 경로 사용
  - `JSON Mmap 읽기 전환 크기`: 임계치 이상에서 mmap으로 읽되 JSON 분석은 전체 문서를 대상으로 수행
- “직접 읽기”, “JSON 스트리밍 mmap”처럼 실제 구현을 과장하거나 오해하게 하는 표현은 제거했습니다.

### IS-03 — 유효, 기존 변경으로 해결됨

- 사용자 `config.json`의 `max_per_file_matches` 값이 성능 테스트에 유입되는 현상을 재현했습니다.
- `tests/test_perf_check.py`에서 필요한 설정 조회만 monkeypatch하는 방식으로 격리했습니다. 전역 APPDATA와 singleton을 재설정하는 방식보다 변경 범위가 작고 다른 테스트에 미치는 영향이 적습니다.

### 검증 계약

- 큰 JSON 뒤에 정상 JSON이 이어지는 혼합 검색에서 큰 파일만 `skipped`가 되고 정상 파일 결과가 유지되어야 합니다.
- 새 `ERR_JSON_SIZE_LIMIT`와 구버전 `ERR_MEMORY_GUARD|Large JSON`은 메모리 부족 팝업이나 전체 중단을 유발하지 않아야 합니다.
- 실제 시스템 메모리 압력은 검색을 중단하고 경고를 한 번만 발생시켜야 합니다.
- 두 정밀 검색 전용 설정은 Rust 옵션에 전달되지 않아야 하며, 한국어·영어 UI 설명은 mmap 읽기와 전체 문서 JSON 분석을 정확히 구분해야 합니다.

### 2026-09-05 실행 결과

- Rust 단위 테스트: 29개 통과
- Python 전체 회귀 테스트: 299개 통과, 1개 플랫폼 조건부 스킵, stress/chaos 10개 제외
- Rust Clippy(`-D warnings`)와 변경 Python 파일 Ruff: 통과
- 실제 릴리스 `sf_engine.pyd` 혼합 검색: 2MiB 제한 초과 JSON만 `skipped`, 정상 JSON 결과 유지
- 엔진 벤치마크를 독립 프로세스로 3회 실행한 중앙값은 기존 v5.8.3 기준선 대비 JSON 반복 매치 -5.4%, JSON 희소 매치 +1.7%, JSON 존재 확인 -6.5%, JSON 무매치 폴백 -4.7%였습니다. 모두 설정한 시간 회귀 허용 범위 안이며 결과 건수 회귀가 없었습니다.
- `tools/benchmark_engine.py`의 RSS 값은 검색 전후 delta이므로 순간 peak의 증거로 사용하지 않았습니다. 시스템 메모리 가드 임계치와 검색 알고리즘 자체를 변경하지 않은 것도 이 한계를 고려한 안전 조치입니다.
