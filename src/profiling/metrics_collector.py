"""
메트릭 수집기

검색 중 시스템 메트릭(CPU, 메모리, 디스크 I/O)과 검색 진행 상황을 추적합니다.
"""

import psutil
import time
from dataclasses import dataclass
from typing import List


@dataclass
class Metrics:
    """단일 시점의 메트릭 스냅샷"""

    timestamp: float
    cpu_percent: float
    memory_mb: float
    disk_io_mb: float
    files_processed: int
    matches_found: int


class MetricsCollector:
    """
    실시간 성능 메트릭 수집기

    검색 중 주기적으로 호출하여 시스템 리소스 사용량과 검색 진행 상황을 추적합니다.
    """

    def __init__(self):
        self.metrics: List[Metrics] = []
        self.start_time = None
        try:
            self.process = psutil.Process()
        except Exception:
            self.process = None

    def start(self):
        """메트릭 수집 시작"""
        self.start_time = time.time()
        self.metrics.clear()

        # CPU 측정 초기화 (첫 호출은 0을 반환하므로 미리 호출)
        if self.process:
            try:
                self.process.cpu_percent()
            except Exception:
                pass

    def collect(self, files_processed: int = 0, matches_found: int = 0):
        """
        현재 시점의 메트릭 수집

        Args:
            files_processed: 처리된 파일 수
            matches_found: 발견된 매칭 수
        """
        if not self.process or self.start_time is None:
            return

        try:
            self.metrics.append(
                Metrics(
                    timestamp=time.time() - self.start_time,
                    cpu_percent=self.process.cpu_percent(),
                    memory_mb=self.process.memory_info().rss / 1024 / 1024,
                    disk_io_mb=self._get_disk_io(),
                    files_processed=files_processed,
                    matches_found=matches_found,
                )
            )
        except Exception:
            # psutil 에러 시 조용히 무시
            pass

    def _get_disk_io(self) -> float:
        """디스크 I/O 총량 (MB) 계산"""
        try:
            io_counters = self.process.io_counters()
            return (io_counters.read_bytes + io_counters.write_bytes) / 1024 / 1024
        except Exception:
            return 0.0

    def get_summary(self) -> dict:
        """
        수집된 메트릭의 요약 통계 반환

        Returns:
            dict: 검색 시간, 평균 CPU, 최대 메모리, 평균 속도 등
        """
        if not self.metrics:
            return {}

        duration = self.metrics[-1].timestamp

        return {
            "duration": duration,
            "avg_cpu": sum(m.cpu_percent for m in self.metrics) / len(self.metrics),
            "peak_memory": max(m.memory_mb for m in self.metrics),
            "total_disk_io": self.metrics[-1].disk_io_mb,
            "avg_speed": self.metrics[-1].files_processed / duration if duration > 0 else 0,
            "total_files": self.metrics[-1].files_processed,
            "total_matches": self.metrics[-1].matches_found,
        }
