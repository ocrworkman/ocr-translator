# -*- coding: utf-8 -*-
"""综合验证：配置接线 / 快捷键录制 / 结果窗口设置入口。"""
import functools
import sys

print = functools.partial(print, flush=True)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

app = QApplication(sys.argv)

# ---------- 1. 配置保存/加载 回环 ----------
from core.config import load_config, save_config

cfg = load_config()
cfg["ocr"]["token"] = "TEST_TOKEN_123"
cfg["translate"]["openai_api_key"] = "sk-test-key"
save_config(cfg)
cfg2 = load_config()
assert cfg2["ocr"]["token"] == "TEST_TOKEN_123", "token 未持久化"
assert cfg2["translate"]["openai_api_key"] == "sk-test-key", "api key 未持久化"
print("[1] config roundtrip OK (token/key 都能持久化)")

# ---------- 2. 设置对话框字段接线 ----------
from ui.settings_dialog import SettingsDialog

dlg = SettingsDialog(cfg)
dlg.ed_token.setText("NEW_TOKEN_456")
dlg.ed_key.setText("sk-new-key")
dlg.hk_ocr.set_value("ctrl+alt+k")
tmp = {}
dlg._apply_to({"_": 0}) if False else None
# _apply_to 需要 target dict
target = {"hotkeys": {}, "ocr": {}, "translate": {}, "tts": {}}
dlg._apply_to(target)
assert target["ocr"]["token"] == "NEW_TOKEN_456", f"token 接线失败: {target['ocr']['token']}"
assert target["translate"]["openai_api_key"] == "sk-new-key", "key 接线失败"
assert target["hotkeys"]["capture_ocr"] == "ctrl+alt+k", "hotkey 接线失败"
print("[2] settings dialog wiring OK (token -> cfg['ocr']['token'], key -> cfg['translate']['openai_api_key'])")

# ---------- 3. 快捷键录制（焦点式 QLineEdit，需可见才有 focusIn） ----------
dlg.show()
edit = dlg.hk_tts
edit.setFocus()
app.processEvents()
assert edit._recording, "focus 后应进入录制状态"
QTest.keyClick(edit, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)
assert edit.value() == "ctrl+alt+k", f"录制失败: {edit.value()!r}"
print("[3a] hotkey record OK: ctrl+alt+K ->", edit.value())

# 只按修饰键 -> 预览，不结束录制
QTest.keyClick(edit, Qt.Key.Key_Control)
assert edit.value() == "ctrl+alt+k", "修饰键不应改变录制结果"
print("[3b] modifier-only preview OK")

# 无修饰键普通键 -> 行内提示，不产生组合
QTest.keyClick(edit, Qt.Key.Key_A)
assert edit.value() == "ctrl+alt+k", "无修饰键不应产生组合"
print("[3c] bare key rejected OK")

# F 键无修饰也可以
QTest.keyClick(edit, Qt.Key.Key_F8)
assert edit.value() == "f8", f"F 键录制失败: {edit.value()!r}"
print("[3d] F-key without modifier OK ->", edit.value())

# 退格清除
QTest.keyClick(edit, Qt.Key.Key_Backspace)
assert edit.value() == "", "退格清除失败"
print("[3e] backspace clear OK")

# ---------- 4. 错误信息 ----------
from core.translators import translate_text, TranslationError

try:
    translate_text("hello", {"provider": "openai", "openai_base_url": "https://x/v1", "openai_api_key": ""})
    raise AssertionError("应当抛错")
except TranslationError as e:
    assert "设置" in str(e) and "Token" in str(e), f"提示不够明确: {e}"
    print("[4] error message OK:", str(e)[:60], "...")

# ---------- 5. 结果窗口带设置入口 ----------
from core.tts import TTSService
from ui.result_window import ResultWindow
from PySide6.QtWidgets import QPushButton

calls = []
service = TTSService(cfg)
win = ResultWindow(cfg, service, auto_translate=True, auto_read="off",
                   near_rect=None, settings_opener=lambda: calls.append(1))
buttons = [b.text() for b in win.findChildren(QPushButton)]
assert any("设置" in t for t in buttons), f"设置按钮缺失: {buttons}"
print("[5] result window settings button OK:", buttons)

print("ALL_TESTS_PASSED")
