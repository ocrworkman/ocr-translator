"""主窗口：任务列表 + 截图/导入识别 + 原文/译文 + 逐条朗读。

支持三种素材来源，统一进入左侧「任务列表」后批量处理：
    1. 屏幕框选截图（全局快捷键 / 右上角按钮）
    2. 导入图片（可多选、可拖拽入窗口）
    3. 导入 PDF（逐页渲染成图片后识别）

每条任务都能单独查看预览、朗读原文、朗读译文、重新翻译；也可以整批翻译、导出全部结果。
"""
import logging
import time

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
    QLabel, QListWidget, QListWidgetItem, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QSizePolicy, QSplitter, QVBoxLayout, QWidget,
)

from core.batch import BatchRunner
from core.config import (
    DEFAULT_TARGET_LANG, TARGET_LANG_CHOICES, lang_to_code,
    resolve_translate_cfg, save_config,
)
from core.ingest import FILE_FILTER, Source, list_sources
from core.translators import translate_text
from core.utils import pixmap_to_png_bytes, run_in_background

_MIN_COL_W = 240
_QUEUE_W = 300

# 列表状态文案与标记
_STATUS_PENDING = "待识别"
_STATUS_OCR = "识别中…"
_STATUS_TR = "翻译中…"
_STATUS_DONE = "完成"
_MARK = {"idle": "·", "busy": "…", "ok": "✔", "fail": "⚠"}


