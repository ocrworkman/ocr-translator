# 识图翻译（截图 OCR 与翻译工具）

[![Release v1.0.0](https://img.shields.io/badge/release-v1.0.0-blue)](https://github.com/ocrworkman/ocr-translator/releases/tag/v1.0.0)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](requirements.txt)

常驻系统托盘，可截图框选、也可**批量导入图片 / PDF**，完成：

- **文字识别（OCR）**：默认调用自建服务器 `https://www.zndjs.cn:8888/ocr`（9999 端口的 HTTP 版本是同一套服务），
  请求体 `{"token", "method", "image(base64)"}`，关闭证书校验。**服务器地址可在设置里随意更换。**

- **翻译**：默认走自建服务器的 `/translate` 简易接口
  （`http://www.zndjs.cn:9999/translate`，POST `{token, text, source, target}` → `{result: 译文}`），
  也可切换为「自定义服务器」（OpenAI / DeepSeek / 智谱 / Ollama 等任意兼容地址）或 Google 免费接口。
  翻译与识别共用一个 Token（在「文字识别」里填）。
- **语音合成（TTS）**：Edge-TTS 在线语音（微软，音质好）或 Windows 本地 SAPI（离线）。
- **逐条朗读**：列表里每一条任务的原文、译文都能单独朗读，朗读时按钮会变成「朗读中…」。

## ⬇️ 下载

**Windows 用户**：前往 [Releases 页面](https://github.com/ocrworkman/ocr-translator/releases) 下载单文件 exe，
**无需安装 Python**，双击即可运行。

| 版本 | 文件 | 大小 | 说明 |
| --- | --- | --- | --- |
| v1.0.0 | [OCRTranslator-v1.0.0-Windows-x64.exe](https://github.com/ocrworkman/ocr-translator/releases/download/v1.0.0/OCRTranslator-v1.0.0-Windows-x64.exe) | 77.2 MB | Windows 10/11 64 位 |

<details>
<summary><b>校验文件完整性（可选）</b></summary>

下载后可用 PowerShell 核对哈希值，确认文件未被篡改或下载损坏：

```powershell
Get-FileHash -Algorithm SHA256 ".\OCRTranslator-v1.0.0-Windows-x64.exe"
```

v1.0.0 的期望值：

```
SHA256: e55e5b2fdc8220ed689a7990de0b3ac1cf21a4be173350f1c8f6bac95a63281f
大小  : 80957545 字节
```

</details>

> ⚠️ 首次启动需解压到临时目录，稍慢属正常现象。Windows Defender 等杀毒软件可能误报单文件 exe，
> 可按需将文件加入信任名单。程序**默认配置为占位值**，使用前请在设置里填入你可用的 OCR 与翻译服务。

## 安装与运行（源码方式）

```bat
pip install -r requirements.txt
python main.py        :: 调试运行（带控制台日志）
run.bat               :: 日常运行（无窗口）
python main.py --selftest   :: 自检（不弹窗），结果写入 selftest.log
```

## 自检（排查环境 / 打包问题）

```bat
python main.py --selftest
"dist\识图翻译.exe" --selftest
```

会在程序目录生成 `selftest.log`，逐项报告配置读取、PDF 后端、语音组件、图片渲染、
主界面与设置窗口能否正常构建，以及一句话 OCR 连通性结论。出问题时先跑这个。

## 三种识别入口

| 入口 | 说明 |
| --- | --- |
| 框选截图 | 全局快捷键或主界面「截图识别」，识别后并入任务列表 |
| 导入图片 / PDF | 主界面「导入图片 / PDF」按钮，**可多选**；也可直接把文件/文件夹**拖进窗口** |
| 托盘菜单 | 托盘右键 → 导入图片 / PDF 识别… |

- 支持的图片格式：png / jpg / jpeg / bmp / webp / tif / tiff / gif
- PDF 会**逐页**渲染成图片后分别识别（清晰度由设置里的「PDF 渲染清晰度」控制，默认 200 dpi；渲染用 `pypdfium2`）
- 图片识别前可按设置里的「识别前放大倍数」放大，小字建议调到 1.5～2

## 默认快捷键（可在设置中自定义）

| 快捷键 | 功能 |
| --- | --- |
| `Ctrl+Alt+O` | 框选屏幕区域 → OCR，**是否翻译按主界面「识别后自动翻译」的勾选**（默认关闭） |
| `Ctrl+Alt+S` | 框选屏幕区域 → OCR → 直接朗读原文（不翻译） |

框选时：按住左键拖拽，`Esc` 或右键取消。

## 任务列表

左侧列表展示所有任务，每条前面的标记表示状态：`·` 待识别、`…` 处理中、`✔` 完成、`⚠` 失败。

- **识别后自动翻译**（复选框）：**默认关闭**，避免白耗翻译额度。勾选状态会记进 `config.json`，
  下次启动保持；改一下勾选就生效，不会再被截图操作重置。
- **译成**（译文标题旁的下拉）：**目标语言就在这里选**（默认英文），改完立刻保存。
  已有原文可点「整批翻译」或单条「重新翻译」生效。
- 单击任意一条 → 右侧显示该条预览图 + 原文 / 译文
- **朗读原文 / 朗读译文**：朗读当前这条
- **重新翻译**：用当前原文（含手动修改）重译这一条
- **整批翻译**：把所有已识别但没翻译的原文一次性翻译
- **导出结果**：把全部原文 / 译文导出成 txt
- **清空列表**：清空任务

## 设置（托盘右键 → 设置…）

- **快捷键**：点击按钮后直接按下组合键（必须含 Ctrl/Alt/Shift/Win 修饰键）
- **文字识别**：
  - **服务器地址**：默认 `https://www.zndjs.cn:8888/ocr`，可改成任意兼容接口；旁边有「恢复默认」
  - Key（识别与翻译共用）、识别方式 method、超时、识别前放大倍数、PDF 渲染清晰度
  - 「测试识别」按钮可直接验证地址 / Key 是否可用
- **翻译**：
  - **目标语言在主界面「译文」上方的「译成」下拉里选**（默认英文，18 种语言），这里只显示当前值
  - 服务商：`zndjs 服务器（默认，/translate 接口）` / `自定义服务器（OpenAI 兼容）` / `Google 翻译（免费）` / `关闭翻译`
  - **zndjs 页**：服务器地址（默认 `http://www.zndjs.cn:9999/translate`，可「恢复默认」）、
    **源语言**（默认自动检测）、目标语种代码（按主界面选的语言自动换算）。
    语种代码用腾讯翻译的写法：日语 `ja`、韩语 `ko`（`kor` 不支持）、繁体 `zh-TW`
  - **自定义服务器页**：OpenAI 兼容地址、模型、系统提示词（`{target_lang}` 会被替换为目标语言）
  - 「测试翻译」按钮可直接验证配置是否可用
- **语音合成**：引擎（Edge 在线 / Windows 本地 / 关闭）、音色、语速、音量、识别完成后自动朗读（原文/译文）
- **外观**：深色 / 浅色

> **自动检测源语言的坑**：服务端自动检测对短句容易误判（例如把 `Hello` 当成意大利语，
> 报 `不支持的语种：it_to_ja`）。程序命中这个错误时会按文本脚本自行判断语种并**自动重试一次**；
> 若仍失败，把「源语言」从 `auto` 固定成实际语种即可。


配置保存在程序目录的 `config.json`（含 Token / API Key，注意不要泄露）。

## 配置文件示例

```json
{
  "hotkeys": { "capture_ocr": "ctrl+alt+o", "capture_tts": "ctrl+alt+s" },
  "ocr": {
    "url": "https://www.zndjs.cn:8888/ocr",
    "token": "",
    "method": 2,
    "timeout": 30,
    "upscale": 1.0,
    "pdf_dpi": 200
  },
  "translate": {
    "provider": "zndjs",
    "zndjs_url": "http://www.zndjs.cn:9999/translate",
    "zndjs_source": "auto",
    "zndjs_target": "",
    "openai_base_url": "",
    "openai_api_key": "",
    "openai_model": "gpt-4o-mini",
    "auto_translate": false,
    "target_lang": "English"
  },
  "tts": { "engine": "edge", "voice": "auto" },
  "ui": { "theme": "light" }
}
```

## 说明

- 翻译走自建 `/translate`：请求 `{"token", "text", "source", "target"}`，
  返回 `{"result": 译文, ...}`；服务端把业务错误（`已超期或者次数不够` / `token不存在` /
  `翻译失败: 不支持的语种：xx_to_yy`）也当正常结果返回，客户端会识别成错误并给出中文提示。
- OCR 返回格式做了兼容解析：
  - PaddleOCR 风格 `[[[坐标],["文字", 置信度]], ...]` → 按阅读顺序还原换行，中英之间自动补空格
  - 百度风格 `{"ParsedResults":[{"ParsedText":"..."}]}`
  - 通用字段 `text / result / data / content` 等
  - 都解析不出来时会显示原始 JSON，方便排查接口变化
- Google 免费翻译接口需要能访问 Google；无 Key、有限流风险。
- 运行日志写在 `error.log`。

## 打包为单个 exe（产物在 `dist\识图翻译.exe`）

> 💡 二进制产物**不进 git 仓库**，统一通过 [Releases](https://github.com/ocrworkman/ocr-translator/releases)
> 分发——避免仓库膨胀、拖慢他人 clone。发版时用 tag 锁定对应代码版本，便于追溯。

**推荐**（自带 `dis` bug 兼容补丁，不会崩）：

```bat
python build_exe.py            :: 打包到 dist\
python build_exe.py --clean    :: 先清空 build\ 再打包
```

也可以直接用 spec（本机 Python 3.10.0 上可能踩到 dis bug，见下）：

```bat
python -m PyInstaller 识图翻译.spec --noconfirm
```

手工命令等价形式：

```bat
pip install pyinstaller
python -m PyInstaller --noconsole --onefile --name "识图翻译" --icon app_icon.ico ^
  --hidden-import pyttsx3.drivers --hidden-import pyttsx3.drivers.sapi5 ^
  --collect-all comtypes --collect-all pypdfium2 --collect-all pypdfium2_raw ^
  --exclude-module rich --exclude-module pydantic --exclude-module pydantic_core ^
  --exclude-module typer --exclude-module pygame ^
  --exclude-module pymupdf --exclude-module fitz ^
  --exclude-module PIL --exclude-module Pillow main.py
```

说明：
- **关于 `dis` bug**：本机 Python 3.10.0 的 `dis._get_const_info` 会越界抛
  `IndexError: tuple index out of range`。PyInstaller 分析字节码时会对每个模块调用
  `dis.get_instructions`，命中就整个打包失败。已实测会踩到的模块：`pymupdf/__init__.py`（Document 类）、
  `PIL/Image.py`。对策有两条，建议都用：
  1. 用 `build_exe.py` 打包 —— 它给 `dis.get_instructions` 打了容错补丁，只跳过出错的代码对象；
  2. spec 里把 `pymupdf/fitz`（改用 `pypdfium2` 做 PDF 渲染）与 `PIL/Pillow`（pypdfium2 只是惰性引用）排除。
- 打包后的 exe 会把 `config.json` / `error.log` 写在自己所在目录（已做 frozen 路径适配）。
- 单文件 exe 首次启动需解压到临时目录，稍慢属正常；如被杀毒软件误报，可加入信任或改用 `--onedir` 方式。


