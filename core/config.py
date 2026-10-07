"""配置读写：默认配置 + JSON 持久化（保存在程序目录 config.json）。

默认全部走自建服务器 zndjs：
    OCR    https://www.zndjs.cn:8888/ocr         （token / method / image(base64)）
    翻译   http://www.zndjs.cn:9999/translate     （token / text / source / target）
"""
import copy
import json
import logging
import sys
from pathlib import Path

# PyInstaller 打包后 __file__ 指向临时解压目录，需改用 exe 所在目录
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

CONFIG_PATH = BASE_DIR / "config.json"

# ---- zndjs 默认服务器（设置里可一键恢复） ----
ZNDJS_HOST = "www.zndjs.cn"
DEFAULT_OCR_URL = f"https://{ZNDJS_HOST}:8888/ocr"
DEFAULT_TRANSLATE_URL = f"http://{ZNDJS_HOST}:9999/translate"
DEFAULT_TARGET_LANG = "English"      # 默认译成英文（源语言由服务端自动检测）

# 主界面「译成」下拉的可选项（名字都能在 TENCENT_LANG_CODES 里查到代码）
TARGET_LANG_CHOICES = [
    "English", "简体中文", "繁體中文", "日本語", "한국어",
    "Français", "Deutsch", "Español", "Italiano", "Русский", "Português",
    "越南语", "泰语", "马来语", "印尼语", "阿拉伯语", "印地语", "土耳其语",
]

# OCR 地址常用预设（下拉里直接选）
OCR_URL_PRESETS = [
    ("zndjs 8888（HTTPS，默认）", DEFAULT_OCR_URL),
    ("zndjs 9999（HTTP，同一套服务）", f"http://{ZNDJS_HOST}:9999/ocr"),
]

# 服务商取值：zndjs（自建 /translate）/ custom（OpenAI 兼容）/ google / off
PROVIDER_LABELS = {
    "zndjs": "zndjs 服务器（默认，/translate 接口）",
    "custom": "自定义服务器（OpenAI 兼容）",
    "google": "Google 翻译（免费）",
    "off": "关闭翻译",
}

# 语言显示名 → 服务端语种代码（腾讯翻译：ja/ko 可用，kor 不支持）
TENCENT_LANG_CODES = {
    "简体中文": "zh", "中文": "zh", "chinese": "zh", "zh": "zh", "zh-cn": "zh",
    "繁体中文": "zh-TW", "繁體中文": "zh-TW", "zh-tw": "zh-TW", "zh-hant": "zh-TW",
    "英语": "en", "英文": "en", "english": "en", "en": "en",
    "日本語": "ja", "日语": "ja", "日文": "ja", "japanese": "ja", "ja": "ja", "jp": "ja",
    "한국어": "ko", "韩语": "ko", "韩文": "ko", "korean": "ko", "ko": "ko", "kr": "ko",
    "法语": "fr", "français": "fr", "french": "fr", "fr": "fr",
    "德语": "de", "deutsch": "de", "german": "de", "de": "de",
    "西班牙语": "es", "español": "es", "spanish": "es", "es": "es",
    "意大利语": "it", "italiano": "it", "italian": "it", "it": "it",
    "俄语": "ru", "русский": "ru", "russian": "ru", "ru": "ru",
    "葡萄牙语": "pt", "português": "pt", "portuguese": "pt", "pt": "pt",
    "越南语": "vi", "vietnamese": "vi", "vi": "vi",
    "泰语": "th", "thai": "th", "th": "th",
    "马来语": "ms", "malay": "ms", "ms": "ms",
    "印尼语": "id", "indonesian": "id", "id": "id",
    "阿拉伯语": "ar", "arabic": "ar", "ar": "ar",
    "印地语": "hi", "hindi": "hi", "hi": "hi",
    "土耳其语": "tr", "turkish": "tr", "tr": "tr",
}

ZNDJS_SOURCE_CHOICES = [
    ("自动检测 (auto)", "auto"),
    ("中文 (zh)", "zh"),
    ("繁体中文 (zh-TW)", "zh-TW"),
    ("英语 (en)", "en"),
    ("日语 (ja)", "ja"),
    ("韩语 (ko)", "ko"),
    ("俄语 (ru)", "ru"),
    ("法语 (fr)", "fr"),
    ("德语 (de)", "de"),
    ("西班牙语 (es)", "es"),
    ("葡萄牙语 (pt)", "pt"),
    ("越南语 (vi)", "vi"),
    ("泰语 (th)", "th"),
    ("阿拉伯语 (ar)", "ar"),
    ("土耳其语 (tr)", "tr"),
]


