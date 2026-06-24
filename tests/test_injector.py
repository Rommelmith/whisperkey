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

    @patch("injector._ydotool_named_keys", return_value=True)
    @patch("injector._is_wayland", return_value=True)
    @patch("injector.subprocess.run")
    def test_wayland_paste_uses_named_keys_on_ydotool_0x(self, run, _is_wayland, _named):
        self.assertTrue(injector._send_paste())

        run.assert_called_once()
        cmd = run.call_args.args[0]
        self.assertEqual(cmd, ["ydotool", "key", "--delay", "50", "ctrl+v"])

    @patch("injector._ydotool_named_keys", return_value=False)
    @patch("injector._is_wayland", return_value=True)
    @patch("injector.subprocess.run")
    def test_wayland_paste_uses_numeric_keys_on_ydotool_1x(self, run, _is_wayland, _named):
        self.assertTrue(injector._send_paste())

        run.assert_called_once()
        cmd = run.call_args.args[0]
        self.assertEqual(cmd, ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"])


class YdotoolDetectionTests(unittest.TestCase):
    def setUp(self):
        injector._ydotool_named_keys.cache_clear()

    def tearDown(self):
        injector._ydotool_named_keys.cache_clear()

    def _help(self, text):
        return type("R", (), {"stdout": text, "stderr": ""})()

    @patch("injector.subprocess.run")
    def test_detects_named_family_from_0x_help(self, run):
        run.return_value = self._help(
            "Each key sequence can be any number of modifiers and keys, "
            "separated by plus (+)\nFor example: alt+r Alt+F4 ctrl+v")
        self.assertTrue(injector._ydotool_named_keys())

    @patch("injector.subprocess.run")
    def test_detects_numeric_family_from_1x_help(self, run):
        run.return_value = self._help(
            "Usage: key [--key-delay <ms>] <keycode:state> ...\n"
            "Where state is 1 for pressed and 0 for released.")
        self.assertFalse(injector._ydotool_named_keys())

    @patch("injector.subprocess.run", side_effect=FileNotFoundError)
    def test_defaults_to_named_when_ydotool_missing(self, run):
        self.assertTrue(injector._ydotool_named_keys())


if __name__ == "__main__":
    unittest.main()
