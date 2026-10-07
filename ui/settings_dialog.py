"""设置窗口：快捷键 / 文字识别 / 翻译 / 语音合成。"""
import copy

from PySide6.QtCore import QBuffer, QIODevice, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QKeySequence, QPainter
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QSpinBox,
    QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
)

from core.config import (
    DEFAULT_OCR_URL, DEFAULT_TARGET_LANG, DEFAULT_TRANSLATE_URL, OCR_URL_PRESETS,
    PROVIDER_LABELS, ZNDJS_SOURCE_CHOICES, lang_to_code,
    resolve_ocr_cfg, resolve_translate_cfg, save_config,
)
from core.ocr import recognize_image
from core.translators import translate_text
from core.tts import EDGE_VOICES, TTSService, list_sapi_voices
from core.utils import run_in_background

# 翻译服务商 → 设置页里的子页面索引（zndjs / custom / google / off）
_KEY_TO_TR_PAGE = {"zndjs": 0, "custom": 1, "openai": 1, "google": 2, "off": 3}



class HotkeyEdit(QLineEdit):
    """点击获得焦点后，直接按下组合键完成录制（焦点式，不依赖 grabKeyboard）。"""

    changed = Signal(str)

    _PURE_MODIFIERS = (
        Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift,
        Qt.Key.Key_Meta, Qt.Key.Key_AltGr,
    )

    def __init__(self, combo="", parent=None):
        super().__init__(parent)
        self._combo = ""
        self._recording = False
        self.setReadOnly(True)   # 只作录制入口，不允许手动输入
        self.setPlaceholderText("点击此处，然后按下快捷键")
        self.setMinimumWidth(200)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.set_value(combo)

    def set_value(self, combo):
        self._combo = str(combo or "").strip()
        if not self._recording:
            self.setText(self._combo)
        self.changed.emit(self._combo)

    def value(self):
        return self._combo

    def _current_mods(self, event):
        mods = []
        m = event.modifiers()
        if m & Qt.KeyboardModifier.ControlModifier:
            mods.append("ctrl")
        if m & Qt.KeyboardModifier.AltModifier:
            mods.append("alt")
        if m & Qt.KeyboardModifier.ShiftModifier:
            mods.append("shift")
        if m & Qt.KeyboardModifier.MetaModifier:
            mods.append("win")
        return mods

    def _enter_recording(self):
        if not self._recording:
            self._recording = True
            self.setText("请按下组合键（Esc 取消 / 退格清除）")

    def mousePressEvent(self, event):
        # 打包版里 read-only 输入框偶发点击不触发 focusIn，这里主动确保进入录制态
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        self._enter_recording()
        super().mousePressEvent(event)

    def focusInEvent(self, event):
        self._enter_recording()
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        if self._recording:
            self._recording = False
            self.setText(self._combo)
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        if not self._recording:
            super().keyPressEvent(event)
            return
        key = event.key()

        # 只按了修饰键：实时预览，等待主键
        if key in self._PURE_MODIFIERS:
            mods = self._current_mods(event)
            if mods:
                self.setText("+".join(mods) + " + …")
            event.accept()
            return

        if key == Qt.Key.Key_Escape:
            self.setText(self._combo)
            event.accept()
            return

        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            self._combo = ""
            self.setText("")
            self.changed.emit("")
            event.accept()
            return

        mods = self._current_mods(event)

        # 无修饰键的普通键：让 Tab/回车继续走默认焦点切换，其余给出行内提示
        if not mods and key in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab,
                                Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.ignore()
            return

        name = QKeySequence(key).toString().lower()
        if not name:
            event.ignore()
            return
        if not mods and not (Qt.Key.Key_F1 <= key <= Qt.Key.Key_F12):
            self.setText(f"无效（{name}）：需要 Ctrl/Alt/Shift/Win 修饰键")
            event.accept()
            return

        self._combo = "+".join(mods + [name])
        self.setText(self._combo)
        self.changed.emit(self._combo)
        event.accept()