class MainWindow(QWidget):
    def __init__(self, cfg, tts, settings_opener=None, capture_opener=None,
                 theme_toggler=None, parent=None):
        super().__init__(parent)
        self.setObjectName("MainWindow")
        self.setWindowTitle("识图翻译")
        self.resize(1120, 720)
        self.setMinimumSize(900, 560)
        self.setAcceptDrops(True)

        self.cfg = cfg
        self.tts = tts
        self._settings_opener = settings_opener
        self._capture_opener = capture_opener
        self._theme_toggler = theme_toggler

        self._workers = set()
        self._runner = None            # 正在跑的 BatchRunner
        self._current = -1             # 当前选中行
        self._auto_read = "off"        # 本次截图的自动朗读方式
        self._auto_read_index = -1
        self._thumb_pix = None         # 当前条目的预览图

        self.items = []                # [{"name","source","thumb","text","translation","status","kind","error"}]

        self._build_ui()
        self.tts.failed.connect(lambda m: self.set_status(str(m), error=True))
        self.tts.stateChanged.connect(self._on_tts_state)
        self._update_buttons()

    # ---------------- UI ----------------

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 14, 18, 12)
        root.setSpacing(12)

        root.addLayout(self._build_header())

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(False)
        body.setHandleWidth(14)
        body.addWidget(self._build_queue_panel())
        body.addWidget(self._build_work_panel())
        body.setStretchFactor(0, 0)
        body.setStretchFactor(1, 1)
        body.setSizes([_QUEUE_W, 780])
        root.addWidget(body, 1)

        self.status = QLabel("就绪：可框选截图，或导入图片 / PDF（可多选、可拖拽）")
        self.status.setObjectName("statusLabel")
        root.addWidget(self.status)
        self.refresh_translate_controls()

    def _build_header(self):
        header = QHBoxLayout()
        header.setSpacing(8)
        title = QLabel("识图翻译")
        title.setObjectName("winTitle")
        header.addWidget(title)
        header.addStretch(1)

        self.btn_theme = QPushButton("浅色" if self._theme_name() == "dark" else "深色")
        self.btn_theme.setToolTip("切换深色 / 浅色界面")
        self.btn_theme.clicked.connect(self._on_toggle_theme)
        header.addWidget(self.btn_theme)

        self.btn_settings = QPushButton("设置")
        self.btn_settings.setToolTip("打开设置：快捷键 / 文字识别 / 翻译 / 语音合成 / 外观")
        self.btn_settings.clicked.connect(
            lambda: self._settings_opener and self._settings_opener()
        )
        header.addWidget(self.btn_settings)

        self.btn_import = QPushButton("导入图片 / PDF")
        self.btn_import.setToolTip(
            "批量导入图片或 PDF 进行识别（可多选；也可直接把文件拖进窗口）"
        )
        self.btn_import.clicked.connect(self.open_import)
        header.addWidget(self.btn_import)

        self.btn_capture = QPushButton("截图识别")
        self.btn_capture.setProperty("accent", True)
        self.btn_capture.setToolTip("框选屏幕区域进行识别（也可用全局快捷键）")
        self.btn_capture.clicked.connect(
            lambda: self._capture_opener and self._capture_opener()
        )
        header.addWidget(self.btn_capture)
        return header

    def _build_queue_panel(self):
        panel = QWidget()
        panel.setMinimumWidth(248)
        panel.setMaximumWidth(420)
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)

        lab = QLabel("任务列表")
        lab.setObjectName("fieldLabel")
        v.addWidget(lab)

        self.queue = QListWidget()
        self.queue.setObjectName("taskQueue")
        self.queue.setWordWrap(True)
        self.queue.setSpacing(2)
        self.queue.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.queue.setToolTip("单击某条可查看预览与原文 / 译文；支持把图片、PDF 直接拖进窗口")
        self.queue.currentRowChanged.connect(self._on_row_changed)
        v.addWidget(self.queue, 1)

        self.chk_translate = QCheckBox("识别后自动翻译")
        self.chk_translate.setChecked(bool(self.cfg.get("translate", {}).get("auto_translate", False)))
        self.chk_translate.setToolTip(
            "默认关闭，避免白耗翻译额度。\n"
            "勾选状态会记进配置文件，下次启动保持；\n"
            "不勾选时仍可对单条「重新翻译」或「整批翻译」。"
        )
        self.chk_translate.toggled.connect(self._on_auto_translate_toggled)
        v.addWidget(self.chk_translate)

        self.btn_start = QPushButton("开始识别")
        self.btn_start.setProperty("accent", True)
        self.btn_start.clicked.connect(self.start)
        v.addWidget(self.btn_start)

        row1 = QHBoxLayout()
        row1.setSpacing(8)
        self.btn_stop = QPushButton("停止")
        self.btn_stop.clicked.connect(self.stop)
        row1.addWidget(self.btn_stop)
        self.btn_translate_all = QPushButton("整批翻译")
        self.btn_translate_all.setToolTip("把列表中所有已识别但未翻译的原文批量翻译")
        self.btn_translate_all.clicked.connect(self.translate_all)
        row1.addWidget(self.btn_translate_all)
        v.addLayout(row1)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.btn_export = QPushButton("导出结果")
        self.btn_export.clicked.connect(self.export_results)
        row2.addWidget(self.btn_export)
        self.btn_clear = QPushButton("清空列表")
        self.btn_clear.clicked.connect(self.clear_items)
        row2.addWidget(self.btn_clear)
        v.addLayout(row2)

        self.progress = QProgressBar()
        self.progress.setObjectName("taskProgress")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("%v / %m")
        v.addWidget(self.progress)
        return panel

    def _build_work_panel(self):
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        self.preview = QLabel(
            "尚未选择任务\n\n点击左上「截图识别」，或「导入图片 / PDF」批量识别"
        )
        self.preview.setObjectName("previewBox")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(150)
        self.preview.setMaximumHeight(280)
        self.preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        v.addWidget(self.preview)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(16)
        splitter.addWidget(self._build_text_column(
            "原文（可编辑，修改后可重新翻译）", "original"
        ))
        splitter.addWidget(self._build_text_column(
            "译文", "translation", readonly=True, header_extra=self._build_lang_selector()
        ))
        splitter.setSizes([500, 500])
        v.addWidget(splitter, 1)

        v.addLayout(self._build_action_row())
        return panel

    def _build_lang_selector(self):
        """「译成」语言下拉：放在译文标题旁边，改了立刻记进配置。"""
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        lab = QLabel("译成")
        lab.setObjectName("fieldLabel")
        h.addWidget(lab)

        self.cb_lang = QComboBox()
        self.cb_lang.setMinimumWidth(120)
        for name in TARGET_LANG_CHOICES:
            self.cb_lang.addItem(name, name)
        current = str(self.cfg.get("translate", {}).get("target_lang", "") or "").strip()
        if not current:
            current = DEFAULT_TARGET_LANG
        idx = self.cb_lang.findData(current)
        if idx < 0:
            self.cb_lang.addItem(current, current)
            idx = self.cb_lang.count() - 1
        self.cb_lang.setCurrentIndex(idx)
        self.cb_lang.setToolTip(
            "翻译成哪种语言。改完立刻保存；已有原文可点「整批翻译」或单条「重新翻译」生效。"
        )
        self.cb_lang.currentIndexChanged.connect(self._on_target_lang_changed)
        h.addWidget(self.cb_lang)
        return box

    def _on_target_lang_changed(self, _index):
        name = str(self.cb_lang.currentData() or "").strip()
        if not name:
            return
        tr = self.cfg.setdefault("translate", {})
        tr["target_lang"] = name
        tr["zndjs_target"] = ""          # 清掉旧的语种代码覆盖，改由 target_lang 自动映射
        save_config(self.cfg)
        code = lang_to_code(name) or "?"
        self.set_status(
            f"目标语言已设为 {name}（{code}）；点「整批翻译」可重新翻译已有原文"
        )

    def refresh_translate_controls(self):
        """设置保存后刷新：语言下拉的可用状态 / 当前值。"""
        if not hasattr(self, "cb_lang"):
            return
        name = str(self.cfg.get("translate", {}).get("target_lang", "") or "").strip()
        if name:
            idx = self.cb_lang.findData(name)
            if idx >= 0 and idx != self.cb_lang.currentIndex():
                self.cb_lang.blockSignals(True)
                self.cb_lang.setCurrentIndex(idx)
                self.cb_lang.blockSignals(False)
        enabled = self._provider() != "off"
        self.cb_lang.setEnabled(enabled)
        self.cb_lang.setToolTip(
            "翻译成哪种语言。改完立刻保存；已有原文可点「整批翻译」或单条「重新翻译」生效。"
            if enabled else "当前翻译服务为「关闭」，可在 设置 → 翻译 开启后再选语言。"
        )

    def _build_text_column(self, label_text, attr, readonly=False, header_extra=None):
        panel = QWidget()
        v = QVBoxLayout(panel)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(6)
        lab = QLabel(label_text)
        lab.setObjectName("fieldLabel")
        head.addWidget(lab)
        head.addStretch(1)
        if header_extra is not None:
            head.addWidget(header_extra)
        v.addLayout(head)

        edit = QPlainTextEdit()
        edit.setPlaceholderText("…")
        edit.setMinimumWidth(_MIN_COL_W)
        edit.setReadOnly(readonly)
        v.addWidget(edit, 1)
        setattr(self, attr, edit)
        return panel

    def _build_action_row(self):
        row = QHBoxLayout()
        row.setSpacing(8)

        self.btn_read_original = QPushButton("朗读原文")
        self.btn_read_original.setToolTip("朗读当前条目的原文")
        self.btn_read_original.clicked.connect(
            lambda: self._speak(self.original.toPlainText())
        )
        row.addWidget(self.btn_read_original)

        self.btn_read_translation = QPushButton("朗读译文")
        self.btn_read_translation.setToolTip("朗读当前条目的译文")
        self.btn_read_translation.clicked.connect(
            lambda: self._speak(self.translation.toPlainText())
        )
        row.addWidget(self.btn_read_translation)

        self.btn_stop_speak = QPushButton("停止朗读")
        self.btn_stop_speak.clicked.connect(self.tts.stop)
        row.addWidget(self.btn_stop_speak)

        row.addSpacing(12)

        self.btn_retranslate = QPushButton("重新翻译")
        self.btn_retranslate.setToolTip("用当前原文（含手动修改）重新翻译这一条")
        self.btn_retranslate.clicked.connect(self.retranslate_current)
        row.addWidget(self.btn_retranslate)

        self.btn_copy_original = QPushButton("复制原文")
        self.btn_copy_original.clicked.connect(
            lambda: self._copy(self.original.toPlainText())
        )
        row.addWidget(self.btn_copy_original)

        self.btn_copy_translation = QPushButton("复制译文")
        self.btn_copy_translation.clicked.connect(
            lambda: self._copy(self.translation.toPlainText())
        )
        row.addWidget(self.btn_copy_translation)

        row.addStretch(1)
        return row

    # ---------------- 主题 ----------------

    def _theme_name(self):
        return str((self.cfg.get("ui", {}) or {}).get("theme", "dark")).lower()

    def refresh_theme_button(self):
        self.btn_theme.setText("浅色" if self._theme_name() == "dark" else "深色")

    def _on_toggle_theme(self):
        if self._theme_toggler:
            self._theme_toggler()
        self.refresh_theme_button()

    # ---------------- 拖拽导入 ----------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [
            u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()
        ]
        if paths:
            self.add_paths(paths)
            event.acceptProposedAction()

    # ---------------- 导入 ----------------

    def open_import(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "导入图片 / PDF（可多选）", "", FILE_FILTER
        )
        if paths:
            self.add_paths(paths)

    def add_paths(self, paths):
        self.set_status("正在读取文件…")
        run_in_background(
            list_sources, list(paths),
            on_done=self._on_sources_loaded,
            on_error=lambda m: self.set_status(f"读取文件失败：{m}", error=True),
            owner=self._workers,
        )

    def _on_sources_loaded(self, sources):
        sources = list(sources or [])
        if not sources:
            self.set_status("没有可识别的图片或 PDF（支持常见图片格式与 PDF）", error=True)
            return
        for src in sources:
            self._append_source(src)
        self.set_status(f"已加入 {len(sources)} 条任务，开始识别…")
        self.start()

    def _append_source(self, src: Source):
        self.items.append({
            "name": src.name,
            "source": src,
            "thumb": b"",
            "text": "",
            "translation": "",
            "status": _STATUS_PENDING,
            "kind": "idle",
            "error": "",
        })
        item = QListWidgetItem()
        self.queue.addItem(item)
        self._refresh_row(len(self.items) - 1)

    # ---------------- 列表渲染 ----------------

    def _refresh_row(self, index):
        if not (0 <= index < len(self.items)) or index >= self.queue.count():
            return
        data = self.items[index]
        mark = _MARK.get(data["kind"], "·")
        text = f"{mark} {index + 1}. {data['name']}\n      {data['status']}"
        if data.get("error"):
            text += f"\n      {data['error'][:80]}"
        self.queue.item(index).setText(text)
        self.queue.item(index).setToolTip(data["name"])

    def _on_row_changed(self, row):
        self._current = row if 0 <= row < len(self.items) else -1
        self._show_current()
        self._update_buttons()

    def _show_current(self):
        if self._current < 0:
            return
        data = self.items[self._current]
        self.original.blockSignals(True)
        self.translation.blockSignals(True)
        self.original.setPlainText(data["text"])
        self.translation.setPlainText(data["translation"])
        self.original.blockSignals(False)
        self.translation.blockSignals(False)

        raw = data.get("thumb") or b""
        pix = QPixmap()
        if raw and pix.loadFromData(raw):
            self._thumb_pix = pix
        else:
            self._thumb_pix = None
        self._show_preview()

    def _show_preview(self):
        if self._thumb_pix is None or self._thumb_pix.isNull():
            if self._current >= 0:
                self.preview.setText(f"（{self.items[self._current]['name']}）\n\n暂无预览图")
            return
        avail_w = max(120, self.preview.width() - 8)
        avail_h = self.preview.height() or 220
        self.preview.setPixmap(self._thumb_pix.scaled(
            avail_w, avail_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._thumb_pix is not None:
            self._show_preview()

    # ---------------- 处理流程 ----------------

    def _provider(self):
        return str((self.cfg.get("translate", {}) or {}).get("provider", "off")).lower()

    def auto_translate_enabled(self) -> bool:
        """当前是否启用「识别后自动翻译」（供快捷键 / 托盘调用）。"""
        return self.chk_translate.isChecked() and self._provider() != "off"

    def _on_auto_translate_toggled(self, checked):
        """勾选状态立刻写入配置，下次启动保持。"""
        tr = self.cfg.setdefault("translate", {})
        tr["auto_translate"] = bool(checked)
        save_config(self.cfg)
        if checked and self._provider() == "off":
            self.set_status("已勾选自动翻译，但翻译服务是「关闭」状态（设置 → 翻译 可开启）", error=True)
        else:
            self.set_status("已开启：识别后自动翻译" if checked else "已关闭：识别后不自动翻译")

    def _build_tasks(self, indexes, with_ocr=True):
        tasks = []
        for i in indexes:
            tasks.append({
                "index": i,
                "source": self.items[i]["source"] if with_ocr else None,
                "text": self.items[i]["text"] if not with_ocr else "",
            })
        return tasks

    def start(self, do_translate=None):
        """开始识别待处理的任务。

        do_translate=None 表示按主界面上「识别后自动翻译」的勾选；
        传 True/False 则本次覆盖（不会改动勾选状态与配置）。
        """
        if self._runner is not None:
            return
        pending = [
            i for i, d in enumerate(self.items) if d["kind"] in ("idle", "fail")
        ]
        if not pending:
            self.set_status("没有待识别的任务，请先截图或导入图片 / PDF", error=True)
            return
        if do_translate is None:
            do_translate = self.auto_translate_enabled()
        else:
            do_translate = bool(do_translate) and self._provider() != "off"
        for i in pending:
            self.items[i]["kind"] = "busy"
            self.items[i]["status"] = _STATUS_PENDING
            self.items[i]["error"] = ""
            self._refresh_row(i)
        self._run_runner(self._build_tasks(pending, True), do_ocr=True, do_translate=do_translate)

    def translate_all(self):
        if self._runner is not None:
            self.set_status("已有任务在执行，请稍候…", error=True)
            return
        if self._provider() == "off":
            self.set_status("当前翻译服务为关闭状态（设置 → 翻译 可开启）", error=True)
            return
        targets = [i for i, d in enumerate(self.items) if d["text"].strip()]
        if not targets:
            self.set_status("还没有可翻译的原文，请先识别", error=True)
            return
        for i in targets:
            self.items[i]["kind"] = "busy"
            self.items[i]["status"] = _STATUS_TR
            self._refresh_row(i)
        self._run_runner(self._build_tasks(targets, False), do_ocr=False, do_translate=True)

    def _run_runner(self, tasks, do_ocr, do_translate):
        runner = BatchRunner(tasks, self.cfg, do_ocr=do_ocr, do_translate=do_translate)
        runner.signals.item_status.connect(self._on_item_status)
        runner.signals.item_done.connect(self._on_item_done)
        runner.signals.progress.connect(self._on_progress)
        runner.signals.failed.connect(lambda m: self.set_status(f"批量处理失败：{m}", error=True))
        runner.signals.finished.connect(self._on_batch_finished)
        self._runner = runner
        self.progress.setRange(0, max(1, len(tasks)))
        self.progress.setValue(0)
        self._update_buttons()
        QThreadPool.globalInstance().start(runner)

    def stop(self):
        if self._runner is not None:
            self._runner.cancel()
            self.set_status("正在停止…")

    def _on_item_status(self, index, status):
        if 0 <= index < len(self.items):
            self.items[index]["status"] = status
            self._refresh_row(index)

    def _on_item_done(self, index, result):
        if not (0 <= index < len(self.items)):
            return
        data = self.items[index]
        error = str(result.get("error") or "").strip()
        data["text"] = str(result.get("text") or "").strip() or data["text"]
        data["translation"] = str(result.get("translation") or "").strip()
        if result.get("thumb"):
            data["thumb"] = result["thumb"]

        recognized = bool(data["text"])
        if error:
            data["error"] = error
            if recognized:
                # 识别成功、只是翻译没成功 —— 别把整条判成失败，原文仍然可用
                data["kind"] = "ok"
                data["status"] = f"已识别（翻译失败：{error[:46]}）"
            else:
                data["kind"] = "fail"
                data["status"] = f"失败：{error[:60]}"
        else:
            data["kind"] = "ok"
            data["status"] = _STATUS_DONE if recognized else "完成（未识别到文字）"
            data["error"] = ""
        self._refresh_row(index)
        if index == self._current:
            self._show_current()

        # 截图自动朗读（只针对本次截图那一条；识别成功即可朗读）
        if (index == self._auto_read_index and self._auto_read != "off"
                and (data["text"] or data["translation"])):
            if self._auto_read == "translation" and data["translation"]:
                self._speak(data["translation"])
            elif self._auto_read == "original" and data["text"]:
                self._speak(data["text"])
            self._auto_read = "off"
            self._auto_read_index = -1

    def _on_progress(self, done, total):
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(done)

    def _on_batch_finished(self):
        self._runner = None
        self._update_buttons()
        ok = sum(1 for d in self.items if d["kind"] == "ok")
        fail = sum(1 for d in self.items if d["kind"] == "fail")
        tr_fail = sum(
            1 for d in self.items if "翻译失败" in str(d.get("status", ""))
        )
        parts = [f"成功 {ok} 条"]
        if fail:
            parts.append(f"失败 {fail} 条")
        if tr_fail:
            parts.append(f"其中 {tr_fail} 条仅翻译失败（原文已识别）")
        msg = "处理结束：" + "，".join(parts)
        if tr_fail:
            msg += "　→ 请在 设置 → 翻译 检查服务器地址，或改用 Google / 关闭翻译"
            self.set_status(msg, error=True)
        else:
            self.set_status(msg)

    def _update_buttons(self):
        busy = self._runner is not None
        self.btn_start.setEnabled(not busy)
        self.btn_translate_all.setEnabled(not busy)
        self.btn_import.setEnabled(True)
        self.btn_stop.setEnabled(busy)
        self.btn_capture.setEnabled(True)
        self.btn_clear.setEnabled(not busy and bool(self.items))
        self.btn_export.setEnabled(bool(self.items))
        has_current = self._current >= 0
        for b in (self.btn_read_original, self.btn_read_translation,
                  self.btn_retranslate, self.btn_copy_original,
                  self.btn_copy_translation):
            b.setEnabled(has_current)

    # ---------------- 单条操作 ----------------

    def retranslate_current(self):
        if self._current < 0:
            return
        if self._provider() == "off":
            self.set_status("当前未开启翻译（可在 设置 → 翻译 选择服务商）", error=True)
            return
        text = self.original.toPlainText().strip()
        if not text:
            self.set_status("没有可翻译的文本", error=True)
            return
        index = self._current
        self.items[index]["text"] = text
        self.items[index]["status"] = _STATUS_TR
        self.items[index]["kind"] = "busy"
        self._refresh_row(index)
        self.set_status("正在翻译…")
        run_in_background(
            translate_text, text, resolve_translate_cfg(self.cfg),
            on_done=lambda out: self._on_single_translated(index, out),
            on_error=lambda m: self._on_single_translate_failed(index, m),
            owner=self._workers,
        )

    def _on_single_translated(self, index, out):
        out = str(out or "").strip()
        self.items[index]["translation"] = out
        self.items[index]["kind"] = "ok"
        self.items[index]["status"] = _STATUS_DONE
        self._refresh_row(index)
        if index == self._current:
            self.translation.setPlainText(out)
        self.set_status("翻译完成")

    def _on_single_translate_failed(self, index, msg):
        self.items[index]["kind"] = "fail"
        self.items[index]["status"] = f"翻译失败：{str(msg)[:60]}"
        self._refresh_row(index)
        self.set_status(f"翻译失败：{msg}", error=True)

    # ---------------- 导出 / 清空 ----------------

    def export_results(self):
        if not self.items:
            self.set_status("列表为空，没有可导出的内容", error=True)
            return
        default = f"识图翻译结果_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出识别 / 翻译结果", default, "文本文件 (*.txt);;所有文件 (*.*)"
        )
        if not path:
            return
        chunks = []
        for i, d in enumerate(self.items, 1):
            chunks.append(f"===== {i}. {d['name']} =====")
            chunks.append("【原文】")
            chunks.append(d["text"] or "（空）")
            if d["translation"]:
                chunks.append("【译文】")
                chunks.append(d["translation"])
            if d.get("error"):
                chunks.append(f"【错误】{d['error']}")
            chunks.append("")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(chunks))
        except OSError as exc:
            self.set_status(f"导出失败：{exc}", error=True)
            return
        self.set_status(f"已导出 {len(self.items)} 条结果：{path}")

    def clear_items(self):
        if self._runner is not None:
            self.set_status("任务执行中，请先停止再清空", error=True)
            return
        if self.items:
            ok = QMessageBox.question(
                self, "识图翻译", f"确定清空列表中的 {len(self.items)} 条任务？"
            )
            if ok != QMessageBox.StandardButton.Yes:
                return
        self.items.clear()
        self.queue.clear()
        self._current = -1
        self._thumb_pix = None
        self.original.setPlainText("")
        self.translation.setPlainText("")
        self.preview.setPixmap(QPixmap())
        self.preview.setText("尚未选择任务\n\n点击左上「截图识别」，或「导入图片 / PDF」批量识别")
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self._update_buttons()
        self.set_status("已清空列表")

    # ---------------- 截图接入 ----------------

    def present_capture(self, pixmap, auto_translate=None, auto_read="off"):
        """把截图并入任务列表并识别。

        auto_translate=None 表示沿用主界面「识别后自动翻译」的勾选，
        不会再像以前那样把用户的勾选强制改回默认。
        """
        data = pixmap_to_png_bytes(pixmap, 1.0)
        name = f"截图 {time.strftime('%H:%M:%S')}"
        self._auto_read = auto_read
        self._auto_read_index = len(self.items)
        self._append_source(Source(name=name, image_bytes=data))
        self.raise_()
        self.activateWindow()
        row = len(self.items) - 1
        self.queue.setCurrentRow(row)
        self.set_status("正在识别文字…")
        self.start(do_translate=auto_translate)

    # ---------------- 辅助 ----------------

    def _copy(self, text):
        if not (text or "").strip():
            self.set_status("没有可复制的内容", error=True)
            return
        QApplication.clipboard().setText(text)
        self.set_status("已复制到剪贴板")

    def _speak(self, text):
        if not (text or "").strip():
            self.set_status("没有可朗读的内容", error=True)
            return
        self.tts.speak(text)
        name = self.items[self._current]["name"] if self._current >= 0 else ""
        self.set_status(f"正在朗读…{('（' + name + '）') if name else ''}")

    def _on_tts_state(self, state):
        playing = str(state) == "playing"
        self.btn_read_original.setText("朗读中…" if playing else "朗读原文")
        self.btn_read_translation.setText("朗读中…" if playing else "朗读译文")
        self.btn_stop_speak.setEnabled(True)

    def set_status(self, msg, error=False):
        self.status.setText(msg)
        self.status.setStyleSheet("color:#f87171;" if error else "")

    def closeEvent(self, event):
        # 关闭主窗口时仅隐藏，常驻托盘可再次唤回
        event.ignore()
        self.tts.stop()
        self.hide()
