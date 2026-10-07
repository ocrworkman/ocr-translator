"""批量识别 / 翻译流水线。

在主线程之外的 QThreadPool 里逐条处理素材，通过信号把每条结果回传界面：
    item_status(index, "识别中")          —— 更新列表状态
    item_done(index, {...})              —— 单条结果（原文 / 译文 / 缩略图 / 错误）
    progress(done, total)                —— 进度条
    finished()                           —— 全部结束（含被取消）
"""
import logging

from PySide6.QtCore import QObject, QRunnable, Signal

from core.config import resolve_ocr_cfg, resolve_translate_cfg
from core.ingest import make_thumbnail, render_source_png
from core.ocr import recognize_image
from core.translators import translate_text


class BatchSignals(QObject):
    item_status = Signal(int, str)
    item_done = Signal(int, object)
    progress = Signal(int, int)
    finished = Signal()
    failed = Signal(str)


class BatchRunner(QRunnable):
    """逐条处理任务列表。

    tasks: [{"index": int, "source": Source|None, "text": str}, ...]
        do_ocr=True  时使用 source 渲染 + 识别；False 时直接用 text（"整批翻译"用）
    """

    def __init__(self, tasks, cfg, *, do_ocr=True, do_translate=True):
        super().__init__()
        self.setAutoDelete(False)
        self.signals = BatchSignals()
        self._tasks = list(tasks)
        self._cfg = cfg
        self._do_ocr = do_ocr
        self._do_translate = do_translate
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        total = len(self._tasks)
        try:
            ocr_cfg = resolve_ocr_cfg(self._cfg)
            tr_cfg = resolve_translate_cfg(self._cfg)
            translate_on = (
                self._do_translate
                and str(tr_cfg.get("provider", "off")).lower() != "off"
            )
            try:
                dpi = int(ocr_cfg.get("pdf_dpi", 200))
            except (TypeError, ValueError):
                dpi = 200
            try:
                upscale = float(ocr_cfg.get("upscale", 1.0) or 1.0)
            except (TypeError, ValueError):
                upscale = 1.0

            for done, task in enumerate(self._tasks):
                if self._cancel:
                    break
                idx = int(task.get("index", done))
                source = task.get("source")
                result = {
                    "text": str(task.get("text") or ""),
                    "translation": "",
                    "thumb": b"",
                    "error": "",
                }
                try:
                    if self._do_ocr and source is not None:
                        self.signals.item_status.emit(idx, "识别中…")
                        # PDF 由 DPI 控制清晰度，不再叠加放大倍数
                        factor = 1.0 if source.page is not None else upscale
                        png = render_source_png(source, dpi=dpi, upscale=factor)
                        result["thumb"] = make_thumbnail(png)
                        result["text"] = str(recognize_image(png, ocr_cfg) or "").strip()
                    if self._cancel:
                        break
                    if translate_on and result["text"]:
                        self.signals.item_status.emit(idx, "翻译中…")
                        result["translation"] = str(
                            translate_text(result["text"], tr_cfg) or ""
                        ).strip()
                except Exception as exc:  # noqa: BLE001
                    # 单条失败（含翻译接口不可用等预期内情况）只记一行，避免 error.log 被刷屏
                    logging.warning("批量处理单条失败 #%s：%s", idx, exc)
                    result["error"] = str(exc) or exc.__class__.__name__
                if self._cancel:
                    break
                self.signals.item_done.emit(idx, result)
                self.signals.progress.emit(done + 1, total)
        except Exception as exc:  # noqa: BLE001
            logging.exception("批量处理启动失败")
            self.signals.failed.emit(str(exc))
        finally:
            self.signals.finished.emit()
