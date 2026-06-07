"""control.py — tiny Unix-socket control channel between the tray and the worker.

The worker (main.py) runs a ControlServer; the tray (tray.py) and the `--ctl` CLI
use send() to talk to it. Protocol is deliberately trivial: the client writes one
line (a command, optionally with one argument), the server replies with one line
of JSON and closes.

Commands:
  status                         -> full status dict
  load / unload                  -> force model load / unload
  pause / resume                 -> arm/disarm the hotkey (model state untouched)
  mode <performance|balanced|low>-> switch idle mode live
  last                           -> {"text": "<last transcription>"}

The socket lives at $XDG_RUNTIME_DIR/whisperkey.sock (falls back to /tmp). Because
it sits in the per-user runtime dir (0700) it's already private to this user.
"""

from __future__ import annotations
import json
import os
import socket
import threading
from pathlib import Path
from typing import Callable

from logger import log


def socket_path() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
    return str(Path(base) / "whisperkey.sock")


# ── server (runs inside the worker) ──────────────────────────────────────────────

class ControlServer:
    """Background AF_UNIX server. `handlers` maps command name -> callable.

    A handler takes an optional string argument and returns a JSON-serialisable
    object. Exceptions are caught and returned as {"ok": False, "error": ...}.
    """

    def __init__(self, handlers: dict[str, Callable[..., object]]):
        self._handlers = handlers
        self._path = socket_path()
        self._sock: socket.socket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        # Remove a stale socket from a previous run.
        try:
            os.unlink(self._path)
        except FileNotFoundError:
            pass

        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(self._path)
        os.chmod(self._path, 0o600)
        self._sock.listen(8)
        self._sock.settimeout(1.0)

        self._thread = threading.Thread(target=self._serve, daemon=True, name="control-server")
        self._thread.start()
        log.info(f"Control socket listening at {self._path}")

    def stop(self) -> None:
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
        try:
            os.unlink(self._path)
        except FileNotFoundError:
            pass

    # ── internals ─────────────────────────────────────────────────────────────

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with conn:
                try:
                    conn.settimeout(2.0)
                    raw = conn.recv(4096).decode("utf-8", "replace").strip()
                    resp = self._dispatch(raw)
                except Exception as e:  # never let one client kill the server
                    resp = {"ok": False, "error": str(e)}
                try:
                    conn.sendall((json.dumps(resp) + "\n").encode())
                except Exception:
                    pass

    def _dispatch(self, raw: str) -> dict:
        if not raw:
            return {"ok": False, "error": "empty command"}
        parts = raw.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else None

        handler = self._handlers.get(cmd)
        if handler is None:
            return {"ok": False, "error": f"unknown command {cmd!r}"}
        try:
            result = handler(arg) if arg is not None else handler()
        except TypeError:
            # handler that doesn't accept an argument was given one (or vice versa)
            result = handler() if arg is None else handler(arg)
        except Exception as e:
            return {"ok": False, "error": str(e)}

        if isinstance(result, dict):
            result.setdefault("ok", True)
            return result
        return {"ok": True, "result": result}


# ── client (used by tray.py and the --ctl CLI) ──────────────────────────────────

def send(command: str, timeout: float = 5.0) -> dict:
    """Send a one-line command to the worker; return its JSON reply as a dict.

    If the worker isn't running, returns {"ok": False, "reason": "worker not running"}.
    """
    path = socket_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect(path)
            s.sendall((command.strip() + "\n").encode())
            chunks = []
            while True:
                data = s.recv(4096)
                if not data:
                    break
                chunks.append(data)
                if b"\n" in data:
                    break
        raw = b"".join(chunks).decode("utf-8", "replace").strip()
        if not raw:
            return {"ok": False, "reason": "no response"}
        return json.loads(raw)
    except (FileNotFoundError, ConnectionRefusedError):
        return {"ok": False, "reason": "worker not running"}
    except socket.timeout:
        return {"ok": False, "reason": "timeout"}
    except Exception as e:
        return {"ok": False, "reason": str(e)}
