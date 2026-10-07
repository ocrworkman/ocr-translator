"""核心调度：全局快捷键 / 托盘 -> 截图 -> OCR -> 主窗口（图 + 原文 + 译文）。"""
import logging

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from core.config import save_config
from core.hotkeys import HotkeyManager
from core.selector import RegionSelector
from core.tts import TTSService
from ui.main_window import MainWindow
from ui.settings_dialog import SettingsDialog
from ui.theme import get_qss, make_app_icon


class Controller(QObject):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.tts = TTSService(cfg, parent=self)
        self.selector = RegionSelector(self)
        self.selector.captured.connect(self._on_captured)
        self.selector.failed.connect(self._toast)
        self.selector.cancelled.connect(self._restore_window)
        self.hotkeys = HotkeyManager(self)
        self.hotkeys.triggered.connect(self._on_hotkey)
        self._tray = None
        self._need_restore = False

        self.window = MainWindow(
            cfg, self.tts,
            settings_opener=self.open_settings,
            capture_opener=self._capture_from_button,
            theme_toggler=self.toggle_theme,
        )
        # 全局快捷键需要一个 Qt 顶层窗口的 HWND 来接收 WM_HOTKEY
        self.hotkeys.bind_window(self.window)
        self.apply_hotkeys()

    # ---------- 主题 ----------
    def apply_theme(self):
        theme = str((self.cfg.get("ui", {}) or {}).get("theme", "dark")).lower()
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(get_qss(theme))
        self.window.refresh_theme_button()

    def toggle_theme(self):
        ui = self.cfg.setdefault("ui", {})
        ui["theme"] = "light" if str(ui.get("theme", "dark")).lower() == "dark" else "dark"
        save_config(self.cfg)
        self.apply_theme()

    # ---------- 主窗口 ----------
    def show_main(self):
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _restore_window(self):
        if self._need_restore:
            self._need_restore = False
            self.show_main()

    # ---------- 快捷键 ----------
    def apply_hotkeys(self):
        errors = self.hotkeys.apply(self.cfg.get("hotkeys", {}))
        for combo, err in errors:
            logging.warning("快捷键 %s 注册失败: %s", combo, err)
            self._toast(f"快捷键 {combo} 注册失败：{err}")

    def _on_hotkey(self, name):
        if name == "capture_tts":
            # 「截图识别并朗读」明确只要原文，不翻译
            self.start_capture(auto_translate=False, auto_read="original")
        else:
            # 「截图识别」是否翻译，按主界面「识别后自动翻译」的勾选（默认关闭）
            auto_read = str(self.cfg.get("tts", {}).get("auto_read", "off"))
            self.start_capture(auto_translate=None, auto_read=auto_read)

    # ---------- 截图流程 ----------
    def _capture_from_button(self):
        auto_read = str(self.cfg.get("tts", {}).get("auto_read", "off"))
        self.start_capture(auto_translate=None, auto_read=auto_read)

    def start_capture(self, auto_translate=None, auto_read="off"):
        """auto_translate=None 表示按主界面「识别后自动翻译」的勾选（默认关闭）。"""
        self._pending_translate = auto_translate
        self._pending_read = auto_read
        # 先隐藏主窗口，避免把主窗口截进图里；稍等一帧让桌面刷新后再抓屏
        if self.window.isVisible():
            self._need_restore = True
            self.window.hide()
        QTimer.singleShot(160, self._do_start)

    def _do_start(self):
        if not self.selector.start():
            self._toast("截图启动失败：未检测到可用屏幕")
            self._restore_window()

    def _on_captured(self, rect, pixmap):
        self._need_restore = False
        self.show_main()
        self.window.present_capture(
            pixmap,
            auto_translate=self._pending_translate,
            auto_read=self._pending_read,
        )

    # ---------- 导入图片 / PDF ----------
    def _import_from_tray(self):
        """从托盘触发导入：先唤回主界面，再弹出文件选择框。"""
        self._need_restore = False
        self.show_main()
        QTimer.singleShot(0, self.window.open_import)

    # ---------- 设置 ----------
    def open_settings(self):
        # 打开设置期间暂停全局快捷键，避免录制新组合键时触发截图
        self.hotkeys.clear()
        dlg = SettingsDialog(self.cfg)
        dlg.saved.connect(self._on_settings_saved)
        dlg.exec()
        self.apply_hotkeys()

    def _on_settings_saved(self):
        self.tts.set_config(self.cfg)
        self.apply_theme()
        # 翻译服务/地址可能变了，刷新主界面的「译成」下拉状态
        refresh = getattr(self.window, "refresh_translate_controls", None)
        if callable(refresh):
            refresh()

    # ---------- 托盘 ----------
    def create_tray(self, app) -> QSystemTrayIcon:
        menu = QMenu()
        act_show = menu.addAction("显示主界面")
        act_show.triggered.connect(self.show_main)
        menu.addSeparator()
        act_ocr = menu.addAction("截图识别（是否翻译按界面勾选）")
        act_ocr.triggered.connect(self._capture_from_button)
        act_tts = menu.addAction("截图识别并朗读")
        act_tts.triggered.connect(lambda: self.start_capture(False, "original"))
        act_import = menu.addAction("导入图片 / PDF 识别…")
        act_import.triggered.connect(self._import_from_tray)
        menu.addSeparator()
        act_settings = menu.addAction("设置…")
        act_settings.triggered.connect(self.open_settings)
        menu.addSeparator()
        act_quit = menu.addAction("退出")
        act_quit.triggered.connect(app.quit)

        try:
            tray = QSystemTrayIcon(make_app_icon(), self)
            tray.setContextMenu(menu)
            tray.setToolTip("识图翻译 · 单击显示主界面")
            tray.activated.connect(self._on_tray_activated)
            tray.show()
            self._tray = tray
            return tray
        except Exception:  # noqa: BLE001
            logging.exception("创建系统托盘失败")
            return None

    def _on_tray_activated(self, reason):
        # 单击 / 双击托盘图标 → 显示主界面
        Trigger = QSystemTrayIcon.ActivationReason
        if reason in (Trigger.Trigger, Trigger.DoubleClick):
            self.show_main()

    # ---------- 其他 ----------
    def shutdown(self):
        self.tts.stop()
        self.hotkeys.clear()

    def _toast(self, msg):
        if self._tray is not None:
            self._tray.showMessage("识图翻译", str(msg))
        else:
            logging.warning("%s", msg)
