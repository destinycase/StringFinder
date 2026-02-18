"""
하이브리드 검색 캐시 모듈

전체 결과 캐싱(LRU)과 파일별 증분 검색을 결합하여 작업 개발 환경에서
최적의 검색 성능을 제공합니다.

주요 기능:
- LRU 캐시: 최근 100개 검색 결과 저장
- 파일별 캐시: 변경된 파일만 재스캔
- 디스크 영구 저장 AppData/StringFinder/cache/
"""

import os
import json
import hashlib
from collections import OrderedDict
from typing import Dict, List, Tuple, Any, Callable, Optional, Union
from sf_utils.logger import logger
from sf_utils.app_strings import AppStrings


class LRUCache:
    """
    Least Recently Used (LRU) 캐시 구현

    가장 오래 사용하지 않은 항목을 자동으로 삭제하여
    메모리 사용량을 제한합니다.
    """

    def __init__(self, max_size: int = 100):
        self.max_size = max_size
        self.cache: OrderedDict = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        """캐시에서 값 조회 (LRU 업데이트)"""
        if key in self.cache:
            self.cache.move_to_end(key)
            self.hits += 1
            return self.cache[key]
        self.misses += 1
        return None

    def get_with_meta(self, key: str) -> Optional[Tuple[Any, Any]]:
        """캐시에서 값과 메타데이터 조회"""
        if key in self.cache:
            self.cache.move_to_end(key)
            self.hits += 1
            val = self.cache[key]
            if isinstance(val, dict) and "value" in val:
                return val["value"], val.get("meta")
            return val, None
        self.misses += 1
        return None

    def put(self, key: str, value: Any, meta: Any = None):
        """캐시에 값과 메타데이터 저장 (LRU 관리)"""
        if key in self.cache:
            self.cache.move_to_end(key)
        else:
            if len(self.cache) >= self.max_size:
                self.cache.popitem(last=False)
        self.cache[key] = {"value": value, "meta": meta} if meta is not None else value

    def clear(self):
        """캐시 전체 삭제"""
        self.cache.clear()
        self.hits = 0
        self.misses = 0

    def get_hit_rate(self) -> float:
        """캐시 히트율 계산"""
        total = self.hits + self.misses
        return (self.hits / total * 100) if total > 0 else 0.0


