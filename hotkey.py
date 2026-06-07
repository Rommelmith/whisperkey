"""hotkey.py — kernel-level hotkey listener via evdev.

Works on both X11 and Wayland because it reads /dev/input directly.
Requires the user to be in the 'input' group (or root).

Push-to-talk model:
  hold every key in the configured chord → on_press() called
  release the full chord                 → on_release() called
  press Esc while recording → on_cancel() called

Grab mode: if enabled, the hotkey is consumed and not forwarded to the focused app.
           All grabbed devices are ungrabbed on stop() / SIGTERM to avoid a stuck grab.
"""

from __future__ import annotations
import os
import signal
import threading
from typing import Callable

from logger import log


class HotkeyListener:
    def __init__(
        self,
        hotkey_names: str | list[str],
        grab: bool,
        on_press: Callable,
        on_release: Callable,
        on_cancel: Callable,
    ):
        if isinstance(hotkey_names, str):
            hotkey_names = [hotkey_names]
        if not hotkey_names:
            raise ValueError("At least one hotkey is required.")

        self._hotkey_names = tuple(hotkey_names)
        self._grab = grab
        self._on_press = on_press
        self._on_release = on_release
        self._on_cancel = on_cancel

        self._recording = False
        self._armed = True
        self._pressed: set[int] = set()
        self._state_lock = threading.Lock()
        self._devices: list = []
        self._stop_event = threading.Event()

    def start(self) -> None:
        import evdev
        from evdev import ecodes

        hotkey_codes = set()
        for name in self._hotkey_names:
            try:
                hotkey_codes.add(getattr(ecodes, name))
            except AttributeError:
                raise ValueError(
                    f"Unknown evdev key name: {name!r}. "
                    "Check /usr/include/linux/input-event-codes.h for valid names."
                )

        # Find devices that actually have the hotkey we want.
        # EV_KEY alone is too broad — mice, touchpads, power buttons all report it,
        # and grabbing them would freeze the whole input system.
        all_devices = []
        for path in evdev.list_devices():
            try:
                d = evdev.InputDevice(path)
                caps = d.capabilities().get(ecodes.EV_KEY, [])
                # Must have the hotkey AND look like a keyboard (has letter keys),
                # so we don't grab mice that happen to expose a few KEY_* codes.
                if hotkey_codes.issubset(caps) and ecodes.KEY_A in caps:
                    all_devices.append(d)
            except Exception:
                pass

        if not all_devices:
            raise RuntimeError("No keyboard input devices found in /dev/input. "
                               "Are you in the 'input' group?")

        self._devices = all_devices
        label = " + ".join(self._hotkey_names)
        log.info(f"Listening on {len(all_devices)} keyboard device(s) for {label}")

        if self._grab:
            for d in self._devices:
                try:
                    d.grab()
                except Exception as e:
                    log.warning(f"Could not grab {d.path}: {e}")
            # Ungrab on signal so we don't leave the keyboard captured on crash
            signal.signal(signal.SIGTERM, lambda *_: self.stop())
            signal.signal(signal.SIGINT, lambda *_: self.stop())

        for device in self._devices:
            t = threading.Thread(
                target=self._listen,
                args=(device, hotkey_codes),
                daemon=True,
                name=f"evdev-{device.path}",
            )
            t.start()

    def stop(self) -> None:
        self._stop_event.set()
        for d in self._devices:
            try:
                if self._grab:
                    d.ungrab()
                d.close()
            except Exception:
                pass
        self._devices = []

    # ── internals ─────────────────────────────────────────────────────────────

    def _listen(self, device, hotkey_codes: set[int]) -> None:
        from evdev import ecodes
        try:
            for event in device.read_loop():
                if self._stop_event.is_set():
                    break
                if event.type != ecodes.EV_KEY:
                    continue

                callback = None
                with self._state_lock:
                    if event.code in hotkey_codes:
                        if event.value == 1:
                            self._pressed.add(event.code)
                            if self._armed and not self._recording and self._pressed == hotkey_codes:
                                self._recording = True
                                callback = self._on_press
                        elif event.value == 0:
                            self._pressed.discard(event.code)
                            if self._recording and not self._pressed:
                                self._recording = False
                                self._armed = True
                                callback = self._on_release
                            elif not self._pressed:
                                self._armed = True

                    elif event.code == ecodes.KEY_ESC and event.value == 1 and self._recording:
                        self._recording = False
                        self._armed = False
                        callback = self._on_cancel

                if callback:
                    callback()

        except OSError:
            if not self._stop_event.is_set():
                log.warning(f"Keyboard device disconnected: {device.path}; restarting.")
                os._exit(1)
        except Exception as e:
            log.error(f"evdev error on {device.path}: {e}")
