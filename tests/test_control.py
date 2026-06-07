import os
import tempfile
import unittest

import control


class ControlRoundTripTests(unittest.TestCase):
    def setUp(self):
        # Point the socket at a private temp dir so we don't clash with a real worker.
        self._tmp = tempfile.mkdtemp()
        self._old_runtime = os.environ.get("XDG_RUNTIME_DIR")
        os.environ["XDG_RUNTIME_DIR"] = self._tmp

    def tearDown(self):
        if self._old_runtime is None:
            os.environ.pop("XDG_RUNTIME_DIR", None)
        else:
            os.environ["XDG_RUNTIME_DIR"] = self._old_runtime

    def test_status_and_mode_round_trip(self):
        calls = {"mode": None}

        server = control.ControlServer({
            "status": lambda *_: {"running": True, "loaded": False, "mode": "balanced"},
            "mode": lambda arg: calls.__setitem__("mode", arg) or {"mode": arg},
        })
        server.start()
        try:
            st = control.send("status")
            self.assertTrue(st["ok"])
            self.assertTrue(st["running"])
            self.assertEqual(st["mode"], "balanced")

            r = control.send("mode low")
            self.assertTrue(r["ok"])
            self.assertEqual(r["mode"], "low")
            self.assertEqual(calls["mode"], "low")
        finally:
            server.stop()

    def test_unknown_command(self):
        server = control.ControlServer({"status": lambda *_: {"running": True}})
        server.start()
        try:
            r = control.send("frobnicate")
            self.assertFalse(r["ok"])
            self.assertIn("unknown command", r["error"])
        finally:
            server.stop()

    def test_send_when_worker_down(self):
        # No server bound at this socket path.
        r = control.send("status", timeout=1.0)
        self.assertFalse(r["ok"])
        self.assertEqual(r["reason"], "worker not running")


if __name__ == "__main__":
    unittest.main()
