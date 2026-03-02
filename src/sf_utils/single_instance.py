"""
동일 애플리케이션의 중복 실행을 방지하는 모듈입니다.

PySide6의 QLockFile을 사용하여 안정적으로 중복 실행을 감지합니다.
기존 QLocalServer/QLocalSocket 방식에 비해 구현이 단순하며,
프로세스가 비정상 종료된 경우에도 운영체제에 의해 락이 자동으로 해제됩니다.
"""

import os
import sys
import logging

from PySide6.QtCore import QDir, QLockFile
from PySide6.QtWidgets import QMessageBox

from sf_utils.app_strings import AppStrings

logger = logging.getLogger("StringFinder.SingleInstance")

# 락 파일 인스턴스를 전역으로 유지 (GC로 인한 조기 해제 방지)
_lock_file: QLockFile | None = None


def ensure_single_instance() -> None:
    """
    이미 실행 중인 인스턴스가 있으면 경고 메시지를 표시하고 프로세스를 종료합니다.

    QLockFile은 시스템 임시 디렉토리에 .lock 파일을 생성합니다.
    프로세스가 정상/비정상 종료 시 OS가 자동으로 락을 해제하므로
    스테일 락 문제가 발생하지 않습니다.
    """
    global _lock_file

    lock_path = os.path.join(QDir.tempPath(), "stringfinder.lock")
    _lock_file = QLockFile(lock_path)

    # 100ms 동안 잠금 시도
    # (기존 인스턴스가 비정상 종료된 경우 스테일 락을 자동으로 제거하고 재시도)
    if not _lock_file.tryLock(100):
        logger.warning(AppStrings.LOG_SYS_SINGLE_INSTANCE_DETECTED)
        QMessageBox.warning(
            None,
            AppStrings.APP_TITLE,
            AppStrings.MSG_ALREADY_RUNNING,
        )
        sys.exit(0)

    logger.debug(AppStrings.LOG_SYS_SINGLE_INSTANCE_LOCK_SUCCESS.format(lock_path))
