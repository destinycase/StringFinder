"""
성능 분석기

검색 히스토리를 저장하고 성능 트렌드를 분석합니다.
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from utils.logger import logger
from utils.app_strings import AppStrings


class PerformanceAnalyzer:
    """
    검색 성능 히스토리 관리 및 분석

    검색 완료 시마다 성능 리포트를 저장하고, 과거 데이터를 기반으로 트렌드를 분석합니다.
    """

    def __init__(self, config_manager):
        self.config_manager = config_manager
        self.history_file = Path(config_manager.config_dir) / "profiling_history.json"

    def save_report(self, report: dict):
        """
        검색 리포트 저장

        Args:
            report: 메트릭 요약 딕셔너리
        """
        try:
            history = self._load_history()
            history.append({**report, "timestamp": datetime.now().isoformat()})

            # 최근 100개만 유지
            history = history[-100:]

            with open(self.history_file, "w", encoding="utf-8") as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(AppStrings.LOG_PRF_SAVE_FAIL.format(e))

    def get_trend(self, days: int = 30) -> dict:
        """
        성능 트렌드 분석

        Args:
            days: 분석할 기간 (일)

        Returns:
            dict: 평균 속도, 평균 메모리, 총 검색 횟수 등
        """
        history = self._load_history()
        if not history:
            return None

        cutoff = datetime.now() - timedelta(days=days)

        recent = [h for h in history if datetime.fromisoformat(h["timestamp"]) > cutoff]

        if not recent:
            return None

        return {
            "avg_speed": sum(h.get("avg_speed", 0) for h in recent) / len(recent),
            "avg_memory": sum(h.get("peak_memory", 0) for h in recent) / len(recent),
            "avg_duration": sum(h.get("duration", 0) for h in recent) / len(recent),
            "total_searches": len(recent),
            "data_points": recent,
        }

    def get_last_report(self) -> dict:
        """가장 최근 검색 리포트 반환"""
        history = self._load_history()
        return history[-1] if history else None

    def _load_history(self) -> list:
        """히스토리 파일 로드"""
        if not self.history_file.exists():
            return []

        try:
            with open(self.history_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(AppStrings.LOG_PRF_LOAD_FAIL.format(e))
            return []