class SettingsDialog(QDialog):
    saved = Signal()

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置 - 识图翻译")
        self.resize(620, 600)
        self.cfg = cfg
        self._tts_service = TTSService(cfg, parent=self)
        self._tts_workers = set()
        self._test_worker = None

        tabs = QTabWidget()
        tabs.addTab(self._build_hotkey_tab(), "快捷键")
        tabs.addTab(self._build_ocr_tab(), "文字识别")
        tabs.addTab(self._build_translate_tab(), "翻译")
        tabs.addTab(self._build_tts_tab(), "语音合成")
        tabs.addTab(self._build_appearance_tab(), "外观")

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.button(QDialogButtonBox.StandardButton.Ok).setText("保存")
        btns.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        btns.accepted.connect(self._save)
        btns.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.addWidget(tabs)
        lay.addWidget(btns)

    # ---------- 快捷键 ----------
    def _build_hotkey_tab(self):
        page = QWidget()
        f = QFormLayout(page)
        f.setContentsMargins(14, 18, 14, 14)
        f.setSpacing(12)

        hk_cfg = self.cfg.get("hotkeys", {})
        self.hk_ocr = HotkeyEdit(hk_cfg.get("capture_ocr", ""))
        self.hk_tts = HotkeyEdit(hk_cfg.get("capture_tts", ""))
        f.addRow("截图识别：", self._hotkey_row(self.hk_ocr))
        f.addRow("截图识别并朗读：", self._hotkey_row(self.hk_tts))

        hint = QLabel("点击输入框获得焦点后，直接按下想要的组合键（如 Ctrl+Alt+O）。"
                      "必须包含至少一个修饰键（Ctrl/Alt/Shift/Win）；按退格键清除；Esc 取消。\n"
                      "「截图识别」识别后是否自动翻译，取决于主界面「识别后自动翻译」的勾选"
                      "（默认关闭，不消耗翻译额度）；「截图识别并朗读」只朗读原文、不翻译。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        f.addRow("", hint)
        return page

    def _hotkey_row(self, edit: HotkeyEdit):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        h.addWidget(edit, 1)
        btn_clear = QPushButton("清除")
        btn_clear.setFixedWidth(56)
        btn_clear.clicked.connect(lambda: edit.set_value(""))
        h.addWidget(btn_clear)
        return row

    # ---------- OCR ----------
    def _build_ocr_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(14, 18, 14, 14)
        v.setSpacing(10)
        cfg = self.cfg.get("ocr", {})

        f = QFormLayout()
        f.setSpacing(12)

        # 服务器地址（可自定义）
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.ed_ocr_url = QLineEdit(str(cfg.get("url", "")))
        self.ed_ocr_url.setPlaceholderText(DEFAULT_OCR_URL)
        self.btn_ocr_preset = QPushButton("恢复默认")
        self.btn_ocr_preset.setFixedWidth(84)
        self.btn_ocr_preset.setToolTip(f"恢复为自建服务器 {DEFAULT_OCR_URL}")
        self.btn_ocr_preset.clicked.connect(
            lambda: self.ed_ocr_url.setText(DEFAULT_OCR_URL)
        )
        h.addWidget(self.ed_ocr_url, 1)
        h.addWidget(self.btn_ocr_preset)
        f.addRow("服务器地址：", row)

        self.cb_ocr_preset = QComboBox()
        self.cb_ocr_preset.addItem("（从下拉选择一个常用地址）", "")
        for label, url in OCR_URL_PRESETS:
            self.cb_ocr_preset.addItem(label, url)
        self.cb_ocr_preset.currentIndexChanged.connect(self._on_ocr_preset_picked)
        f.addRow("常用地址：", self.cb_ocr_preset)

        self.ed_token = QLineEdit(str(cfg.get("token", "")))
        self.ed_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_token.setPlaceholderText("识别与翻译共用的 Key")
        f.addRow("Key：", self.ed_token)

        self.sp_method = QSpinBox()
        self.sp_method.setRange(1, 3)
        self.sp_method.setToolTip("识别方式：1 / 2 / 3，按服务端说明选择，默认 2")
        try:
            self.sp_method.setValue(int(cfg.get("method", 2)))
        except (TypeError, ValueError):
            self.sp_method.setValue(2)
        f.addRow("识别方式 method：", self.sp_method)

        self.sp_timeout = QSpinBox()
        self.sp_timeout.setRange(5, 300)
        self.sp_timeout.setSuffix(" 秒")
        try:
            self.sp_timeout.setValue(int(cfg.get("timeout", 30)))
        except (TypeError, ValueError):
            self.sp_timeout.setValue(30)
        f.addRow("超时时间：", self.sp_timeout)

        self.sp_upscale = QDoubleSpinBox()
        self.sp_upscale.setRange(1.0, 4.0)
        self.sp_upscale.setSingleStep(0.5)
        self.sp_upscale.setSuffix(" ×")
        try:
            self.sp_upscale.setValue(float(cfg.get("upscale", 1.0) or 1.0))
        except (TypeError, ValueError):
            self.sp_upscale.setValue(1.0)
        f.addRow("识别前放大倍数：", self.sp_upscale)

        self.sp_pdf_dpi = QSpinBox()
        self.sp_pdf_dpi.setRange(72, 400)
        self.sp_pdf_dpi.setSingleStep(16)
        self.sp_pdf_dpi.setSuffix(" dpi")
        self.sp_pdf_dpi.setToolTip("导入 PDF 时的渲染清晰度：越大越清晰，识别越准但越慢")
        try:
            self.sp_pdf_dpi.setValue(int(cfg.get("pdf_dpi", 200)))
        except (TypeError, ValueError):
            self.sp_pdf_dpi.setValue(200)
        f.addRow("PDF 渲染清晰度：", self.sp_pdf_dpi)
        v.addLayout(f)

        self.btn_test_ocr = QPushButton("测试识别（用上面的服务器和 Key 识别一张测试图）")
        self.btn_test_ocr.clicked.connect(self._test_ocr)
        v.addWidget(self.btn_test_ocr)

        self.lbl_ocr_msg = QLabel("")
        self.lbl_ocr_msg.setObjectName("hintLabel")
        self.lbl_ocr_msg.setWordWrap(True)
        v.addWidget(self.lbl_ocr_msg)

        hint = QLabel(
            f"默认使用自建服务器 {DEFAULT_OCR_URL}（9999 端口的 HTTP 版本是同一套服务，"
            "下拉里可直接切换）。改成其它地址即可使用任意兼容接口："
            "请求体为 {\"token\", \"method\", \"image(base64)\"}，返回文本或坐标列表均可自动解析。"
            "Key 与翻译共用一个 Token。"
        )
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        v.addWidget(hint)
        v.addStretch(1)
        return page

    def _on_ocr_preset_picked(self, _index):
        url = self.cb_ocr_preset.currentData()
        if url:
            self.ed_ocr_url.setText(str(url))
        self.cb_ocr_preset.setCurrentIndex(0)

    def _test_ocr(self):
        tmp = copy.deepcopy(self.cfg)
        self._apply_to(tmp)
        ocr_cfg = resolve_ocr_cfg(tmp)
        self.lbl_ocr_msg.setText("正在测试识别…（首次可能要几秒）")
        run_in_background(
            recognize_image, self._make_test_png(), ocr_cfg,
            on_done=self._on_test_ocr_done,
            on_error=lambda m: self.lbl_ocr_msg.setText(f"识别失败：{m}"),
            owner=self._tts_workers,
        )

    def _on_test_ocr_done(self, text):
        got = str(text or "").strip()
        if got:
            self.lbl_ocr_msg.setText(f"识别成功：{got[:120]}")
        else:
            self.lbl_ocr_msg.setText(
                "接口通了，但没识别出文字。常见原因：Key 额度用尽、图片太小，"
                "或本机字体渲染异常（测试图变成方块）。换一张真实截图试试。"
            )

    @staticmethod
    def _make_test_png() -> bytes:
        """生成一张带中英文的测试图，用于验证 OCR 服务器地址 / Key 是否可用。"""
        img = QImage(640, 190, QImage.Format.Format_RGB32)
        img.fill(QColor("white"))
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        font = QFont()
        font.setFamilies([
            "Microsoft YaHei", "Microsoft YaHei UI", "SimHei", "SimSun",
            "Noto Sans CJK SC", "Arial",
        ])
        font.setPixelSize(34)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor("black"))
        painter.drawText(28, 70, "测试识别 TEST 2468")
        font.setPixelSize(28)
        painter.setFont(font)
        painter.drawText(28, 140, "第二行 sample line two")
        painter.end()
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        data = bytes(buf.data())
        buf.close()
        return data

    # ---------- 翻译 ----------
    def _build_translate_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(14, 18, 14, 14)
        v.setSpacing(10)
        cfg = self.cfg.get("translate", {})

        f = QFormLayout()
        f.setSpacing(10)

        self.lbl_tr_lang = QLabel("")
        self.lbl_tr_lang.setObjectName("fieldLabel")
        self.lbl_tr_lang.setToolTip("目标语言在主界面「译文」上方的「译成」下拉里选择")
        f.addRow("当前目标语言：", self.lbl_tr_lang)

        self.cb_provider = QComboBox()
        for key in ("zndjs", "custom", "google", "off"):
            self.cb_provider.addItem(PROVIDER_LABELS[key], key)
        cur = str(cfg.get("provider", "zndjs")).lower()
        if cur == "openai":          # 兼容老配置：openai 即"自定义服务器"
            cur = "custom"
        idx = self.cb_provider.findData(cur)
        self.cb_provider.setCurrentIndex(max(0, idx))
        f.addRow("翻译服务：", self.cb_provider)
        v.addLayout(f)

        lang_hint = QLabel(
            "目标语言请在主界面「译文」上方的「译成」下拉里改（默认英文），改完立刻保存。"
        )
        lang_hint.setObjectName("hintLabel")
        lang_hint.setWordWrap(True)
        v.addWidget(lang_hint)

        self.stack_tr = QStackedWidget()
        self.stack_tr.addWidget(self._build_zndjs_page(cfg))
        self.stack_tr.addWidget(self._build_openai_page(cfg))
        self.stack_tr.addWidget(self._build_google_page(cfg))
        off_page = QWidget()
        off_lay = QVBoxLayout(off_page)
        off_lab = QLabel("关闭后只做文字识别，不翻译。")
        off_lab.setObjectName("hintLabel")
        off_lay.addWidget(off_lab)
        off_lay.addStretch(1)
        self.stack_tr.addWidget(off_page)
        self.stack_tr.setCurrentIndex(
            _KEY_TO_TR_PAGE.get(self.cb_provider.currentData(), 0)
        )
        self.cb_provider.currentIndexChanged.connect(self._on_provider_changed)
        v.addWidget(self.stack_tr)
        self._refresh_lang_hint()

        btn_test = QPushButton("测试翻译（翻译一句英文）")
        btn_test.clicked.connect(self._test_translate)
        v.addWidget(btn_test)

        self.lbl_tr_msg = QLabel("")
        self.lbl_tr_msg.setObjectName("hintLabel")
        self.lbl_tr_msg.setWordWrap(True)
        v.addWidget(self.lbl_tr_msg)
        v.addStretch(1)
        return page

    def _on_provider_changed(self, _index):
        key = self.cb_provider.currentData()
        self.stack_tr.setCurrentIndex(_KEY_TO_TR_PAGE.get(key, 0))
        self._refresh_lang_hint()

    def _refresh_lang_hint(self):
        """把「目标语言在主界面选」这件事实时显示出来。"""
        name = (str(self.cfg.get("translate", {}).get("target_lang", "") or "").strip()
                or DEFAULT_TARGET_LANG)
        code = lang_to_code(name) or "?"
        if hasattr(self, "lbl_tr_lang"):
            self.lbl_tr_lang.setText(f"{name}（{code}）")
        if hasattr(self, "lbl_zndjs_target"):
            self.lbl_zndjs_target.setText(f"{code}　（跟随主界面语言 {name}）")

    def _build_zndjs_page(self, cfg):
        page = QWidget()
        f = QFormLayout(page)
        f.setContentsMargins(0, 4, 0, 0)
        f.setSpacing(10)

        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.ed_zndjs_url = QLineEdit(str(cfg.get("zndjs_url", "")))
        self.ed_zndjs_url.setPlaceholderText(DEFAULT_TRANSLATE_URL)
        self.btn_tr_preset = QPushButton("恢复默认")
        self.btn_tr_preset.setFixedWidth(84)
        self.btn_tr_preset.setToolTip(f"恢复为自建服务器 {DEFAULT_TRANSLATE_URL}")
        self.btn_tr_preset.clicked.connect(
            lambda: self.ed_zndjs_url.setText(DEFAULT_TRANSLATE_URL)
        )
        h.addWidget(self.ed_zndjs_url, 1)
        h.addWidget(self.btn_tr_preset)
        f.addRow("服务器地址：", row)

        self.cb_zndjs_source = QComboBox()
        for label, code in ZNDJS_SOURCE_CHOICES:
            self.cb_zndjs_source.addItem(label, code)
        cur_src = str(cfg.get("zndjs_source", "auto") or "auto").lower()
        idx = self.cb_zndjs_source.findData(cur_src)
        self.cb_zndjs_source.setCurrentIndex(max(0, idx))
        self.cb_zndjs_source.setToolTip(
            "服务端自动检测语种对短句容易误判（比如把 Hello 当成意大利语），"
            "此时把源语言固定成实际语种即可"
        )
        f.addRow("源语言：", self.cb_zndjs_source)

        self.lbl_zndjs_target = QLabel("")
        self.lbl_zndjs_target.setObjectName("fieldLabel")
        f.addRow("目标语种代码：", self.lbl_zndjs_target)

        hint = QLabel(
            f"默认调用自建服务器 {DEFAULT_TRANSLATE_URL}（POST "
            "{\"token\", \"text\", \"source\", \"target\"} → {\"result\": 译文}），"
            "Key 复用「文字识别」里的 Token。"
            "目标语种代码按主界面「译文」上方选的语言自动换算："
            "日语 ja、韩语 ko（kor 不支持）、繁体 zh-TW。"
        )
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        f.addRow("", hint)
        return page

    def _build_openai_page(self, cfg):
        page = QWidget()
        f = QFormLayout(page)
        f.setContentsMargins(0, 4, 0, 0)
        f.setSpacing(10)

        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.ed_baseurl = QLineEdit(str(cfg.get("openai_base_url", "")))
        self.ed_baseurl.setPlaceholderText("https://api.deepseek.com/v1 或 https://api.openai.com/v1")
        self.btn_tr_preset = QPushButton("示例")
        self.btn_tr_preset.setFixedWidth(84)
        self.btn_tr_preset.setToolTip("填入一个示例地址")
        self.btn_tr_preset.clicked.connect(
            lambda: self.ed_baseurl.setText("https://api.deepseek.com/v1")
        )
        h.addWidget(self.ed_baseurl, 1)
        h.addWidget(self.btn_tr_preset)
        f.addRow("服务器地址：", row)

        self.ed_key = QLineEdit(str(cfg.get("openai_api_key", "")))
        self.ed_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ed_key.setPlaceholderText("留空则自动复用『文字识别』里的 Token")
        f.addRow("API Key：", self.ed_key)

        self.ed_model = QLineEdit(str(cfg.get("openai_model", "")))
        self.ed_model.setPlaceholderText("gpt-4o-mini")
        f.addRow("模型：", self.ed_model)

        self.ed_prompt = QPlainTextEdit()
        self.ed_prompt.setPlainText(str(cfg.get("system_prompt", "")))
        self.ed_prompt.setMinimumHeight(110)
        self.ed_prompt.setTabChangesFocus(True)
        f.addRow("系统提示词：", self.ed_prompt)

        hint = QLabel(
            "用于任意 OpenAI 兼容服务：填到 /v1 为止的 Base URL（程序会自动补 /chat/completions），"
            "例如 https://api.deepseek.com/v1、https://api.openai.com/v1 或本地 Ollama。"
            "API Key 留空时会自动复用『文字识别』里填写的 Token。"
        )
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        f.addRow("", hint)
        return page


    def _build_google_page(self, cfg):
        page = QWidget()
        f = QFormLayout(page)
        f.setContentsMargins(0, 4, 0, 0)
        f.setSpacing(10)
        self.ed_gcode = QLineEdit(str(cfg.get("google_target_code", "zh-CN")))
        f.addRow("目标语言代码：", self.ed_gcode)
        hint = QLabel("免费接口，无需 Key。语言代码如 zh-CN / en / ja；"
                      "若上面的目标语言能识别则自动使用。需要能访问 Google。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        f.addRow("", hint)
        return page

    # ---------- 语音合成 ----------
    def _build_tts_tab(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(14, 18, 14, 14)
        v.setSpacing(10)
        cfg = self.cfg.get("tts", {})

        f = QFormLayout()
        f.setSpacing(10)

        self.cb_engine = QComboBox()
        self.cb_engine.addItem("Edge 在线语音（微软，音质好）", "edge")
        self.cb_engine.addItem("Windows 本地语音（离线 SAPI）", "sapi")
        self.cb_engine.addItem("关闭语音合成", "off")
        idx = self.cb_engine.findData(str(cfg.get("engine", "edge")))
        self.cb_engine.setCurrentIndex(max(0, idx))
        f.addRow("语音引擎：", self.cb_engine)

        self.cb_autoread = QComboBox()
        self.cb_autoread.addItem("关闭", "off")
        self.cb_autoread.addItem("自动朗读原文", "original")
        self.cb_autoread.addItem("自动朗读译文", "translation")
        idx = self.cb_autoread.findData(str(cfg.get("auto_read", "off")))
        self.cb_autoread.setCurrentIndex(max(0, idx))
        f.addRow("识别完成后：", self.cb_autoread)
        v.addLayout(f)

        self.stack_tts = QStackedWidget()
        self.stack_tts.addWidget(self._build_edge_page(cfg))
        self.stack_tts.addWidget(self._build_sapi_page(cfg))
        off_page = QWidget()
        off_lay = QVBoxLayout(off_page)
        off_lab = QLabel("语音合成已关闭。")
        off_lab.setObjectName("hintLabel")
        off_lay.addWidget(off_lab)
        off_lay.addStretch(1)
        self.stack_tts.addWidget(off_page)
        self.stack_tts.setCurrentIndex(max(0, self.cb_engine.currentIndex()))
        self.cb_engine.currentIndexChanged.connect(self.stack_tts.setCurrentIndex)
        v.addWidget(self.stack_tts)

        btn_test = QPushButton("试听语音")
        btn_test.clicked.connect(self._test_tts)
        v.addWidget(btn_test)

        self.lbl_tts_msg = QLabel("")
        self.lbl_tts_msg.setObjectName("hintLabel")
        self.lbl_tts_msg.setWordWrap(True)
        self._tts_service.failed.connect(lambda m: self.lbl_tts_msg.setText(str(m)))
        v.addWidget(self.lbl_tts_msg)
        v.addStretch(1)
        return page

    def _build_edge_page(self, cfg):
        page = QWidget()
        f = QFormLayout(page)
        f.setContentsMargins(0, 4, 0, 0)
        f.setSpacing(10)

        self.cb_voice = QComboBox()
        for value, label in EDGE_VOICES:
            self.cb_voice.addItem(label, value)
        idx = self.cb_voice.findData(str(cfg.get("voice", "auto")))
        self.cb_voice.setCurrentIndex(max(0, idx))
        f.addRow("音色：", self.cb_voice)

        self.cb_rate = QComboBox()
        self.cb_rate.setEditable(True)
        for r in ("+0%", "+10%", "-10%", "+20%", "-20%", "+50%", "-50%"):
            self.cb_rate.addItem(r)
        self.cb_rate.setCurrentText(str(cfg.get("rate", "+0%")))
        f.addRow("语速：", self.cb_rate)

        self.cb_volume = QComboBox()
        self.cb_volume.setEditable(True)
        for vol in ("+0%", "+10%", "-10%", "-20%", "-30%"):
            self.cb_volume.addItem(vol)
        self.cb_volume.setCurrentText(str(cfg.get("volume", "+0%")))
        f.addRow("音量：", self.cb_volume)

        hint = QLabel("“自动”会根据文本语言选择合适音色（中文→晓晓，英文→Aria 等）。"
                      "更多音色见 edge-tts 官方文档。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        f.addRow("", hint)
        return page

    def _build_sapi_page(self, cfg):
        page = QWidget()
        f = QFormLayout(page)
        f.setContentsMargins(0, 4, 0, 0)
        f.setSpacing(10)

        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        self.cb_sapi_voice = QComboBox()
        self.cb_sapi_voice.addItem("系统默认", "")
        h.addWidget(self.cb_sapi_voice, 1)
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self._refresh_sapi_voices)
        h.addWidget(btn_refresh)
        f.addRow("本地音色：", row)

        hint = QLabel("离线使用 Windows 自带语音（SAPI），无需联网。点击“刷新”枚举系统已安装的音色。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        f.addRow("", hint)
        return page

    def _refresh_sapi_voices(self):
        self.lbl_tts_msg.setText("正在枚举本地音色…")
        run_in_background(
            list_sapi_voices,
            on_done=self._on_sapi_voices,
            on_error=lambda m: self.lbl_tts_msg.setText(f"枚举失败: {m}"),
            owner=self._tts_workers,
        )

    def _on_sapi_voices(self, voices):
        current = self.cb_sapi_voice.currentData() or ""
        self.cb_sapi_voice.clear()
        self.cb_sapi_voice.addItem("系统默认", "")
        for vid, name in voices or []:
            self.cb_sapi_voice.addItem(name, vid)
        idx = self.cb_sapi_voice.findData(current)
        if idx >= 0:
            self.cb_sapi_voice.setCurrentIndex(idx)
        self.lbl_tts_msg.setText(f"已加载 {len(voices or [])} 个本地音色。")

    # ---------- 外观 ----------
    def _build_appearance_tab(self):
        page = QWidget()
        f = QFormLayout(page)
        f.setContentsMargins(14, 18, 14, 14)
        f.setSpacing(12)
        cfg = self.cfg.get("ui", {})

        self.cb_theme = QComboBox()
        self.cb_theme.addItem("深色", "dark")
        self.cb_theme.addItem("浅色", "light")
        idx = self.cb_theme.findData(str(cfg.get("theme", "dark")))
        self.cb_theme.setCurrentIndex(max(0, idx))
        f.addRow("界面主题：", self.cb_theme)

        hint = QLabel("选择深色或浅色界面，保存后立即生效，并记忆到配置文件。")
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        f.addRow("", hint)
        return page

    # ---------- 保存 / 测试 ----------
    def _apply_to(self, target: dict):
        hk = target.setdefault("hotkeys", {})
        hk["capture_ocr"] = self.hk_ocr.value().strip()
        hk["capture_tts"] = self.hk_tts.value().strip()

        ocr = target.setdefault("ocr", {})
        ocr["url"] = self.ed_ocr_url.text().strip() or DEFAULT_OCR_URL
        ocr["token"] = self.ed_token.text().strip()
        ocr["method"] = self.sp_method.value()
        ocr["timeout"] = self.sp_timeout.value()
        ocr["upscale"] = self.sp_upscale.value()
        ocr["pdf_dpi"] = self.sp_pdf_dpi.value()

        tr = target.setdefault("translate", {})
        tr["provider"] = self.cb_provider.currentData()
        # 注意：target_lang / zndjs_target 由主界面「译成」下拉维护，这里不覆盖
        tr["zndjs_url"] = self.ed_zndjs_url.text().strip() or DEFAULT_TRANSLATE_URL
        tr["zndjs_source"] = self.cb_zndjs_source.currentData() or "auto"
        tr["openai_base_url"] = self.ed_baseurl.text().strip()
        tr["openai_api_key"] = self.ed_key.text().strip()
        tr["openai_model"] = self.ed_model.text().strip()
        tr["system_prompt"] = self.ed_prompt.toPlainText()
        tr["google_target_code"] = self.ed_gcode.text().strip() or "zh-CN"

        tts = target.setdefault("tts", {})
        tts["engine"] = self.cb_engine.currentData()
        tts["voice"] = self.cb_voice.currentData() or "auto"
        tts["rate"] = self.cb_rate.currentText().strip() or "+0%"
        tts["volume"] = self.cb_volume.currentText().strip() or "+0%"
        tts["auto_read"] = self.cb_autoread.currentData()
        tts["sapi_voice"] = self.cb_sapi_voice.currentData() or ""

        ui = target.setdefault("ui", {})
        ui["theme"] = self.cb_theme.currentData() or "dark"

    def _save(self):
        self._apply_to(self.cfg)
        save_config(self.cfg)
        self.saved.emit()
        self.accept()

    def _test_translate(self):
        provider = self.cb_provider.currentData()
        if provider == "off":
            QMessageBox.information(self, "识图翻译", "当前翻译服务为关闭状态。")
            return
        tmp = copy.deepcopy(self.cfg)
        self._apply_to(tmp)
        self.lbl_tr_msg.setText("正在翻译，请稍候…")
        sample = "Hello! This is a translation test. The weather is nice today."
        self._test_worker = run_in_background(
            translate_text, sample, resolve_translate_cfg(tmp),
            on_done=self._on_test_translate_done,
            on_error=self._on_test_translate_failed,
        )

    def _on_test_translate_done(self, text):
        got = str(text or "").strip()
        self.lbl_tr_msg.setText(f"翻译成功：{got[:160]}")
        QMessageBox.information(self, "识图翻译", f"翻译结果：\n\n{got}")

    def _on_test_translate_failed(self, msg):
        self.lbl_tr_msg.setText(f"翻译失败：{msg}")
        QMessageBox.warning(self, "识图翻译", f"翻译失败：\n{msg}")

    def _test_tts(self):
        tmp = copy.deepcopy(self.cfg)
        self._apply_to(tmp)
        self._tts_service.set_config(tmp)
        self.lbl_tts_msg.setText("正在合成，请聆听…")
        self._tts_service.speak("你好，这是语音合成测试。Hello, this is a TTS test.")

    def done(self, result):
        self._tts_service.stop()
        super().done(result)
