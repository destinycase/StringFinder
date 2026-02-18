from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtCore import QObject, Signal
from sf_utils.logger import logger
from sf_utils.app_strings import AppStrings

from typing import Optional


class SingleInstanceController(QObject):
    """
    애플리케이션의 단일 인스턴스 실행을 보장합니다.
    이미 실행 중인 인스턴스가 있으면 통신을 통해 기존 창을 활성화하도록 요청합니다.
    """

    instance_requested = Signal()

    def __init__(self, key: str):
        super().__init__()
        self.key = key
        self.server: Optional[QLocalServer] = None

    def check_and_start(self) -> bool:
        """
        인스턴스 상태를 확인합니다.
        이미 실행 중이면 True를 반환하고, 아니면 서버를 시작하고 False를 반환합니다.
        """
        # 먼저 기존 서버에 연결 시도
        socket = QLocalSocket()
        socket.connectToServer(self.key)

        if socket.waitForConnected(500):
            # 연결 성공 -> 이미 다른 인스턴스가 실행 중임
            logger.info(AppStrings.LOG_SYS_SINGLE_INSTANCE_DETECTED)
            # 기존 인스턴스에 포커스 요청 메시지 전송
            socket.write(b"focus")
            socket.waitForBytesWritten(500)
            socket.disconnectFromServer()
            return True

        # 연결 실패 -> 이 인스턴스가 첫 번째임. 서버를 열어 대기함.
        # 이전 실행에서 남은 소켓 파일 정리
        QLocalServer.removeServer(self.key)

        self.server = QLocalServer()
        if self.server.listen(self.key):
            self.server.newConnection.connect(self._on_new_connection)
            logger.debug(f"[시스템] 단일 인스턴스 서버 시작: {self.key}")
            return False
        else:
            logger.error(f"[시스템] 단일 인스턴스 서버 시작 실패: {self.server.errorString()}")
            return False

    def _on_new_connection(self):
        """중복 실행 시도자가 보낸 메시지를 처리합니다."""
        if not self.server:
            return

        socket = self.server.nextPendingConnection()
        if socket.waitForReadyRead(500):
            message = bytes(socket.readAll().data()).decode()
            if message == "focus":
                logger.info("[시스템] 중복 실행 요청 감지 - 기존 창 활성화")
                self.instance_requested.emit()

        socket.disconnectFromServer()
        socket.deleteLater()
