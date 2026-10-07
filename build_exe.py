"""一键打包 exe（推荐用这个，而不是直接跑 PyInstaller）。

为什么要用它：
    本机 Python 3.10.0 的 `dis` 模块有已知 bug（`_get_const_info` 越界，
    `IndexError: tuple index out of range`）。PyInstaller 分析字节码时会对每个
    模块调用 `dis.get_instructions`，一旦某个模块（如 pymupdf/__init__.py、
    PIL/Image.py）的字节码命中这个 bug，整个打包就崩了。

    这里在调用 PyInstaller 之前给 `dis.get_instructions` 打一个容错补丁：
    某个 code object 扫到一半出错时，只跳过它剩余的指令（最坏情况是漏掉那里面
    的若干可选依赖，例如惰性 import 的 PIL），而不是让整个打包失败。

用法：
    python build_exe.py                 # 用 识图翻译.spec 打包到 dist/
    python build_exe.py --clean         # 先清掉 build/ 再打包
"""
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = "识图翻译.spec"

_orig_get_instructions = __import__("dis").get_instructions


def _patched_get_instructions(code_object, *args, **kwargs):
    """容错版：出错就停止该 code object 的扫描，不影响其它模块。"""
    try:
        yield from _orig_get_instructions(code_object, *args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        print(
            f"[build] 跳过无法反汇编的代码对象（本机 dis bug）："
            f"{getattr(code_object, 'co_filename', '?')} :: "
            f"{getattr(code_object, 'co_name', '?')} ({type(exc).__name__})"
        )
        return


def main() -> int:
    import dis

    dis.get_instructions = _patched_get_instructions

    os.chdir(ROOT)
    argv = list(sys.argv[1:])
    if "--clean" in argv:
        argv.remove("--clean")
        shutil.rmtree(ROOT / "build", ignore_errors=True)
        print("[build] 已清空 build/")

    from PyInstaller.__main__ import run

    args = [SPEC, "--noconfirm", "--distpath", "dist", "--workpath", "build", *argv]
    print("[build] PyInstaller", " ".join(args))
    try:
        run(args)
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
