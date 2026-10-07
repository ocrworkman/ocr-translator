"""识别结果弹窗：原文（可编辑）/ 译文 / 复制 / 朗读，出现在选区附近。"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from core.config import resolve_translate_cfg
from core.ocr import recognize_image
from core.translators import translate_text
from core.utils import pixmap_to_png_bytes, run_in_background


class ResultWindow(QWidget):
    def __init__(self, cfg, tts, auto_translate=True, auto_read="off", near_rect=None,
                 settings_opener=None, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.cfg = cfg
        self.tts = tts
        self._auto_read = auto_read
        self._settings_opener = settings_opener
        self._translate_enabled = (
            auto_translate and str(cfg.get("translate", {}).get("provider", "off")) != "off"
        )
        self._workers = set()
        self._drag_pos = None

        self._build_ui()
        self._position_near(near_rect)
        self.tts.failed.connect(self._show_error)

    # ---------- UI ----------
    def _build_ui(self):
        self.setMinimumSize(400, 300)
        self.resize(560, 500)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 标题栏（可拖动）
        bar = QWidget()
        bar.setObjectName("titleBar")
        bar.setFixedHeight(40)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 0, 8, 0)
        title = QLabel("识图翻译")
        title.setObjectName("winTitle")
        btn_close = QPushButton("×")
        btn_close.setObjectName("closeBtn")
        btn_close.setFixedSize(34, 28)
        btn_close.setToolTip("关闭 (Esc)")
        btn_close.clicked.connect(self.close)
        bl.addWidget(title)
        bl.addStretch(1)
        bl.addWidget(btn_close)
        root.addWidget(bar)

        # 工具栏
        tools = QWidget()
        tl = QHBoxLayout(tools)
        tl.setContentsMargins(10, 8, 10, 4)
        tl.setSpacing(6)

        btn_copy_o = QPushButton("复制原文")
        btn_copy_o.clicked.connect(lambda: self._copy(self.original.toPlainText()))
        btn_read_o = QPushButton("朗读原文")
        btn_read_o.clicked.connect(lambda: self.tts.speak(self.original.toPlainText()))
        btn_stop = QPushButton("停止朗读")
        btn_stop.clicked.connect(self.tts.stop)
        tl.addWidget(btn_copy_o)
        tl.addWidget(btn_read_o)
        tl.addWidget(btn_stop)

        if self._settings_opener is not None:
            btn_set = QPushButton("设置…")
            btn_set.setToolTip("打开设置：快捷键 / 文字识别 / 翻译 / 语音合成")
            btn_set.clicked.connect(self._settings_opener)
            tl.addWidget(btn_set)

        if self._translate_enabled:
            btn_copy_t = QPushButton("复制译文")
            btn_copy_t.clicked.connect(lambda: self._copy(self.translation.toPlainText()))
            btn_read_t = QPushButton("朗读译文")
            btn_read_t.clicked.connect(lambda: self.tts.speak(self.translation.toPlainText()))
            btn_re = QPushButton("重新翻译")
            btn_re.setToolTip("原文可手动修改，修改后点此重新翻译")
            btn_re.clicked.connect(self.translate_now)
            tl.addStretch(1)
            tl.addWidget(btn_copy_t)
            tl.addWidget(btn_read_t)
            tl.addWidget(btn_re)
        else:
            tl.addStretch(1)
        root.addWidget(tools)

        # 正文
        body = QVBoxLayout()
        body.setContentsMargins(10, 4, 10, 10)
        body.setSpacing(6)

        lab_o = QLabel("原文（可编辑，修改后可重新翻译）")
        lab_o.setObjectName("hintLabel")
        self.original = QPlainTextEdit()
        self.original.setPlaceholderText("识别结果将显示在这里…")
        body.addWidget(lab_o)
        body.addWidget(self.original, 3)

        if self._translate_enabled:
            lab_t = QLabel("译文")
            lab_t.setObjectName("hintLabel")
            self.translation = QPlainTextEdit()
            self.translation.setPlaceholderText("翻译结果…")
            body.addWidget(lab_t)
            body.addWidget(self.translation, 3)
        else:
            self.translation = None

        self.status = QLabel("就绪")
        self.status.setObjectName("statusLabel")
        body.addWidget(self.status)
        root.addLayout(body, 1)

    # ---------- 流程 ----------
    def run_ocr(self, pixmap):
        ocr_cfg = self.cfg.get("ocr", {})
        try:
            upscale = float(ocr_cfg.get("upscale", 1.0) or 1.0)
        except (TypeError, ValueError):
            upscale = 1.0
        self.set_status("正在识别文字…")
        run_in_background(
            recognize_image, pixmap_to_png_bytes(pixmap, upscale), ocr_cfg,
            on_done=self._on_ocr_done, on_error=self._on_fail,
            owner=self._workers,
        )

    def _on_ocr_done(self, text):
        text = (str(text or "")).strip()
        self.original.setPlainText(text)
        if not text:
            self.set_status("未识别到文字（可加大识别放大倍数后重试）", error=True)
            return
        if self._translate_enabled:
            self.translate_now()
        elif self._auto_read == "original":
            self.tts.speak(text)
            self.set_status("识别完成，正在朗读…")
        else:
            self.set_status("识别完成")

    def translate_now(self):
        if not self._translate_enabled or self.translation is None:
            return
        text = self.original.toPlainText().strip()
        if not text:
            self.set_status("没有可翻译的文本", error=True)
            return
        self.set_status("正在翻译…")
        run_in_background(
            translate_text, text, resolve_translate_cfg(self.cfg),
            on_done=self._on_translate_done, on_error=self._on_fail,
            owner=self._workers,
        )

    def _on_translate_done(self, text):
        text = (str(text or "")).strip()
        self.translation.setPlainText(text)
        if self._auto_read == "translation" and text:
            self.tts.speak(text)
            self.set_status("翻译完成，正在朗读…")
        else:
            self.set_status("翻译完成")

    # ---------- 辅助 ----------
    def _on_fail(self, msg):
        self.set_status(f"失败：{msg}", error=True)

    def _show_error(self, msg):
        self.set_status(str(msg), error=True)

    def set_status(self, msg, error=False):
        self.status.setText(msg)
        self.status.setStyleSheet("color:#f87171;" if error else "color:#9aa0ae;")

    def _copy(self, text):
        if not (text or "").strip():
            self.set_status("没有可复制的内容", error=True)
            return
        QApplication.clipboard().setText(text)
        self.set_status("已复制到剪贴板")

    def _position_near(self, rect):
        if rect is not None:
            screen = QGuiApplication.screenAt(rect.center()) or QGuiApplication.primaryScreen()
        else:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        width = 560
        if rect is not None and avail.width() > 440:
            width = max(440, min(rect.width(), avail.width() - 20))
        self.resize(width, min(500, max(360, avail.height() - 80)))
        if rect is not None:
            x = rect.left()
            y = rect.bottom() + 12
            if y + self.height() > avail.bottom():
                y = rect.top() - self.height() - 12
            x = max(avail.left(), min(x, avail.right() - self.width()))
            y = max(avail.top(), min(y, avail.bottom() - self.height()))
            self.move(x, y)
        else:
            self.move(avail.center().x() - self.width() // 2,
                      max(avail.top() + 40, avail.center().y() - self.height() // 2))

    # ---------- 事件 ----------
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and event.position().y() <= 40:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
