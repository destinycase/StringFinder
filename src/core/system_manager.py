import os
import glob
import time
import logging

from sf_utils.app_strings import AppStrings
from sf_utils.logger import logger


class SystemManager:
    """애플리케이션 로그 보관 정책을 적용합니다."""

    def cleanup_logs(self, log_dir, retention_config):
        """오래된 로그 파일을 정리합니다."""

        try:
            if not os.path.exists(log_dir):
                return
            log_pattern = os.path.join(log_dir, "stringfinder_*.log")
            all_log_files = glob.glob(log_pattern)
            if not all_log_files:
                return

            if not retention_config.get("enabled", False):
                max_files = 1
                max_days = 0
            else:
                max_files = retention_config.get("max_files", 5)
                max_days = retention_config.get("max_days", 7)

            # 수정일(mtime) 기준으로 정렬하여 보관 정책을 유지합니다.
            log_files = sorted(all_log_files, key=os.path.getmtime, reverse=True)
            cutoff_time = time.time() - (max_days * 86400)
            
            current_log_file = None
            root_logger = logging.getLogger("StringFinder")
            for h in root_logger.handlers:
                if isinstance(h, logging.FileHandler):
                    current_log_file = os.path.abspath(h.baseFilename)
                    break

            deleted_count = 0
            kept_count = 0
            for log_file in log_files:
                try:
                    abs_log = os.path.abspath(log_file)
                    if current_log_file and abs_log == current_log_file:
                        continue
                    
                    if kept_count >= max_files or os.path.getmtime(log_file) < cutoff_time:
                        os.remove(log_file)
                        deleted_count += 1
                    else:
                        kept_count += 1
                except (OSError, FileNotFoundError) as e:
                    logger.debug(AppStrings.LOG_SYS_CLEANUP_SKIP.format(log_file, e))
            
            if deleted_count > 0:
                logger.info(AppStrings.LOG_SYS_LOG_CLEANUP_DONE.format(deleted_count))
        except Exception as e:
            logger.error(AppStrings.LOG_RES_LOG_CLEANUP_FAIL.format(e))