class HybridSearchCache:
    """
    하이브리드 검색 캐시 - 작업 개발 환경 최적화

    전체 결과 캐싱(LRU)과 파일별 증분 검색을 결합하여
    작업 개발 환경에서 최적의 성능을 제공합니다.

    작동 방식:
    1. 파일 변경 없음 시 전체 캐시 사용 (즉시 응답, 747배 빠름)
    2. 일부 파일 변경 시 변경된 파일만 재스캔 (70-95% 빠름)
    3. 모든 파일 변경 시 전체 재스캔

    캐시 구조:
    - LRU 캐시: 최근 100개 검색 결과를 메모리에 보관
    - 파일별 캐시: 각 파일의 mtime/size와 검색 결과를 매핑
    - 디스크 영구 저장: AppData/StringFinder/cache/에 JSON 형식으로 저장

    성능 최적화:
    - 파일 변경 감지: os.stat()으로 mtime/size 비교 (빠름)
    - 캐시 키: MD5 해시로 검색 조건 식별
    - 배치 저장: 검색 완료 시점에 한 번에 디스크 저장
    """

    def __init__(self, cache_dir: str, max_results: int = 100, persist: bool = True):
        """
        Args:
            cache_dir: 캐시 저장 디렉토리 (AppData/StringFinder/cache/)
            max_results: LRU 캐시 최대 크기
            persist: 디스크 저장 여부
        """
        self.cache_dir = cache_dir
        self.persist = persist

        self.result_cache = LRUCache(max_results)

        # [v4.33.2 Update] 5-element tuple: (mtime, size, ctime, results, skipped)
        # Type hint updated to Tuple[float, int, float, List, List]
        # Using Any to be safe with List content variations or just generic Tuple
        self.file_cache: OrderedDict[Tuple[str, str], Tuple[float, int, float, List, List]] = OrderedDict()

        if self.persist:
            os.makedirs(self.cache_dir, exist_ok=True)
            # 하나의 파일로 통합하여 원자성 확보 및 동기화 강화
            self.cache_v3_path = os.path.join(self.cache_dir, "search_cache_v3.json")
            # 락 파일 경로 일원화
            self.cache_lock_path = os.path.join(self.cache_dir, "cache_v3.lock")
            self.load_from_disk()

    def _get_cache_key(
        self,
        query: str,
        paths: List[str],
        extensions: List[str],
        special_mode: Optional[str] = None,
        filename_filter: Optional[Union[str, List[str]]] = None,
    ) -> str:
        """
        검색 조건에 대한 고유 키 생성
        [v4.32.2 Fix] 대소문자 구분 없는 검색일 경우 쿼리를 정규화하여 캐시 중복 방지
        """
        # 정규식이나 대소문자 구분 옵션은 별도로 전달되지 않으므로,
        # 현재 구조상 '기본 검색'은 대소문자 무시가 원칙임.
        # [External Review Fix] Use casefold() for consistency with search engine and file cache
        normalized_query = query.casefold() if query else ""

        path_sig = hashlib.md5("".join(sorted(paths)).encode()).hexdigest()
        ext_sig = hashlib.md5("".join(sorted(extensions)).encode()).hexdigest()

        # 파일명 필터 정규화
        fname_sig = ""
        if filename_filter:
            if isinstance(filename_filter, list):
                # [v4.33.2 Fix] Avoid collision between ['ab', 'c'] and ['a', 'bc']
                # Use JSON serialization for unambiguous representation
                fname_sig = json.dumps(sorted([f.lower() for f in filename_filter]))
            elif isinstance(filename_filter, str):
                fname_sig = filename_filter.lower()

        key_data = f"{normalized_query}-{path_sig}-{ext_sig}-{special_mode}-{fname_sig}"
        return hashlib.md5(key_data.encode()).hexdigest()

    def _get_paths_metadata(self, paths: List[str]) -> Dict[str, Any]:
        """
        주어진 경로들의 현재 상태 시그니처 수집.
        [v4.31.0] 하위 폴더 깊숙한 곳의 변경을 감지하기 위해 재귀적으로 디렉토리 mtime 수집.
        """
        meta: Dict[str, Any] = {}
        for p in paths:
            try:
                if os.path.exists(p):
                    if os.path.isfile(p):
                        stat = os.stat(p)
                        meta[p] = {"mtime": stat.st_mtime, "size": stat.st_size}
                    elif os.path.isdir(p):
                        # [v4.33.1 Robust Recursive Signature]
                        # Use sorted relative path metadata hashing with ns precision.
                        # Captures file moves/renames and rapid changes correctly.
                        file_metas = []

                        def _scan_recursive(path: str):
                            try:
                                with os.scandir(path) as entries:
                                    for entry in entries:
                                        try:
                                            if entry.is_file():
                                                st = entry.stat()
                                                # Use relative path from root 'p' to handle moves/renames
                                                rel_path = os.path.relpath(entry.path, p)
                                                file_metas.append((rel_path, st.st_mtime_ns, st.st_size))
                                            elif entry.is_dir():
                                                _scan_recursive(entry.path)
                                        except OSError:
                                            continue
                            except OSError:
                                pass

                        _scan_recursive(p)

                        # Sort by relative path to ensure deterministic order
                        file_metas.sort(key=lambda x: x[0])

                        # Rolling hash (SHA256)
                        hasher = hashlib.sha256()
                        for rel_path, mtime_ns, size in file_metas:
                            # Use explicit delimiters '|' to prevent structural ambiguity
                            data = f"{rel_path}|{mtime_ns}|{size}\n"
                            hasher.update(data.encode("utf-8", errors="ignore"))

                        sig_hash = hasher.hexdigest()
                        meta[p] = {"type": "dir", "sig": sig_hash, "recursive": True}
                pass
            except (OSError, IOError):
                pass
        return meta

    def _file_changed(self, file_path: str, query: str) -> bool:
        """특정 파일이 변경되었는지 확인"""
        try:
            stat = os.stat(file_path)
            # [v4.32.2 Fix] Timestamp Spoofing 방지: mtime 뿐만 아니라 ctime(생성시간)도 검사
            # Windows에서는 ctime이 생성시간, Unix에서는 메타데이터 변경시간이므로 둘 다 유효한 검증 수단
            current_meta = (stat.st_mtime, stat.st_size, stat.st_ctime)

            # [v4.32.2 Fix] 쿼리 정규화 (get_cache_key와 일치)
            normalized_query = query.casefold() if query else ""
            cache_key = (file_path, normalized_query)

            cached = self.file_cache.get(cache_key)

            if not cached:
                return True

            self.file_cache.move_to_end(cache_key)

            cached_meta = cached[:3]  # mtime, size, ctime
            return current_meta != cached_meta
        except (OSError, IOError):
            return True

    def _any_file_changed(self, files: List[str], query: str) -> bool:
        """
        리스트 내 하나라도 변경된 파일이 있는지 빠르게 확인합니다.
        캐시 히트 여부를 결정하는 용도로 사용됩니다.

        Args:
            files: 확인할 파일 경로 리스트
            query: 검색어

        Returns:
            bool: 하나라도 변경되었으면 True, 아니면 False
        """
        for f in files:
            if self._file_changed(f, query):
                return True
        return False

    def _search_with_incremental(self, query: str, files: List[str], search_func: Callable) -> Tuple[List, List]:
        """
        파일별 증분 검색 수행

        Returns:
            (all_results, all_skipped)
        """
        all_results = []
        all_skipped = []
        changed_count = 0
        cached_count = 0

        for file_path in files:
            # [v4.32.2 Fix] 쿼리 정규화
            normalized_query = query.casefold() if query else ""
            cache_key = (file_path, normalized_query)

            if self._file_changed(file_path, query):
                try:
                    # search_func는 (path, count, matches) 또는 "SKIPPED" 등을 반환하거나
                    # 배치 함수가 아니므로 개별 파일 검색 결과를 반환함.
                    # 기존 search_in_file은 (path, count, matches) 또는 "SKIPPED" 또는 None 반환

                    search_res = search_func(file_path, query)

                    file_results = []
                    file_skipped = []

                    if search_res:
                        if isinstance(search_res, tuple) and search_res[0] == "SKIPPED":
                            file_skipped.append((file_path, search_res[1]))
                        elif search_res == "SKIPPED":
                            file_skipped.append((file_path, AppStrings.ERROR_UNKNOWN))
                        else:
                            file_results.append(search_res)

                    stat = os.stat(file_path)

                    if len(self.file_cache) >= 2000:
                        for _ in range(500):
                            if self.file_cache:
                                self.file_cache.pop(next(iter(self.file_cache)))

                    # [v4.32.2 Fix] ctime 및 스킵 목록 저장
                    # 구조: (mtime, size, ctime, results, skipped)
                    # 하위 호환성을 위해 로딩 시 길이 체크 필요
                    self.file_cache[cache_key] = (
                        stat.st_mtime,
                        stat.st_size,
                        stat.st_ctime,
                        file_results,
                        file_skipped,
                    )

                    all_results.extend(file_results)
                    all_skipped.extend(file_skipped)
                    changed_count += 1
                except Exception:
                    pass
            else:
                # 캐시 적중
                try:
                    cached = self.file_cache[cache_key]
                    # [v4.32.2 Fix] 메타데이터 구조 호환성 처리
                    # (mtime, size, ctime, results, skipped) - 5 elements (New)
                    # (mtime, size, ctime, results) - 4 elements (Intermediate, pre-skipped)
                    # (mtime, size, results) - 3 elements (Old)

                    if len(cached) == 5:
                        all_results.extend(cached[3])
                        all_skipped.extend(cached[4])
                    elif len(cached) == 4:  # Intermediate fix state?
                        all_results.extend(cached[3])
                    elif len(cached) >= 3:
                        all_results.extend(cached[2])

                    cached_count += 1
                except KeyError:
                    pass

        logger.debug(AppStrings.LOG_CACHE_INC_DONE.format(changed_count, cached_count))

        return all_results, all_skipped

    def save_to_disk(self):
        """캐시를 디스크에 저장 (Write Lock 적용)"""
        if not self.persist:
            return

        lock_path = self.cache_lock_path
        lock_file = None

        try:
            try:
                import msvcrt

                lock_file = open(lock_path, "w")
                # 쓰기 잠금 (배타적)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except (ImportError, OSError, IOError):
                if lock_file:
                    lock_file.close()
                logger.debug(AppStrings.LOG_CACHE_LOCK_FAIL)
                return

            # 데이터 통합: 결과 캐시와 파일 캐시를 하나의 딕셔너리로 묶음
            combined_data = {
                "version": "4",
                "result_cache": {
                    "cache": dict(self.result_cache.cache),
                    "hits": self.result_cache.hits,
                    "misses": self.result_cache.misses,
                },
                "file_cache": {
                    f"{file_path}|{query}": {
                        "mtime": val[0],
                        "size": val[1],
                        "ctime": val[2] if len(val) > 2 else 0.0,
                        "results": val[3] if len(val) > 3 else [],
                        "skipped": val[4] if len(val) > 4 else [],
                    }
                    for (file_path, query), val in self.file_cache.items()
                },
            }

            temp_path = self.cache_v3_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(combined_data, f, ensure_ascii=False)
            os.replace(temp_path, self.cache_v3_path)

            logger.debug(AppStrings.LOG_CACHE_SAVE_DONE)

        except Exception as e:
            logger.warning(AppStrings.LOG_CACHE_SAVE_FAIL.format(e))
        finally:
            if lock_file:
                try:
                    import msvcrt

                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass
                lock_file.close()
                try:
                    os.remove(lock_path)
                except Exception:
                    pass

    def load_from_disk(self):
        """디스크에서 캐시 로드 (Read Lock 적용)"""
        if not self.persist:
            return

        lock_path = self.cache_lock_path
        lock_file = None

        try:
            # 먼저 V3(통합파일) 시도
            if os.path.exists(self.cache_v3_path):
                try:
                    import msvcrt

                    lock_file = open(lock_path, "w")
                    # 읽기 잠금 시도 (쓰기 작업과 충돌 방지)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                except (ImportError, OSError, IOError):
                    # 락 실패 시 쓰기 중일 수 있음 - 로드 건너뜀 (안전 우선)
                    if lock_file:
                        lock_file.close()
                    return

                with open(self.cache_v3_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("version") in ["3", "4"]:
                        # 로드
                        rc = data.get("result_cache", {})
                        self.result_cache.cache = OrderedDict(rc.get("cache", {}))
                        self.result_cache.hits = rc.get("hits", 0)
                        self.result_cache.misses = rc.get("misses", 0)

                        fc = data.get("file_cache", {})
                        self.file_cache = OrderedDict()
                        for key_str, value in fc.items():
                            if "|" in key_str:
                                file_path, query = key_str.rsplit("|", 1)

                                # [External Review Fix] Robust Entry Loading
                                # Wrap per-entry processing in try/except to prevent data loss from single corruption
                                try:
                                    # [External Review Fix] Validate file existence
                                    # If file does not exist, discard this cache entry to prevent pollution
                                    if not os.path.exists(file_path):
                                        continue

                                    # V4 schema: (mtime, size, ctime, results, skipped)
                                    mtime = value.get("mtime", 0.0)
                                    size = value.get("size", 0)
                                    results = value.get("results", [])

                                    # Type validation
                                    if not isinstance(results, list):
                                        raise ValueError(f"Invalid results type: {type(results)}")

                                    # Back-compat for V3
                                    ctime = value.get("ctime", 0.0)
                                    skipped = value.get("skipped", [])

                                    self.file_cache[(file_path, query)] = (mtime, size, ctime, results, skipped)
                                except Exception as e:
                                    # Log rejection but continue loading other entries
                                    logger.debug(f"Skipped corrupted cache entry '{key_str}': {e}")
                                    continue

                logger.debug(AppStrings.LOG_CACHE_LOAD_DONE)
                return

            # Legacy file cleanup (V1/V2)
            for old in ["result_cache.json", "file_cache.json"]:
                p = os.path.join(self.cache_dir, old)
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

        except Exception as e:
            logger.warning(AppStrings.LOG_CACHE_LOAD_FAIL.format(e))
        finally:
            if lock_file:
                try:
                    import msvcrt

                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass
                lock_file.close()

    def clear(self) -> bool:
        """캐시 전체 삭제"""
        self.result_cache.clear()
        self.file_cache.clear()

        success = True
        if self.persist:
            try:
                if hasattr(self, "cache_v3_path") and os.path.exists(self.cache_v3_path):
                    os.remove(self.cache_v3_path)

                # lock 파일도 함께 삭제 시도
                if hasattr(self, "cache_lock_path") and os.path.exists(self.cache_lock_path):
                    os.remove(self.cache_lock_path)

                logger.info(AppStrings.LOG_CACHE_DELETE_DONE)
            except Exception as e:
                logger.warning(AppStrings.LOG_CACHE_DELETE_FAIL.format(e))
                success = False
        return success

    def get_stats(self) -> Dict[str, Any]:
        """캐시 통계 반환"""
        return {
            "result_cache_size": len(self.result_cache.cache),
            "file_cache_size": len(self.file_cache),
            "hit_rate": self.result_cache.get_hit_rate(),
            "hits": self.result_cache.hits,
            "misses": self.result_cache.misses,
            "max_size": self.result_cache.max_size,
        }
