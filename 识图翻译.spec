# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = []
binaries = []
hiddenimports = ['pyttsx3.drivers', 'pyttsx3.drivers.sapi5']
tmp_ret = collect_all('comtypes')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# PDF 逐页渲染（导入图片 / PDF 用）
# 用 pypdfium2（纯二进制，打包无坑）；不用 pymupdf —— 本机 Python 3.10.0 的 dis
# 模块有 bug，PyInstaller 分析 pymupdf/__init__.py 时会在 dis.get_instructions 崩溃。
for pkg in ('pypdfium2', 'pypdfium2_raw'):
    try:
        tmp_ret = collect_all(pkg)
        datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
    except Exception:
        hiddenimports.append(pkg)


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'rich', 'pydantic', 'pydantic_core', 'typer', 'pygame',
        'pymupdf', 'fitz',            # 会让本机 Python 3.10.0 的 dis 崩溃
        'PIL', 'Pillow',              # pypdfium2 只是惰性引用，运行时不使用
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='识图翻译',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app_icon.ico'],
)