def lang_to_code(lang: str) -> str:
    """把界面上的语言名（简体中文 / English …）映射成服务端语种代码。"""
    return TENCENT_LANG_CODES.get(str(lang or "").strip().lower(), "")


DEFAULT_CONFIG = {
    "hotkeys": {
        "capture_ocr": "ctrl+alt+o",   # 截图 -> 识别 -> 翻译
        "capture_tts": "ctrl+alt+s",   # 截图 -> 识别 -> 直接朗读原文
    },
    "ocr": {
        "url": DEFAULT_OCR_URL,
        "token": "asdasdasd",
        "method": 2,
        "timeout": 30,
        "upscale": 1.0,                # 识别前放大倍数，小字可调大
        "pdf_dpi": 200,                # PDF 渲染清晰度（越大越清晰、越慢）
    },
    "translate": {
        "provider": "zndjs",           # zndjs / custom / google / off
        # --- zndjs 自建 /translate 接口 ---
        "zndjs_url": DEFAULT_TRANSLATE_URL,
        "zndjs_source": "auto",        # auto 由服务端检测；短句误判时可固定成 en/zh 等
        "zndjs_target": "",            # 空 = 按下面的「目标语言」自动映射
        # --- 自定义 OpenAI 兼容服务 ---
        "openai_base_url": "",
        "openai_api_key": "",
        "openai_model": "gpt-4o-mini",
        # --- 通用 ---
        "auto_translate": False,       # 识别后自动翻译（默认关闭，避免白耗翻译额度；界面上的勾选会记在这里）
        "target_lang": DEFAULT_TARGET_LANG,
        "system_prompt": (
            "你是一个专业翻译引擎。将用户提供的文本翻译成{target_lang}。"
            "只输出译文本身，不要任何解释、注释或原文。"
            "保留原文的换行格式。如果文本已经是目标语言，原样输出。"
        ),
        "google_target_code": "zh-CN",
    },
    "tts": {
        "engine": "edge",              # edge / sapi / off
        "voice": "auto",               # auto = 按文本语言自动选择
        "rate": "+0%",
        "volume": "+0%",
        "auto_read": "off",            # off / original / translation
        "sapi_voice": "",
    },
    "ui": {
        "theme": "dark",               # dark / light
    },
}



def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config() -> dict:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                cfg = _deep_merge(cfg, saved)
        except Exception:
            logging.exception("读取配置失败，使用默认配置")
    return cfg


def save_config(cfg: dict) -> None:
    try:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        logging.exception("写入配置失败")


def resolve_translate_cfg(cfg: dict) -> dict:
    """返回用于翻译的配置副本（补齐默认地址 / 语种代码 / 共用 Key）。不修改传入的 cfg。

    - provider = zndjs ：走自建 /translate 接口，地址、源语言、目标语种都在这里补默认值，
      Key 未单独填时复用「文字识别」的 token。
    - provider = custom / openai ：走 OpenAI 兼容协议，同样复用 token。
    - provider = google ：免费接口，无需 Key。
    """
    tr = copy.deepcopy(cfg.get("translate", {}) or {})
    provider = str(tr.get("provider", "off")).lower()
    ocr_token = str((cfg.get("ocr", {}) or {}).get("token", "")).strip()

    if provider == "zndjs":
        if not str(tr.get("zndjs_url", "")).strip():
            tr["zndjs_url"] = DEFAULT_TRANSLATE_URL
        if not str(tr.get("zndjs_source", "")).strip():
            tr["zndjs_source"] = "auto"
        if not str(tr.get("zndjs_target", "")).strip():
            tr["zndjs_target"] = lang_to_code(tr.get("target_lang", "")) or "zh"
        if not str(tr.get("token", "")).strip():
            tr["token"] = ocr_token
    elif provider in ("custom", "openai"):
        if not str(tr.get("openai_api_key", "")).strip() and ocr_token:
            tr["openai_api_key"] = ocr_token
    return tr


def resolve_ocr_cfg(cfg: dict) -> dict:
    """返回用于识别的配置副本（接口地址为空时回退到 zndjs 默认地址）。"""
    ocr = copy.deepcopy(cfg.get("ocr", {}) or {})
    if not str(ocr.get("url", "")).strip():
        ocr["url"] = DEFAULT_OCR_URL
    return ocr
