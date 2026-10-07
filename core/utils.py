"""通用小工具：后台任务 Worker、QPixmap 转 PNG 字节。"""
import traceback

from PySide6.QtCore import QBuffer, QIODevice, QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QPixmap


class WorkerSignals(QObject):
    done = Signal(object)
    error = Signal(str)
    finished = Signal()


class Worker(QRunnable):
    """在 QThreadPool 里执行 fn，结果通过信号回主线程。"""

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.setAutoDelete(False)
        self.signals = WorkerSignals()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs

    def run(self):
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.signals.error.emit(str(exc))
        else:
            self.signals.done.emit(result)
        finally:
            self.signals.finished.emit()


def run_in_background(fn, *args, on_done=None, on_error=None, owner=None, **kwargs):
    """启动后台任务。

    owner 传入一个 set，用于持有 Worker 引用防止被 GC（任务结束后自动移除）。
    返回 Worker，调用方也可自行持有。
    """
    worker = Worker(fn, *args, **kwargs)
    if on_done is not None:
        worker.signals.done.connect(on_done)
    if on_error is not None:
        worker.signals.error.connect(on_error)
    if owner is not None:
        owner.add(worker)
        worker.signals.finished.connect(lambda: owner.discard(worker))
    QThreadPool.globalInstance().start(worker)
    return worker


def pixmap_to_png_bytes(pix: QPixmap, upscale: float = 1.0) -> bytes:
    """把截图转成 PNG 字节，可选放大（有助于识别小字）。"""
    img = pix
    try:
        factor = float(upscale or 1.0)
    except (TypeError, ValueError):
        factor = 1.0
    if abs(factor - 1.0) > 1e-6:
        w = max(1, round(img.width() * factor))
        h = max(1, round(img.height() * factor))
        img = img.scaled(
            w, h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not img.save(buf, "PNG"):
        raise RuntimeError("截图转 PNG 失败")
    data = bytes(buf.data())
    buf.close()
    return data
