import logging
import os
import tempfile
import unittest
from pathlib import Path

from core.logging_config import runtime_log_handlers, should_write_runtime_logs


class LoggingConfigTests(unittest.TestCase):
    def test_should_write_runtime_logs_when_not_in_tests(self):
        self.assertTrue(should_write_runtime_logs(modules={}, environ={}))

    def test_should_not_write_runtime_logs_under_unittest(self):
        self.assertFalse(should_write_runtime_logs(modules={"unittest": object()}, environ={}))

    def test_env_var_can_disable_runtime_file_logs(self):
        self.assertFalse(
            should_write_runtime_logs(
                modules={},
                environ={"HAWKSTRADE_DISABLE_FILE_LOGS": "true"},
            )
        )

    def test_runtime_log_handlers_skip_file_handler_under_unittest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            handlers = runtime_log_handlers(Path(os.path.abspath(tmpdir)), "test.log")
            self.addCleanup(lambda: [handler.close() for handler in handlers])

        self.assertFalse(any(isinstance(handler, logging.FileHandler) for handler in handlers))


if __name__ == "__main__":
    unittest.main()
