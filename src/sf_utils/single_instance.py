from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from sf_utils.app_strings import AppStrings
from sf_utils.logger import logger


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
        """check_and_start 함수."""
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
            logger.debug(AppStrings.LOG_SYS_SINGLE_INSTANCE_SERVER_START.format(self.key))
            return False
        # 남아있는 소켓 흔적 가능성을 고려해 1회 재시도
        QLocalServer.removeServer(self.key)
        if self.server.listen(self.key):
            self.server.newConnection.connect(self._on_new_connection)
            logger.debug(AppStrings.LOG_SYS_SINGLE_INSTANCE_SERVER_START.format(self.key))
            return False
        logger.error(AppStrings.LOG_SYS_SINGLE_INSTANCE_SERVER_START_FAIL.format(self.server.errorString()))
        # 단일 인스턴스 보장을 확보하지 못하면 안전하게 추가 실행을 차단
        return True

    def _on_new_connection(self):
        """중복 실행 시도자가 보낸 메시지를 처리합니다."""
        if not self.server:
            return
        socket = self.server.nextPendingConnection()
        if socket.waitForReadyRead(500):
            message = bytes(socket.readAll().data()).decode()
            if message == "focus":
                logger.info(AppStrings.LOG_SYS_SINGLE_INSTANCE_REQUEST)
                self.instance_requested.emit()
        socket.disconnectFromServer()
        socket.deleteLater()
