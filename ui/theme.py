"""主题样式（深色 / 浅色）与应用图标。"""
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

# ---------- 深色 ----------
DARK_QSS = """
QWidget {
    background-color: #1e1f24;
    color: #e8eaf0;
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}
QDialog, QMainWindow, QWidget#MainWindow { background-color: #1e1f24; }
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #2a2c33;
    border: 1px solid #3a3d47;
    border-radius: 6px;
    padding: 6px 9px;
    selection-background-color: #2563eb;
}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: #2563eb;
}
QPlainTextEdit {
    font-family: Consolas, "Microsoft YaHei UI", monospace;
    font-size: 14px;
}
QPushButton {
    background-color: #2a2c33;
    color: #e8eaf0;
    border: 1px solid #3a3d47;
    border-radius: 6px;
    padding: 7px 14px;
}
QPushButton:hover { background-color: #343743; border-color: #4a4f5e; }
QPushButton:pressed { background-color: #23252c; }
QPushButton:disabled { color: #6b7280; }
QPushButton[accent="true"] {
    background-color: #2563eb; color: #ffffff; border: 1px solid #2563eb; font-weight: 600;
}
QPushButton[accent="true"]:hover { background-color: #3b74f0; }
QPushButton[accent="true"]:pressed { background-color: #1d4fd0; }
QTabWidget::pane { border: 1px solid #3a3d47; border-radius: 6px; }
QTabBar::tab {
    background: #26282f;
    color: #b6bcc8;
    padding: 9px 20px;
    margin-right: 4px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}
QTabBar::tab:selected { background: #2563eb; color: #ffffff; }
QComboBox QAbstractItemView {
    background-color: #2a2c33;
    border: 1px solid #3a3d47;
    selection-background-color: #2563eb;
}
QGroupBox {
    border: 1px solid #3a3d47;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 8px;
    font-weight: 600;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #b6bcc8; }
QLabel#hintLabel { color: #9aa0ae; font-size: 12px; }
QLabel#fieldLabel { color: #c3c9d4; font-size: 12px; font-weight: 600; }
QLabel#winTitle { font-size: 16px; font-weight: 600; }
QLabel#statusLabel { color: #9aa0ae; font-size: 12px; }
QLabel#previewBox {
    background-color: #16171b;
    border: 1px dashed #3a3d47;
    border-radius: 8px;
    color: #6b7280;
}
QSplitter::handle { background: #2a2c33; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #3a3d47; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #4a4f5e; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #3a3d47; border-radius: 5px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QToolTip { background-color: #2a2c33; color: #e8eaf0; border: 1px solid #3a3d47; padding: 4px; }
QStatusBar { background-color: #1a1b1f; color: #9aa0ae; }
QPushButton#closeBtn { border: none; background: transparent; font-size: 16px; }
QPushButton#closeBtn:hover { background: #ef4444; color: #ffffff; }
QMessageBox { background-color: #1e1f24; }
QMenu { background-color: #26282f; border: 1px solid #3a3d47; padding: 6px; }
QMenu::item { padding: 6px 24px; border-radius: 4px; }
QMenu::item:selected { background-color: #2563eb; color: #ffffff; }
QListWidget {
    background-color: #26282f;
    border: 1px solid #3a3d47;
    border-radius: 6px;
    padding: 4px;
    outline: none;
}
QListWidget::item { padding: 6px 8px; border-radius: 4px; color: #c3c9d4; }
QListWidget::item:hover { background-color: #32353f; }
QListWidget::item:selected { background-color: #2563eb; color: #ffffff; }
QProgressBar {
    background-color: #2a2c33;
    border: 1px solid #3a3d47;
    border-radius: 6px;
    min-height: 16px;
    max-height: 16px;
    text-align: center;
    color: #c3c9d4;
    font-size: 11px;
}
QProgressBar::chunk { background-color: #2563eb; border-radius: 5px; }
QCheckBox { spacing: 6px; }
QCheckBox::indicator {
    width: 15px; height: 15px; border-radius: 3px;
    border: 1px solid #3a3d47; background-color: #2a2c33;
}
QCheckBox::indicator:checked { background-color: #2563eb; border-color: #2563eb; }
"""

