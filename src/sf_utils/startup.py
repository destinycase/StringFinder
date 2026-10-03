"""Non-UI startup log maintenance."""

import threading


class StartupLogCleanup:
    """Run once after first paint; finish before shutdown closes log handlers."""

    def __init__(self, cleanup, on_error):
        self.cleanup = cleanup
        self.on_error = on_error
        self._thread = None
        self._closed = False
        self._lock = threading.Lock()

    def start(self):
        error = None
        with self._lock:
            if self._closed or self._thread is not None:
                return
            thread = threading.Thread(target=self._run, name="StartupLogCleanup")
            try:
                thread.start()
            except Exception as failure:
                self._closed = True
                error = failure
            else:
                self._thread = thread
        if error is not None:
            self.on_error(error)

    def _run(self):
        try:
            self.cleanup()
        except Exception as error:
            self.on_error(error)

    def stop(self):
        with self._lock:
            self._closed = True
            thread = self._thread
        if thread is not None:
            thread.join()
