import unittest

from hotkey import HotkeyListener


class HotkeyConfigTests(unittest.TestCase):
    def test_accepts_single_key_for_old_configs(self):
        listener = HotkeyListener("KEY_RIGHTCTRL", False, lambda: None, lambda: None, lambda: None)
        self.assertEqual(listener._hotkey_names, ("KEY_RIGHTCTRL",))

    def test_accepts_key_chord(self):
        listener = HotkeyListener(
            ["KEY_LEFTCTRL", "KEY_LEFTMETA"],
            False,
            lambda: None,
            lambda: None,
            lambda: None,
        )
        self.assertEqual(listener._hotkey_names, ("KEY_LEFTCTRL", "KEY_LEFTMETA"))


if __name__ == "__main__":
    unittest.main()