# ---------- 浅色 ----------
LIGHT_QSS = """
QWidget {
    background-color: #f5f6f8;
    color: #1f2328;
    font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
    font-size: 13px;
}
QDialog, QMainWindow, QWidget#MainWindow { background-color: #f5f6f8; }
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background-color: #ffffff;
    border: 1px solid #d0d7de;
    border-radius: 6px;
    padding: 6px 9px;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
    border-color: #2563eb;
}
QPlainTextEdit {
    font-family: Consolas, "Microsoft YaHei UI", monospace;
    font-size: 14px;
}
QPushButton {
    background-color: #ffffff;
    color: #1f2328;
    border: 1px solid #d0d7de;
    border-radius: 6px;
    padding: 7px 14px;
}
QPushButton:hover { background-color: #eef1f5; border-color: #b6bcc8; }
QPushButton:pressed { background-color: #e2e6ec; }
QPushButton:disabled { color: #9aa0ae; }
QPushButton[accent="true"] {
    background-color: #2563eb; color: #ffffff; border: 1px solid #2563eb; font-weight: 600;
}
QPushButton[accent="true"]:hover { background-color: #3b74f0; }
QPushButton[accent="true"]:pressed { background-color: #1d4fd0; }
QTabWidget::pane { border: 1px solid #d0d7de; border-radius: 6px; background: #ffffff; }
QTabBar::tab {
    background: #e9edf2;
    color: #57606a;
    padding: 9px 20px;
    margin-right: 4px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
}
QTabBar::tab:selected { background: #2563eb; color: #ffffff; }
QComboBox QAbstractItemView {
    background-color: #ffffff;
    border: 1px solid #d0d7de;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
}
QGroupBox {
    border: 1px solid #d0d7de;
    border-radius: 8px;
    margin-top: 12px;
    padding-top: 8px;
    font-weight: 600;
    background: #ffffff;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px; color: #57606a; }
QLabel#hintLabel { color: #6a737d; font-size: 12px; }
QLabel#fieldLabel { color: #3a4149; font-size: 12px; font-weight: 600; }
QLabel#winTitle { font-size: 16px; font-weight: 600; }
QLabel#statusLabel { color: #6a737d; font-size: 12px; }
QLabel#previewBox {
    background-color: #ffffff;
    border: 1px dashed #c3c9d4;
    border-radius: 8px;
    color: #9aa0ae;
}
QSplitter::handle { background: #e2e6ec; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #c3c9d4; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #aeb4bf; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #c3c9d4; border-radius: 5px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QToolTip { background-color: #ffffff; color: #1f2328; border: 1px solid #d0d7de; padding: 4px; }
QStatusBar { background-color: #eceff3; color: #6a737d; }
QPushButton#closeBtn { border: none; background: transparent; font-size: 16px; }
QPushButton#closeBtn:hover { background: #ef4444; color: #ffffff; }
QMessageBox { background-color: #f5f6f8; }
QMenu { background-color: #ffffff; border: 1px solid #d0d7de; padding: 6px; }
QMenu::item { padding: 6px 24px; border-radius: 4px; }
QMenu::item:selected { background-color: #2563eb; color: #ffffff; }
QListWidget {
    background-color: #ffffff;
    border: 1px solid #d0d7de;
    border-radius: 6px;
    padding: 4px;
    outline: none;
}
QListWidget::item { padding: 6px 8px; border-radius: 4px; color: #3a4149; }
QListWidget::item:hover { background-color: #eef1f5; }
QListWidget::item:selected { background-color: #2563eb; color: #ffffff; }
QProgressBar {
    background-color: #ffffff;
    border: 1px solid #d0d7de;
    border-radius: 6px;
    min-height: 16px;
    max-height: 16px;
    text-align: center;
    color: #3a4149;
    font-size: 11px;
}
QProgressBar::chunk { background-color: #2563eb; border-radius: 5px; }
QCheckBox { spacing: 6px; }
QCheckBox::indicator {
    width: 15px; height: 15px; border-radius: 3px;
    border: 1px solid #c3c9d4; background-color: #ffffff;
}
QCheckBox::indicator:checked { background-color: #2563eb; border-color: #2563eb; }
"""

# 向后兼容：APP_QSS 仍指深色
APP_QSS = DARK_QSS

_THEMES = {"dark": DARK_QSS, "light": LIGHT_QSS}


def get_qss(theme: str) -> str:
    return _THEMES.get(str(theme or "dark").lower(), DARK_QSS)


def make_app_icon(text: str = "译") -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#2563EB"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 16, 16)
    font = QFont("Microsoft YaHei", 32, QFont.Weight.Black)
    p.setFont(font)
    p.setPen(QColor("white"))
    p.drawText(QRect(0, 0, 64, 64), Qt.AlignmentFlag.AlignCenter, text)
    p.end()
    return QIcon(pm)
