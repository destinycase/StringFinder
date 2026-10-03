"""First-paint notification without delaying the main-window paint event."""

from PySide6.QtCore import QEvent, QObject, QTimer


class FirstPaintObserver(QObject):
    def __init__(self, window, on_ready):
        super().__init__(window)
        self._seen = False
        self._on_ready = on_ready
        window.installEventFilter(self)

    def eventFilter(self, watched, event):
        if not self._seen and event.type() == QEvent.Type.Paint:
            self._seen = True
            # The paint event completes before this next event-loop callback.
            QTimer.singleShot(0, self._on_ready)
        return False
