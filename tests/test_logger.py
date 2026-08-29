import logging
from unittest.mock import MagicMock, patch


def test_logger_uses_process_specific_append_file(tmp_path, monkeypatch):
    import sf_utils.logger as logger_module

    test_logger = logging.Logger("StringFinder.test")
    monkeypatch.setattr(logger_module, "_logger_instance", test_logger)
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setattr(logger_module.os, "getpid", lambda: 4242)
    monkeypatch.setattr(logger_module.sys, "stdout", None)

    file_handler = MagicMock()
    with patch("sf_utils.logger.logging.FileHandler", return_value=file_handler) as handler_factory:
        assert logger_module.get_logger() is test_logger

    log_path = handler_factory.call_args.args[0]
    assert log_path.endswith("_4242.log")
    assert handler_factory.call_args.kwargs["mode"] == "a"
