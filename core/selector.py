"""屏幕区域框选：冻结屏幕 -> 拖拽框选 -> 裁剪为 QPixmap。

冻结屏幕优先使用 Win32 GDI BitBlt 直接抓取整个虚拟桌面的真实像素，
避免 PySide6 QScreen.grabWindow(0) 在打包 exe / 特定显卡驱动下返回全黑的问题；
GDI 不可用时回退到 Qt 抓屏。
"""
import ctypes
import logging
from ctypes import wintypes

from PySide6.QtCore import QObject, QRect, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QImage, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QWidget

MIN_SIZE = 6  # 逻辑像素，小于该尺寸视为误点击

# 虚拟桌面（所有显示器合并后的外接矩形，可为负坐标）
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79
_SRCCOPY = 0x00CC0020
_CAPTUREBLT = 0x40000000


def _capture_virtual_desktop():
    """用 GDI BitBlt 抓取整个虚拟桌面，返回 (QPixmap, 全局逻辑起点, 设备宽, 设备高)。

    成功返回 (pixmap, origin QPoint, dev_w, dev_h)，失败返回 None。
    """
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
    except Exception:  # 非 Windows / 无法加载
        return None

    try:
        user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        user32.GetSystemMetrics.restype = ctypes.c_int
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.ReleaseDC.restype = ctypes.c_int
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.BitBlt.argtypes = [
            wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
            wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD,
        ]
        gdi32.BitBlt.restype = wintypes.BOOL
        gdi32.GetDIBits.argtypes = [
            wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
            ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
        ]
        gdi32.GetDIBits.restype = ctypes.c_int
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteDC.argtypes = [wintypes.HDC]

        x = user32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
        y = user32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
        w = user32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
        h = user32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)
        if w <= 0 or h <= 0:
            return None

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_int),
                ("biHeight", ctypes.c_int), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_int),
                ("biYPelsPerMeter", ctypes.c_int), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        from PySide6.QtCore import QPoint
        hdc = user32.GetDC(None)
        if not hdc:
            return None
        memdc = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        old = gdi32.SelectObject(memdc, bmp)
        try:
            if not gdi32.BitBlt(memdc, 0, 0, w, h, hdc, x, y, _SRCCOPY | _CAPTUREBLT):
                return None
            bi = BITMAPINFOHEADER()
            bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bi.biWidth = w
            bi.biHeight = -h  # 负值 = 自顶向下，行序与 QImage 一致
            bi.biPlanes = 1
            bi.biBitCount = 32
            bi.biCompression = 0  # BI_RGB
            buf = ctypes.create_string_buffer(w * h * 4)
            if gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bi), 0) != h:
                return None
            image = QImage(buf, w, h, w * 4, QImage.Format_ARGB32).copy()
            if image.isNull():
                return None
            pix = QPixmap.fromImage(image)
            pix.setDevicePixelRatio(1.0)
            return pix, QPoint(x, y), w, h
        finally:
            gdi32.SelectObject(memdc, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(memdc)
            user32.ReleaseDC(None, hdc)
    except Exception:  # noqa: BLE101
        logging.exception("GDI 抓屏失败，回退到 Qt 抓屏")
        return None


def _capture_via_qt():
    """回退方案：用 Qt 抓取整个虚拟桌面（可能受平台/DPI 影响）。"""
    screens = QGuiApplication.screens()
    if not screens:
        return None
    from PySide6.QtCore import QPoint
    union = QRect()
    for s in screens:
        union = union.united(s.geometry())
    canvas = QPixmap(union.width(), union.height())
    canvas.fill(Qt.GlobalColor.black)
    painter = QPainter(canvas)
    for s in screens:
        pix = s.grabWindow(0)
        geo = s.geometry().translated(-union.topLeft())
        painter.drawPixmap(geo, pix)
    painter.end()
    canvas.setDevicePixelRatio(1.0)
    return canvas, union.topLeft(), canvas.width(), canvas.height()


class OverlayWindow(QWidget):
    """覆盖整个虚拟桌面的全屏遮罩，显示冻结的屏幕截图并框选。"""

    selected = Signal(QRect)   # 全局逻辑坐标下的选区
    cancelled = Signal()

    def __init__(self, pixmap, origin, dev_w, dev_h, union, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self._pix = pixmap          # 整块虚拟桌面截图（设备像素）
        self._origin = origin       # 截图对应的全局逻辑起点
        self._dev_w = dev_w
        self._dev_h = dev_h
        self._union = union         # 逻辑虚拟桌面矩形（遮罩覆盖范围）
        self._start = None
        self._cur = None
        self._finished = False
        self.setGeometry(union)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def showEvent(self, event):
        self.raise_()
        self.activateWindow()
        self.setFocus()
        self.grabKeyboard()
        super().showEvent(event)

    def closeEvent(self, event):
        self.releaseKeyboard()
        if not self._finished:
            self.cancelled.emit()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self._finished = True
            self.close()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._start = self.mapToGlobal(event.position().toPoint())
            self._cur = self._start
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self._finished = True
            self.close()

    def mouseMoveEvent(self, event):
        if self._start is not None:
            self._cur = self.mapToGlobal(event.position().toPoint())
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton or self._start is None:
            return
        rect = QRect(self._start, self._cur).normalized()
        self._start = self._cur = None
        if rect.width() < MIN_SIZE or rect.height() < MIN_SIZE:
            self._finished = True
            self.close()
            return
        self._finished = True
        self.selected.emit(rect)
        self.close()

    def _local_rect(self) -> QRect:
        return QRect(self._start, self._cur).normalized().translated(-self._union.topLeft())

    def paintEvent(self, event):
        p = QPainter(self)
        target = QRect(0, 0, self.width(), self.height())
        # 1. 画冻结的整屏快照（把设备像素图缩放到逻辑窗口大小，100% 缩放下即 1:1）
        p.drawPixmap(target, self._pix)
        # 2. 整体压暗
        p.fillRect(self.rect(), QColor(0, 0, 0, 100))

        if self._start is not None and self._cur is not None:
            r = QRectF(self._local_rect())
            # 选区内恢复亮度
            p.save()
            p.setClipRect(r.toRect())
            p.drawPixmap(target, self._pix)
            p.restore()
            p.setPen(QPen(QColor("#3B82F6"), 2))
            p.drawRect(r)
            # 尺寸角标
            label = f"{r.width():.0f} × {r.height():.0f}"
            font = QFont(self.font())
            font.setBold(True)
            p.setFont(font)
            chip_w = max(96, 14 * len(label) + 24)
            ly = r.bottom() + 8
            if ly + 30 > self.height():
                ly = r.top() - 38
            chip = QRectF(r.left(), max(4.0, ly), chip_w, 28)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#2563EB"))
            p.drawRoundedRect(chip, 6, 6)
            p.setPen(QColor("white"))
            p.drawText(chip, Qt.AlignmentFlag.AlignCenter, label)
        else:
            # 顶部提示条
            hint = "按住鼠标左键拖拽框选识别区域 · Esc / 右键取消"
            font = QFont(self.font())
            font.setPointSize(10)
            p.setFont(font)
            chip = QRectF((self.width() - 470) / 2, 18, 470, 34)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(17, 24, 39, 225))
            p.drawRoundedRect(chip, 8, 8)
            p.setPen(QColor("#e5e7eb"))
            p.drawText(chip, Qt.AlignmentFlag.AlignCenter, hint)
        p.end()


class RegionSelector(QObject):
    captured = Signal(QRect, QPixmap)
    cancelled = Signal()
    failed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pix = None
        self._origin = None
        self._dev_w = 0
        self._dev_h = 0
        self._union = None
        self._overlay = None

    def start(self) -> bool:
        if self._overlay is not None:
            return True  # 已在框选中
        screens = QGuiApplication.screens()
        if not screens:
            return False
        union = QRect()
        for s in screens:
            union = union.united(s.geometry())   # 逻辑坐标，作为遮罩覆盖范围
        captured = _capture_virtual_desktop() or _capture_via_qt()
        if not captured:
            return False
        self._pix, self._origin, self._dev_w, self._dev_h = captured
        self._union = union
        self._overlay = OverlayWindow(
            self._pix, self._origin, self._dev_w, self._dev_h, self._union
        )
        self._overlay.selected.connect(self._on_selected)
        self._overlay.cancelled.connect(self._on_cancelled)
        self._overlay.show()
        return True

    def _cleanup(self):
        if self._overlay is not None:
            overlay, self._overlay = self._overlay, None
            overlay.close()
            overlay.deleteLater()
        self._pix = None

    def _on_selected(self, rect: QRect):
        pix = self._crop(rect)
        self._cleanup()
        if pix is None or pix.isNull():
            self.failed.emit("未能截取所选区域（选区可能落在屏幕范围外）")
        else:
            self.captured.emit(rect, pix)

    def _on_cancelled(self):
        self._cleanup()
        self.cancelled.emit()

    def _crop(self, rect: QRect):
        """把全局逻辑坐标选区映射回设备像素，从整屏截图中裁出。"""
        if self._pix is None:
            return None
        union = self._union
        # 逻辑 → 设备 的缩放系数（100% 缩放下为 1.0）
        sx = self._dev_w / max(1, union.width())
        sy = self._dev_h / max(1, union.height())
        logical = rect.intersected(union).translated(-union.topLeft())
        if logical.isEmpty():
            return None
        dev = QRect(
            round(logical.x() * sx), round(logical.y() * sy),
            max(1, round(logical.width() * sx)), max(1, round(logical.height() * sy)),
        )
        dev = dev.intersected(QRect(0, 0, self._pix.width(), self._pix.height()))
        if dev.isEmpty():
            return None
        out = self._pix.copy(dev)
        out.setDevicePixelRatio(1)
        return out
