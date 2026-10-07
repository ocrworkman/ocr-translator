"""载入待识别素材：图片文件、PDF 页面、内存位图（截图）。

- 图片：QImage 解码（支持 png/jpg/bmp/webp/tif/gif 等 Qt 自带格式）
- PDF ：用 pypdfium2 逐页渲染成位图，可指定 DPI，清晰度越高识别越准、越慢
         （不用 PyMuPDF：本机 Python 3.10.0 的 dis 模块有 bug，PyInstaller 分析
          pymupdf 字节码会崩溃；pypdfium2 是纯二进制依赖，打包无坑）
- 缩略图：用于主界面预览，避免把整张原图塞进界面

所有函数都设计为可在工作线程中调用（只碰 QImage，不碰 QPixmap/QWidget）。
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage

IMAGE_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".jpe", ".jfif", ".bmp",
    ".webp", ".tif", ".tiff", ".gif", ".ico",
}
PDF_SUFFIXES = {".pdf"}

FILE_FILTER = (
    "图片和 PDF (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff *.gif *.pdf);;"
    "图片 (*.png *.jpg *.jpeg *.bmp *.webp *.tif *.tiff *.gif);;"
    "PDF (*.pdf);;"
    "所有文件 (*.*)"
)


@dataclass
class Source:
    """一条待识别素材（一张图片 / PDF 的某一页）。"""

    name: str                          # 列表中显示的名字
    path: str = ""                     # 磁盘路径，截图类为空
    page: int | None = None            # PDF 页码（0 起）；非 PDF 为 None
    image_bytes: bytes | None = None   # 内存中的原始图（截图 / 拖入的图）


# ---------------- PDF 后端适配 ----------------

PDF_MISSING_MSG = "未安装 PDF 解析库，无法读取 PDF。请执行：pip install pypdfium2"


def _pdf_backend():
    """返回 (名称, 模块)；优先 pypdfium2，退化到 PyMuPDF（用动态导入避免打包分析）。"""
    try:
        return "pypdfium2", importlib.import_module("pypdfium2")
    except Exception:  # noqa: BLE001
        pass
    for mod_name in ("pymupdf", "fitz"):
        try:
            return mod_name, importlib.import_module(mod_name)
        except Exception:  # noqa: BLE001
            continue
    return "", None


def pdf_available() -> bool:
    return _pdf_backend()[1] is not None


# ---------------- 枚举素材 ----------------

def list_sources(paths) -> list:
    """把用户选中的文件/文件夹展开成 Source 列表（PDF 逐页展开）。"""
    out = []
    for raw in paths or []:
        p = Path(str(raw))
        try:
            if p.is_dir():
                for child in sorted(p.iterdir()):
                    if child.is_file():
                        out.extend(_expand_file(child))
            elif p.is_file():
                out.extend(_expand_file(p))
        except OSError:
            continue
    return out


def _expand_file(path: Path) -> list:
    suffix = path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        return _expand_pdf(path)
    if suffix in IMAGE_SUFFIXES:
        return [Source(name=path.name, path=str(path))]
    return []


def _expand_pdf(path: Path) -> list:
    count = _pdf_page_count(path)
    if count <= 0:
        return []
    if count == 1:
        return [Source(name=path.name, path=str(path), page=0)]
    return [
        Source(name=f"{path.name} · 第 {i + 1} 页", path=str(path), page=i)
        for i in range(count)
    ]


def _pdf_page_count(path: Path) -> int:
    name, backend = _pdf_backend()
    if backend is None:
        raise RuntimeError(PDF_MISSING_MSG)
    try:
        if name == "pypdfium2":
            doc = backend.PdfDocument(str(path))
            try:
                return len(doc)
            finally:
                doc.close()
        with backend.open(str(path)) as doc:      # PyMuPDF
            return int(doc.page_count)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"读取 PDF 失败（{Path(path).name}）：{exc}") from exc


# ---------------- 渲染 ----------------

def render_source_png(source: Source, dpi: int = 200, upscale: float = 1.0) -> bytes:
    """把素材渲染成用于识别的 PNG 字节。"""
    if source.page is not None:
        img = _render_pdf_page(source.path, source.page, dpi)
    elif source.image_bytes is not None:
        img = QImage.fromData(source.image_bytes)
        if img.isNull():
            img = _load_image_file(source.path)   # 内存图解析失败时退回按路径读
    else:
        img = _load_image_file(source.path)

    if img is None or img.isNull():
        raise RuntimeError(f"无法读取图片：{source.name}")

    factor = _safe_float(upscale, 1.0)
    if abs(factor - 1.0) > 1e-6:
        w = max(1, round(img.width() * factor))
        h = max(1, round(img.height() * factor))
        img = img.scaled(
            w, h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return _to_png(img)


def _load_image_file(path: str) -> QImage:
    if not path:
        return QImage()
    img = QImage(path)
    if img.isNull():
        try:
            from PySide6.QtGui import QImageReader

            reader = QImageReader(path)
            reader.setAutoTransform(True)      # 按 EXIF 自动转正
            img = reader.read()
        except Exception:  # noqa: BLE001
            pass
    return img


def _render_pdf_page(path: str, page_index: int, dpi: int) -> QImage:
    name, backend = _pdf_backend()
    if backend is None:
        raise RuntimeError(PDF_MISSING_MSG)
    zoom = max(0.5, _safe_float(dpi, 200) / 72.0)
    try:
        if name == "pypdfium2":
            img = _render_pdfium2(backend, path, page_index, zoom)
        else:
            img = _render_pymupdf(backend, path, page_index, zoom)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"渲染 PDF 第 {int(page_index) + 1} 页失败：{exc}") from exc
    if img is None or img.isNull():
        raise RuntimeError(f"渲染 PDF 第 {int(page_index) + 1} 页失败：结果为空白")
    return img


def _render_pdfium2(pdfium, path: str, page_index: int, zoom: float) -> QImage:
    doc = pdfium.PdfDocument(path)
    try:
        page = doc[int(page_index)]
        try:
            bitmap = page.render(scale=zoom)
            width, height = int(bitmap.width), int(bitmap.height)
            stride = int(bitmap.stride)
            channels = int(getattr(bitmap, "n_channels", 3))
            fmt = QImage.Format.Format_BGRA8888 if channels == 4 else QImage.Format.Format_BGR888
            img = QImage(bytes(bitmap.buffer), width, height, stride, fmt).copy()
        finally:
            page.close()
    finally:
        doc.close()
    return img


def _render_pymupdf(fitz, path: str, page_index: int, zoom: float) -> QImage:
    with fitz.open(path) as doc:
        page = doc.load_page(int(page_index))
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return QImage(
            pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888
        ).copy()


# ---------------- 缩略图 ----------------

def make_thumbnail(image_bytes: bytes, max_side: int = 1400) -> bytes:
    """生成预览用缩略图（PNG 字节）；失败返回空字节。"""
    img = QImage.fromData(image_bytes)
    if img.isNull():
        return b""
    if max(img.width(), img.height()) > max_side:
        img = img.scaled(
            max_side, max_side,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    try:
        return _to_png(img)
    except RuntimeError:
        return b""


# ---------------- 内部工具 ----------------

def _to_png(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not img.save(buf, "PNG"):
        raise RuntimeError("图片转 PNG 失败")
    data = bytes(buf.data())
    buf.close()
    return data


def _safe_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
