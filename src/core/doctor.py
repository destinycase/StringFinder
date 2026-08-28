import os
import sys
import platform
import subprocess
from typing import List, Dict, Any

from sf_utils.constants import Constants
from sf_utils.app_strings import AppStrings
from sf_utils.logger import logger

class SystemDoctor:
    """시스템 환경을 진단하고 보고서를 생성하는 클래스."""

    def __init__(self):
        self.results: List[Dict[str, Any]] = []

    def run_full_diagnosis(self) -> str:
        """전체 진단을 수행하고 마크다운 형식의 보고서를 반환한다."""
        self.results = []
        
        self._check_basic_info()
        self._check_engine_integrity()
        self._check_hardware_acceleration()
        self._check_permissions()
        
        return self._generate_report()

    def _check_basic_info(self):
        info = {
            "category": AppStrings.DOCTOR_CATEGORY_BASIC_INFO,
            "items": [
                (AppStrings.DOCTOR_ITEM_OS, f"{platform.system()} {platform.release()} ({platform.machine()})"),
                (AppStrings.DOCTOR_ITEM_PYTHON, sys.version.split()[0]),
                (AppStrings.DOCTOR_ITEM_APP_VERSION, Constants.APP_VERSION)
            ]
        }
        self.results.append(info)

    def _check_engine_integrity(self):
        status = AppStrings.DOCTOR_STATUS_OK
        details = AppStrings.DOCTOR_ENGINE_LOADED
        try:
            from core.search_engine import HAS_RUST_ENGINE, _RUST_ENGINE_ERROR
            if not HAS_RUST_ENGINE:
                status = AppStrings.DOCTOR_STATUS_CRITICAL
                detail_reason = _RUST_ENGINE_ERROR if _RUST_ENGINE_ERROR else AppStrings.DOCTOR_ENGINE_UNKNOWN_ERROR
                details = AppStrings.DOCTOR_ENGINE_LOAD_FAILED.format(detail_reason)
        except Exception as e:
            status = AppStrings.DOCTOR_STATUS_CRITICAL
            details = AppStrings.DOCTOR_ENGINE_CHECK_FAILED.format(e)

        self.results.append({
            "category": AppStrings.DOCTOR_CATEGORY_ENGINE,
            "items": [(AppStrings.DOCTOR_ITEM_STATUS, status), (AppStrings.DOCTOR_ITEM_DETAILS, details)]
        })

    def _check_hardware_acceleration(self):
        # Windows 전용: IsProcessorFeaturePresent (AVX2 체크 등)
        # 여기서는 단순 예시로 아키텍처 정보 활용 또는 필요 시 특수 명령 사용
        cpu_info = platform.processor()
        self.results.append({
            "category": AppStrings.DOCTOR_CATEGORY_HARDWARE,
            "items": [
                (AppStrings.DOCTOR_ITEM_PROCESSOR, cpu_info),
                (
                    AppStrings.DOCTOR_ITEM_64BIT,
                    AppStrings.DOCTOR_STATUS_SUPPORTED
                    if sys.maxsize > 2**32
                    else AppStrings.DOCTOR_STATUS_NOT_SUPPORTED,
                )
            ]
        })

    def _check_permissions(self):
        app_data = os.getenv(Constants.ENV_APPDATA)
        config_dir = os.path.join(app_data, Constants.APP_NAME) if app_data else AppStrings.DOCTOR_UNKNOWN

        writable = AppStrings.DOCTOR_STATUS_UNKNOWN
        if os.path.exists(config_dir):
            writable = (
                AppStrings.DOCTOR_STATUS_WRITABLE
                if os.access(config_dir, os.W_OK)
                else AppStrings.DOCTOR_STATUS_NOT_WRITABLE
            )

        self.results.append({
            "category": AppStrings.DOCTOR_CATEGORY_PERMISSIONS,
            "items": [
                (AppStrings.DOCTOR_ITEM_CONFIG_DIR, config_dir),
                (AppStrings.DOCTOR_ITEM_WRITABLE, writable)
            ]
        })

    def _generate_report(self) -> str:
        import datetime
        report = [f"# {AppStrings.APP_TITLE} {AppStrings.LOG_SYS_DOCTOR_TITLE}\n"]
        report.append(
            f"{AppStrings.DOCTOR_REPORT_DATE}: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        
        for section in self.results:
            report.append(f"## {section['category']}")
            for key, value in section['items']:
                report.append(f"- **{key}**: {value}")
            report.append("")
        
        footer = f"\n---\n{AppStrings.DOCTOR_REPORT_FOOTER}"
        report.append(footer)
        return "\n".join(report)

def run_doctor_and_open():
    """진단을 실행하고 결과를 텍스트 파일로 저장한 후 메모장으로 연다."""
    doctor = SystemDoctor()
    report_content = doctor.run_full_diagnosis()
    
    # 임시 디렉토리에 보고서 저장
    import tempfile
    temp_path = os.path.join(tempfile.gettempdir(), "sf_doctor_report.md")
    
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(report_content)
        
        # 시스템 기본 편집기(메모장 등)로 열기
        if os.name == "nt":
            os.startfile(temp_path)
        else:
            subprocess.run(["open", temp_path])
        return True
    except Exception as e:
        logger.error(f"Failed to save or open doctor report: {e}")
        return False
