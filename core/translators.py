"""翻译服务。

三种服务商：
    zndjs  —— 自建 `POST /translate` 简易接口：{token, text, source, target} -> {result: 译文}
    custom —— 任意 OpenAI 兼容服务（Base URL + Key，走 /chat/completions）
    google —— Google 免费接口
"""
import json
import logging
import re

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 自建服务器用自签证书，对这些主机跳过证书校验（与 OCR 调用保持一致）
_NO_VERIFY_HOSTS = ("zndjs.cn", "localhost", "127.0.0.1")

# 服务端把业务错误也当正常结果返回，这些是它的固定文案
_ZNDJS_ERROR_TEXTS = (
    "翻译文本不能为空", "参数错误", "已超期或者次数不够", "方法错误", "token不存在",
)

DEFAULT_TRANSLATE_URL = "http://www.zndjs.cn:9999/translate"

# 目标语言名称 -> Google 接口语言代码
GOOGLE_LANG_CODES = {
    "简体中文": "zh-CN", "繁体中文": "zh-TW", "繁體中文": "zh-TW", "中文": "zh-CN",
    "英语": "en", "英文": "en", "english": "en", "en": "en",
    "日语": "ja", "日文": "ja", "japanese": "ja", "ja": "ja", "日本語": "ja",
    "韩语": "ko", "韩文": "ko", "korean": "ko", "ko": "ko", "한국어": "ko",
    "法语": "fr", "francais": "fr", "français": "fr",
    "德语": "de", "deutsch": "de",
    "俄语": "ru", "русский": "ru",
    "西班牙语": "es", "español": "es",
    "葡萄牙语": "pt", "italiano": "it", "意大利语": "it",
    "越南语": "vi", "vietnamese": "vi",
    "泰语": "th", "thai": "th",
    "马来语": "ms", "malay": "ms",
    "印尼语": "id", "indonesian": "id",
    "阿拉伯语": "ar", "arabic": "ar",
    "印地语": "hi", "hindi": "hi",
    "土耳其语": "tr", "turkish": "tr",
}


class TranslationError(RuntimeError):
    pass


