import subprocess
import unittest
from unittest.mock import patch

import injector


class ClipboardTests(unittest.TestCase):
    @patch("injector.subprocess.Popen")
    def test_clipboard_process_does_not_capture_daemon_output(self, popen):
        process = popen.return_value
        process.returncode = 0

        self.assertTrue(injector._to_clipboard("hello"))

        _, kwargs = popen.call_args
        self.assertIs(kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(kwargs["stderr"], subprocess.DEVNULL)
        process.communicate.assert_called_once_with(b"hello", timeout=2)


if __name__ == "__main__":
    unittest.main()
