"""The desktop window's global push-to-talk key, driven by a fake user32: no real hotkey is held."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from laptop_agent import hotkey
from laptop_agent.failures import FAILURES
from laptop_agent.hotkey import MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, WM_HOTKEY, GlobalHotkey


class FakeKernel32:
    def GetCurrentThreadId(self) -> int:
        return 77


class FakeUser32:
    """Delivers the queued messages to GetMessageW, then ends the loop as WM_QUIT would."""

    def __init__(self, messages: list[tuple[int, int]], register_ok: bool = True) -> None:
        self.messages = list(messages)
        self.register_ok = register_ok
        self.registered: list[tuple] = []
        self.unregistered = 0
        self.posted: list[tuple] = []

    def RegisterHotKey(self, hwnd, ident, modifiers, key) -> int:
        self.registered.append((ident, modifiers, key))
        return 1 if self.register_ok else 0

    def UnregisterHotKey(self, hwnd, ident) -> int:
        self.unregistered += 1
        return 1

    def GetMessageW(self, pointer, hwnd, low, high) -> int:
        if not self.messages:
            return 0
        message = pointer._obj
        message.message, message.wParam = self.messages.pop(0)
        return 1

    def PostThreadMessageW(self, thread, message, wparam, lparam) -> int:
        self.posted.append((thread, message))
        return 1


class GlobalHotkeyTests(unittest.TestCase):
    def run_hotkey(self, user32: FakeUser32, on_press) -> GlobalHotkey:
        key = GlobalHotkey(on_press, user32=user32, kernel32=FakeKernel32())
        key._loop()
        return key

    def test_ctrl_alt_j_is_registered_without_repeats_and_released(self) -> None:
        user32 = FakeUser32([])
        key = self.run_hotkey(user32, lambda: None)
        self.assertTrue(key.registered)
        self.assertEqual(user32.registered, [(hotkey._HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, ord("J"))])
        self.assertEqual(user32.unregistered, 1)

    def test_each_press_of_our_hotkey_calls_back_once(self) -> None:
        pressed: list[int] = []
        other = (WM_HOTKEY, hotkey._HOTKEY_ID + 1)
        self.run_hotkey(FakeUser32([(WM_HOTKEY, hotkey._HOTKEY_ID), other, (0x0100, 0), (WM_HOTKEY, hotkey._HOTKEY_ID)]),
                        lambda: pressed.append(1))
        self.assertEqual(pressed, [1, 1])

    def test_a_press_that_raises_does_not_end_the_listener(self) -> None:
        calls: list[int] = []

        def press() -> None:
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("window gone")

        self.run_hotkey(FakeUser32([(WM_HOTKEY, hotkey._HOTKEY_ID)] * 2), press)
        self.assertEqual(len(calls), 2)

    def test_a_key_another_app_holds_is_reported_not_raised(self) -> None:
        key = GlobalHotkey(lambda: None, user32=FakeUser32([], register_ok=False), kernel32=FakeKernel32())
        self.assertFalse(key.start())
        newest = FAILURES.recent(1)[0]
        self.assertEqual((newest["where"], newest["message"]), ("hotkey/register", "Ctrl+Alt+J is taken by another app"))

    def test_stop_ends_the_message_loop(self) -> None:
        user32 = FakeUser32([])
        key = self.run_hotkey(user32, lambda: None)
        key.stop()
        self.assertEqual(user32.posted, [(77, hotkey.WM_QUIT)])

    def test_off_windows_there_is_nothing_to_register(self) -> None:
        with patch.object(hotkey.sys, "platform", "linux"):
            self.assertFalse(GlobalHotkey(lambda: None).start())


class FallbackAppHotkeyTests(unittest.TestCase):
    def test_chromium_fallback_registers_the_global_key_and_signals_the_page(self) -> None:
        import laptop_agent.webui as webui
        import laptop_agent.tools.transcribe as transcribe

        server = Mock(server_address=("127.0.0.1", 4321))
        process = Mock()
        process.poll.return_value = 0
        created: list[object] = []

        class FakeHotkey:
            def __init__(self, on_press) -> None:
                self.on_press = on_press
                self.stopped = False
                created.append(self)

            def start(self) -> bool:
                self.on_press()
                return True

            def stop(self) -> None:
                self.stopped = True

        before = webui._wait_hotkey(None)
        with patch.object(webui, "_refuse_if_running"), patch.object(webui, "_Server", return_value=server), \
             patch.object(webui, "_warmup"), patch.object(webui, "_keep_warm"), \
             patch.object(webui, "_schedule_ticker"), patch.object(transcribe, "warm_stt"), \
             patch.object(webui, "_launch_webview", return_value=False), \
             patch.object(webui, "_launch_app_window", return_value=process) as launch, \
             patch.object(webui, "GlobalHotkey", FakeHotkey), patch.object(webui, "_DESKTOP_MODE", False):
            webui.run_desktop()

        self.assertEqual(len(created), 1)
        self.assertTrue(created[0].stopped)
        self.assertEqual(webui._wait_hotkey(None), before + 1)
        self.assertIn("desktop_hotkey=", launch.call_args.args[0])
        server.shutdown.assert_called_once()


if __name__ == "__main__":
    unittest.main()
