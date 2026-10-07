"""识图翻译 —— 截图 OCR / 翻译 / 语音合成 常驻工具（托盘运行）。

用法：
    python main.py          常规运行（保留控制台，便于看日志）
    pythonw main.py         后台静默运行
    python main.py --selftest   自检：检查依赖与关键链路，结果写入 selftest.log

默认快捷键：
    Ctrl+Alt+O  截图 -> 识别 -> 翻译
    Ctrl+Alt+S  截图 -> 识别 -> 直接朗读
"""
import ctypes
import logging
import os
import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from core.config import BASE_DIR, load_config
from core.controller import Controller
from ui.theme import make_app_icon

LOG_FILE = BASE_DIR / "error.log"
SELFTEST_FILE = BASE_DIR / "selftest.log"

_MUTEX_NAME = "Local/ShiTuFanYi_SingleInstance_Mutex"
_ERROR_ALREADY_EXISTS = 183
_mutex_handle = None  # 进程生命周期内持有，勿回收


def run_selftest() -> int:
    """自检：不启动常驻服务，检查依赖与关键链路，结果写入 selftest.log。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")   # 纯后台，不弹窗
    lines = []
    failed = []

    def check(name, fn):
        try:
            lines.append(f"[OK]   {name}: {fn()}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
            failed.append(name)

    lines.append(f"Python {sys.version.split()[0]} | frozen={getattr(sys, 'frozen', False)}")
    lines.append(f"BASE_DIR = {BASE_DIR}")

    cfg = load_config()
    tr = cfg["translate"]
    tr_addr = (
        tr.get("zndjs_url") if str(tr.get("provider", "")).lower() == "zndjs"
        else tr.get("openai_base_url") or tr.get("google_target_code") or ""
    )
    lines.append(
        f"[OK]   配置读取: OCR={cfg['ocr']['url']} | "
        f"翻译={tr.get('provider')} {tr_addr} | "
        f"PDF DPI={cfg['ocr'].get('pdf_dpi')} | TTS={cfg['tts']['engine']}"
    )

    def _pdf():
        import pypdfium2

        from core.ingest import pdf_available

        if not pdf_available():
            raise RuntimeError("没有可用的 PDF 后端（需要 pypdfium2）")
        return f"pypdfium2 {getattr(pypdfium2, 'V_PYPDFIUM2', '可用')}"

    def _multimedia():
        from PySide6.QtMultimedia import QMediaPlayer  # noqa: F401

        return "QtMultimedia 可用（Edge 在线语音）"

    def _tts_lib():
        import edge_tts  # noqa: F401
        import pyttsx3  # noqa: F401

        return "edge-tts + pyttsx3 均可导入"

    def _roundtrip():
        """程序自己生成测试图，跑 渲染 → 缩略图 → 识别请求 链路（识别失败不算自检失败）。"""
        from core.config import resolve_ocr_cfg
        from core.ingest import Source, make_thumbnail, render_source_png
        from core.ocr import recognize_image
        from ui.settings_dialog import SettingsDialog

        png = SettingsDialog._make_test_png()
        rendered = render_source_png(Source(name="selftest", image_bytes=png), dpi=200)
        thumb = make_thumbnail(rendered)
        try:
            text = recognize_image(rendered, resolve_ocr_cfg(cfg))
            note = str(text).strip()
            ocr_note = f"识别返回：{note[:60]}" if note else (
                "识别返回为空（自检是无窗口渲染，字体可能画成方块；"
                "以程序里「测试识别」的结果为准）"
            )
        except Exception as exc:  # noqa: BLE001
            ocr_note = f"识别未通过（不算失败）：{exc}"
        return f"测试图 {len(png)}B → 渲染 {len(rendered)}B / 缩略图 {len(thumb)}B；{ocr_note}"

    app = QApplication.instance() or QApplication(sys.argv)

    def _window():
        from core.tts import TTSService
        from ui.main_window import MainWindow

        tts = TTSService(cfg)
        win = MainWindow(cfg, tts)
        count = win.queue.count()
        win.hide()
        tts.stop()
        return f"主窗口 + 任务列表构建成功（当前 {count} 条任务）"

    def _settings():
        from ui.settings_dialog import SettingsDialog

        dlg = SettingsDialog(cfg)
        dlg.done(0)
        return "设置窗口（含服务器地址项）构建成功"

    def _translate():
        """翻译接口连通性（会消耗少量额度，失败不计入自检失败）。"""
        from core.config import resolve_translate_cfg
        from core.translators import translate_text

        tr = resolve_translate_cfg(cfg)
        provider = str(tr.get("provider", "off")).lower()
        if provider == "off":
            return "翻译服务为关闭状态（跳过）"
        target = str(tr.get("target_lang") or "")
        try:
            out = str(translate_text("Good morning everyone", tr)).strip()
            note = ""
            if out.lower() == "good morning everyone":
                note = "（与原文相同：目标语言就是英语，属正常）"
            return f"{provider} 可用，译成「{target}」：{out[:60]}{note}"
        except Exception as exc:  # noqa: BLE001
            return f"{provider} 未通过（不算失败）：{exc}"

    check("PDF 渲染后端", _pdf)
    check("在线语音组件", _multimedia)
    check("语音合成库", _tts_lib)
    check("图片渲染与缩略图", _roundtrip)
    check("翻译接口连通性", _translate)
    check("主界面构建", _window)
    check("设置窗口构建", _settings)

    lines.append("")
    lines.append("结论：" + ("全部通过" if not failed else "有失败项 → " + ", ".join(failed)))
    text = "\n".join(lines)
    print(text)
    try:
        SELFTEST_FILE.write_text(text, encoding="utf-8")
    except OSError:
        pass
    return 0 if not failed else 1


def _acquire_single_instance():
    """返回 True 表示本进程是唯一实例；False 表示已有实例在运行。"""
    global _mutex_handle
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    _mutex_handle = handle  # 保持引用以持有互斥量
    if not handle:
        return True  # 创建失败时不阻塞启动
    return kernel32.GetLastError() != _ERROR_ALREADY_EXISTS


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_FILE, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def main() -> int:
    setup_logging()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("识图翻译")
    app.setApplicationDisplayName("识图翻译")
    app.setWindowIcon(make_app_icon())

    # 单实例保护：避免多开导致全局快捷键互相抢占注册失败
    if not _acquire_single_instance():
        QMessageBox.information(
            None, "识图翻译",
            "程序已在运行中，请在系统托盘图标处查看（单击托盘可显示主界面）。",
        )
        return 0

    cfg = load_config()
    controller = Controller(cfg)
    controller.apply_theme()
    tray = controller.create_tray(app)
    if tray is None:
        QMessageBox.warning(
            None, "识图翻译",
            "无法创建系统托盘图标，程序仍将以后台方式运行（快捷键可用）。",
        )

    # 首次启动显示主界面；之后可通过点击托盘图标唤回
    controller.show_main()

    ret = app.exec()
    controller.shutdown()
    return ret


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(run_selftest())
    sys.exit(main())