def _verify_for(url: str) -> bool:
    host = ""
    try:
        host = (requests.utils.urlparse(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        pass
    return not any(h in host for h in _NO_VERIFY_HOSTS)


def translate_text(text: str, cfg: dict) -> str:
    provider = str(cfg.get("provider", "off")).lower()
    text = text or ""
    if provider == "off":
        raise TranslationError("翻译服务已关闭：托盘右键 → 设置 → 翻译 可选择翻译服务")
    if not text.strip():
        raise TranslationError("没有可翻译的文本")
    if provider == "google":
        return _google_free(text, cfg)
    if provider == "zndjs":
        return _zndjs_translate(text, cfg)
    return _openai_compatible(text, cfg)


# ---------------- zndjs 自建 /translate ----------------

def _zndjs_translate(text: str, cfg: dict) -> str:
    url = str(cfg.get("zndjs_url", "")).strip() or DEFAULT_TRANSLATE_URL
    token = str(cfg.get("token", "")).strip()
    source = str(cfg.get("zndjs_source", "") or "auto").strip() or "auto"
    target = str(cfg.get("zndjs_target", "")).strip()
    if not target:
        from core.config import lang_to_code

        target = lang_to_code(cfg.get("target_lang", "")) or "zh"
    if not token:
        raise TranslationError(
            "尚未配置翻译 Key：请到 设置 → 文字识别 填写 Token（识别与翻译共用一个 Key）"
        )

    timeout = _safe_timeout(cfg)
    result, raw = _zndjs_call(url, token, text, source, target, timeout)

    # 服务端的自动语种检测对短句容易误判（例如把 "Hello" 当成意大利语），
    # 命中"不支持的语种"时，按文本脚本自行判断语种重试一次。
    if _is_unsupported_pair(result) and source == "auto":
        guess = guess_lang_code(text)
        if guess and guess != "auto":
            logging.info("zndjs 翻译自动检测有误（%s），改用 source=%s 重试", raw, guess)
            result, raw = _zndjs_call(url, token, text, guess, target, timeout)

    return result


def _zndjs_call(url, token, text, source, target, timeout):
    """调用一次 /translate，返回 (译文, 原始响应)。业务错误抛 TranslationError。"""
    payload = {"token": token, "text": text, "source": source, "target": target}
    try:
        resp = requests.post(url, json=payload, timeout=timeout, verify=_verify_for(url))
    except requests.RequestException as exc:
        raise TranslationError(f"连接翻译接口失败: {exc}") from exc
    if resp.status_code >= 400:
        raise TranslationError(
            f"翻译接口返回 HTTP {resp.status_code}：{(resp.text or '').strip()[:200]}"
        )
    body = (resp.text or "").strip()
    if not body:
        raise TranslationError("翻译接口返回为空")
    try:
        data = resp.json()
    except ValueError:
        raise TranslationError(f"翻译接口返回的不是 JSON：{body[:200]}") from None
    if not isinstance(data, dict):
        raise TranslationError(f"翻译接口返回格式异常：{str(data)[:200]}")

    result = str(data.get("result", "") or "").strip()
    # 成功响应一定带这些字段（见服务端代码）；只有 result 的是错误文案
    ok = any(k in data for k in ("source_text", "char_count", "deduct_count"))
    if ok:
        if not result:
            raise TranslationError("翻译接口未返回译文")
        if "Balance" in data:
            logging.info("zndjs 翻译成功，剩余额度 %s", data.get("Balance"))
        return result, data

    if result in _ZNDJS_ERROR_TEXTS:
        raise TranslationError(_friendly_zndjs_error(result))
    if result.startswith("翻译失败"):
        raise TranslationError(
            f"{result}　（可在 设置 → 翻译 把「源语言」固定成实际语种再试）"
        )
    raise TranslationError(
        f"翻译失败：{result or json.dumps(data, ensure_ascii=False)[:200]}"
    )


def _friendly_zndjs_error(text: str) -> str:
    if "超期" in text or "次数" in text:
        return (
            f"翻译服务返回：{text}（Key 已超期 / 次数用尽）"
            "　请到服务商后台续期或充值，或在 设置 → 文字识别 换一个有效 Key"
        )
    if "token" in text.lower():
        return f"翻译服务返回：{text}　请检查 设置 → 文字识别 里的 Key 是否正确"
    return f"翻译服务返回：{text}"


def _is_unsupported_pair(result) -> bool:
    return isinstance(result, str) and "不支持的语种" in result


def guess_lang_code(text: str) -> str:
    """按字符脚本粗略判断语种代码（自动检测失败时兜底用）。"""
    t = text or ""
    if re.search(r"[\u3040-\u30ff]", t):
        return "ja"
    if re.search(r"[\uac00-\ud7af]", t):
        return "ko"
    if re.search(r"[\u4e00-\u9fff]", t):
        return "zh"
    if re.search(r"[\u0400-\u04ff]", t):
        return "ru"
    if re.search(r"[A-Za-z]", t):
        return "en"
    return ""


def _safe_timeout(cfg) -> float:
    try:
        return max(5.0, float(cfg.get("zndjs_timeout", 30) or 30))
    except (TypeError, ValueError):
        return 30.0


# ---------------- OpenAI 兼容 ----------------

def _openai_compatible(text: str, cfg: dict) -> str:
    base = str(cfg.get("openai_base_url", "")).strip().rstrip("/")
    key = str(cfg.get("openai_api_key", "")).strip()
    model = str(cfg.get("openai_model", "")).strip() or "gpt-4o-mini"
    if not base:
        raise TranslationError(
            "尚未配置翻译接口地址：设置 → 翻译 选「自定义服务器」后填写地址"
            "（如 https://api.deepseek.com/v1 或 https://api.openai.com/v1）"
        )
    if not key:
        raise TranslationError(
            "尚未配置翻译密钥：请到 设置 → 文字识别 填写 Token"
            "（识别与翻译共用同一个 Token），或在 设置 → 翻译 里单独填写 API Key"
        )

    target = str(cfg.get("target_lang", "")).strip() or "简体中文"
    system_prompt = str(cfg.get("system_prompt") or "").replace("{target_lang}", target)
    if not system_prompt.strip():
        system_prompt = f"你是翻译引擎，把文本翻译成{target}，只输出译文。"

    payload = {
        "model": model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": text},
        ],
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    endpoint = f"{base}/chat/completions"
    try:
        resp = requests.post(
            endpoint, json=payload, headers=headers, timeout=60,
            verify=_verify_for(endpoint),
        )
    except requests.RequestException as exc:
        raise TranslationError(f"请求翻译接口失败: {exc}") from exc
    if resp.status_code >= 400:
        raise TranslationError(f"接口返回 {resp.status_code}: {resp.text[:300]}")
    try:
        data = resp.json()
        return (data["choices"][0]["message"]["content"] or "").strip()
    except Exception as exc:  # noqa: BLE001
        raise TranslationError(
            "无法解析翻译接口返回: " + json.dumps(data, ensure_ascii=False)[:300]
        ) from exc


# ---------------- Google 免费 ----------------

def _google_free(text: str, cfg: dict) -> str:
    target = str(cfg.get("target_lang", "")).strip().lower()
    code = GOOGLE_LANG_CODES.get(target) or str(cfg.get("google_target_code", "zh-CN") or "zh-CN")
    params = {"client": "gtx", "sl": "auto", "tl": code, "dt": "t", "q": text}
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        resp = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params=params, headers=headers, timeout=15,
        )
    except requests.RequestException as exc:
        raise TranslationError(f"请求 Google 翻译失败: {exc}") from exc
    resp.raise_for_status()
    try:
        data = resp.json()
        segments = data[0] if data and isinstance(data[0], list) else []
        out = "".join(seg[0] for seg in segments if seg and isinstance(seg[0], str))
    except Exception as exc:  # noqa: BLE001
        raise TranslationError("Google 翻译返回格式异常") from exc
    if not out.strip():
        raise TranslationError("Google 翻译返回为空")
    return out.strip()
