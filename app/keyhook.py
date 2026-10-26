"""Which key was pressed, for "react to keystrokes" and the heatmap. Windows only for now.

Only the key and when it was pressed are kept, never the text. Presses made while a password field
has focus are dropped. Holding a key down counts as one press. Modifier shortcuts (Ctrl, Alt, Win)
aren't counted. Nothing is listened to unless a Paint setting needs it.

Ported from Illumunda. Fixes over the original:
  * key repeats (holding a key) counted as presses, so the heatmap saturated on hold;
  * the password check ran on the lighting thread, where a hung app could stall every light for
    up to half a second per keypress. It now runs on its own thread, and the hook only reads a flag.
"""

import ctypes
import sys
import threading
import time
from ctypes import wintypes

import keymap

IS_WINDOWS = sys.platform == "win32"

WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
WM_QUIT = 0x12
SHIFT = (0x10, 0xA0, 0xA1)
CTRL = (0x11, 0xA2, 0xA3)
ALT = (0x12, 0xA4, 0xA5)
WIN = (0x5B, 0x5C)
MODIFIERS = set(SHIFT + CTRL + ALT + WIN)
SHORTCUT_MODIFIERS = set(CTRL + ALT + WIN)
GWL_STYLE, ES_PASSWORD = -16, 0x20

if IS_WINDOWS:
    LRESULT = ctypes.c_ssize_t
    HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
    user32.CallNextHookEx.restype = LRESULT
    user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.c_void_p]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class KeyHook:
    """A Windows low-level keyboard hook on its own thread. `on_key(key id)` is called on that thread
    for each new press (not repeats), unless `blocked()` says the focus is in a password field.
    Windows drops hooks that are slow, so `on_key` must return at once."""

    def __init__(self, on_key, blocked=lambda: False):
        if not IS_WINDOWS:
            raise OSError("Keystroke reactions are Windows-only for now.")
        self.on_key = on_key
        self.blocked = blocked
        self.held = set()     # modifier virtual keys held down
        self.down = set()     # other virtual keys held down, so a repeat isn't a new press
        self._thread = None
        self._thread_id = None
        self._ready = threading.Event()
        self._error = None
        self._proc = HOOKPROC(self._callback)   # kept alive here: ctypes would collect it otherwise

    def start(self):
        self._thread = threading.Thread(target=self._run, name="keyboard-hook", daemon=True)
        self._thread.start()
        self._ready.wait(5)
        if self._error:
            raise OSError(self._error)

    def stop(self):
        if self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(2)

    def _run(self):
        self._thread_id = kernel32.GetCurrentThreadId()
        hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not hook:
            self._error = f"Couldn't install the keyboard hook (error {ctypes.get_last_error()})"
            self._ready.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            pass
        user32.UnhookWindowsHookEx(hook)

    def _callback(self, code, wparam, lparam):
        if code == 0:
            try:
                vk = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents.vkCode
                if wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    self._key_down(vk)
                elif wparam in (WM_KEYUP, WM_SYSKEYUP):
                    (self.held if vk in MODIFIERS else self.down).discard(vk)
            except Exception:  # noqa: BLE001 - an error here must never break the user's typing
                pass
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _key_down(self, vk):
        if vk in MODIFIERS:
            self.held.add(vk)
            return
        if vk in self.down:           # holding a key sends repeats: that's one press
            return
        # forget keys whose key-up we missed (e.g. after Win+L), or they'd never count again
        self.down = {k for k in self.down if user32.GetAsyncKeyState(k) & 0x8000}
        self.down.add(vk)
        if self.held & SHORTCUT_MODIFIERS:
            return
        key = keymap.VK.get(vk)
        if key and not self.blocked():
            self.on_key(key)


class PasswordWatch(threading.Thread):
    """Keeps `focused` up to date: is a password field focused right now? Its own thread, because
    asking an app for its focused control can take half a second when that app hangs."""

    CHECK_EVERY_S = 0.15

    def __init__(self):
        super().__init__(daemon=True, name="password-check")
        self.focused = False
        self.uia_available = False
        self._quit = threading.Event()

    def stop(self):
        self._quit.set()

    def run(self):
        detector = _PasswordDetector()
        self.uia_available = detector.uia is not None
        while not self._quit.is_set():
            self.focused = detector.focused_is_password()
            self._quit.wait(self.CHECK_EVERY_S)


class _PasswordDetector:
    """Uses UI Automation (browsers, modern apps, Windows sign-in) when comtypes is installed, and the
    ES_PASSWORD style of classic edit boxes as a fallback. Create and use on one thread."""

    def __init__(self):
        self.uia = None
        try:
            import comtypes
            import comtypes.client
            try:
                comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
            except OSError:
                pass   # already initialized on this thread in another mode
            comtypes.client.GetModule("UIAutomationCore.dll")
            from comtypes.gen import UIAutomationClient as uia
            self.uia = comtypes.client.CreateObject(uia.CUIAutomation8, interface=uia.IUIAutomation2)
            self.uia.ConnectionTimeout = 500
            self.uia.TransactionTimeout = 500
        except Exception:  # noqa: BLE001 - no comtypes, or no UI Automation: the fallback still works
            self.uia = None

    def focused_is_password(self):
        if self.uia is not None:
            try:
                if self.uia.GetFocusedElement().CurrentIsPassword:
                    return True
            except Exception:  # noqa: BLE001 - COM errors, timeouts, elements going away
                pass
        return _classic_password_focused()


class _GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD), ("hwndActive", wintypes.HWND),
                ("hwndFocus", wintypes.HWND), ("hwndCapture", wintypes.HWND), ("hwndMenuOwner", wintypes.HWND),
                ("hwndMoveSize", wintypes.HWND), ("hwndCaret", wintypes.HWND), ("rcCaret", wintypes.RECT)]


def _classic_password_focused():
    if not IS_WINDOWS:
        return False
    info = _GUITHREADINFO(cbSize=ctypes.sizeof(_GUITHREADINFO))
    if not (user32.GetGUIThreadInfo(0, ctypes.byref(info)) and info.hwndFocus):
        return False
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(info.hwndFocus, cls, 64)
    if "edit" not in cls.value.lower():     # the style bit means something else on other controls
        return False
    return bool(user32.GetWindowLongW(info.hwndFocus, GWL_STYLE) & ES_PASSWORD)


class KeyMonitor:
    """Starts and stops the key hook and the password check together, feeding `memory`."""

    def __init__(self, memory):
        self.memory = memory
        self.hook = None
        self.watch = None
        self.error = ""

    @property
    def running(self):
        return self.hook is not None

    def start(self):
        if self.running:
            return
        self.error = ""
        watch = PasswordWatch()
        try:
            # KeyHook refuses to exist off Windows, so nothing else starts in that case
            hook = KeyHook(lambda key: self.memory.press(key, time.monotonic()), blocked=lambda: watch.focused)
            watch.start()
            hook.start()
        except OSError as e:
            watch.stop()
            self.error = str(e)
            raise
        self.watch, self.hook = watch, hook

    def stop(self):
        if self.hook is not None:
            self.hook.stop()
        if self.watch is not None:
            self.watch.stop()
        self.hook = self.watch = None
