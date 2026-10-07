"""全局快捷键管理。

改用 Windows 原生 RegisterHotKey + WM_HOTKEY（经 Qt 原生事件过滤器接收），
比第三方 keyboard 库更可靠：能正确处理 Alt 组合键，也避免 keyboard 库在打包 /
前台窗口提权时收不到按键的问题。回调直接在 GUI 线程触发，无需跨线程投递。
"""
import ctypes
import logging
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal

user32 = ctypes.windll.user32
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_uint, ctypes.c_uint]
user32.RegisterHotKey.restype = ctypes.c_bool
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = ctypes.c_bool
user32.VkKeyScanW.argtypes = [ctypes.c_wchar]
user32.VkKeyScanW.restype = ctypes.c_short

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_MOD_MAP = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN, "meta": MOD_WIN, "super": MOD_WIN, "cmd": MOD_WIN,
}

_SPECIAL_VK = {
    "space": 0x20, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "esc": 0x1B, "escape": 0x1B, "del": 0x2E, "delete": 0x2E,
    "ins": 0x2D, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pgup": 0x21, "prior": 0x21,
    "pagedown": 0x22, "pgdn": 0x22, "next": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "prt": 0x2C, "printscreen": 0x2C, "snapshot": 0x2C,
    "pause": 0x13, "capslock": 0x14,
    "backspace": 0x08,
}


def parse_combo(combo: str):
    """把 'ctrl+alt+o' / 'alt+g' / 'f8' 解析成 (modifiers, vk)。失败返回 None。"""
    parts = [p.strip().lower() for p in str(combo or "").split("+") if p.strip()]
    if not parts:
        return None
    mods = 0
    key = parts[-1]
    for m in parts[:-1]:
        if m not in _MOD_MAP:
            return None
        mods |= _MOD_MAP[m]
    is_function = key.startswith("f") and key[1:].isdigit()
    if mods == 0 and not is_function:
        # 至少要有一个修饰键（纯功能键除外），避免劫持普通输入
        return None

    if is_function:
        n = int(key[1:])
        if 1 <= n <= 24:
            return mods | MOD_NOREPEAT, 0x70 + (n - 1)
        return None
    if key in _SPECIAL_VK:
        return mods | MOD_NOREPEAT, _SPECIAL_VK[key]
    if len(key) == 1:
        vk = user32.VkKeyScanW(ctypes.c_wchar(key.upper()))
        if vk < 0:
            vk = ord(key.upper())
        return mods | MOD_NOREPEAT, vk & 0xFF
    return None


class _HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, manager):
        super().__init__()
        self._mgr = manager

    def nativeEventFilter(self, eventType, message):
        try:
            if bytes(eventType) != b"windows_generic_MSG":
                return False, 0
            msg = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
            if msg.message == WM_HOTKEY:
                self._mgr._on_wm_hotkey(int(msg.wParam) & 0xFFFF)
                return True, 0
        except Exception:  # noqa: BLE001
            logging.exception("处理 WM_HOTKEY 失败")
        return False, 0


class HotkeyManager(QObject):
    triggered = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._by_id = {}          # hotkey_id -> name
        self._next_id = 1
        self._hwnd = 0
        self._window = None
        self._filter = _HotkeyFilter(self)
        self._installed = False

    def bind_window(self, widget):
        """绑定一个 Qt 顶层窗口，用它的 HWND 接收 WM_HOTKEY。"""
        self._window = widget
        try:
            self._hwnd = int(widget.winId())
        except Exception:  # noqa: BLE001
            self._hwnd = 0

    def _ensure_filter(self):
        if self._installed:
            return
        app = QCoreApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._filter)
            self._installed = True

    def apply(self, hotkey_map: dict) -> list:
        """注册 {名称: 组合键}，返回 [(组合键, 错误信息), ...]。"""
        self.clear()
        self._ensure_filter()
        errors = []
        for name, combo in (hotkey_map or {}).items():
            combo = str(combo or "").strip().lower()
            if not combo:
                continue
            parsed = parse_combo(combo)
            if not parsed:
                errors.append((combo, "格式无法识别，需含 Ctrl/Alt/Shift/Win 修饰键（或纯功能键 F1-F24）"))
                continue
            mods, vk = parsed
            if not self._hwnd:
                errors.append((combo, "主界面尚未创建，无法注册全局快捷键"))
                continue
            hid = self._next_id
            self._next_id += 1
            if user32.RegisterHotKey(wintypes.HWND(self._hwnd), hid, mods, vk):
                self._by_id[hid] = name
            else:
                try:
                    err = ctypes.WinError().strerror
                except Exception:  # noqa: BLE001
                    err = "RegisterHotKey 失败"
                errors.append((combo, f"注册失败（可能被其他程序占用）：{err}"))
        return errors

    def clear(self):
        if self._hwnd:
            for hid in list(self._by_id.keys()):
                try:
                    user32.UnregisterHotKey(wintypes.HWND(self._hwnd), hid)
                except Exception:  # noqa: BLE001
                    pass
        self._by_id.clear()

    def _on_wm_hotkey(self, hid):
        name = self._by_id.get(hid)
        if name:
            self.triggered.emit(name)
