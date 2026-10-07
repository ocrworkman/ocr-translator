"""调用自定义 OCR 接口（默认 https://www.zndjs.cn:8888/ocr）。

请求格式（与服务端调用代码一致）：
    POST JSON {"token": ..., "method": 2, "image": "<base64>"}
    verify=False（自签证书，跳过校验）

返回结构兼容解析：
    1) PaddleOCR 风格   [[[[x,y],...],["文字", 0.99]], ...]   —— 按阅读顺序还原换行
    2) 百度风格         {"ParsedResults":[{"ParsedText":"..."}]}
    3) 通用字段         text / result / data / content ...
"""
import base64
import json

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_TEXT_KEYS = ("text", "result", "data", "content", "words", "ocr_text", "ocr_result", "results", "txt")
_ERROR_KEYS = ("error", "message", "msg", "error_msg")

# 服务端会把"额度用尽 / token 失效"当成正常结果返回，需要识别出来提示用户
_SERVER_ERROR_HINTS = (
    "已超期", "超期", "次数不够", "次数不足", "次数用完", "额度", "余额不足", "余额",
    "过期", "失效", "无效", "不存在", "未授权", "未登录", "请充值", "充值",
    "unauthorized", "invalid token", "token invalid", "expired", "quota", "forbidden",
)
_MAX_ERROR_LEN = 40



def recognize_image(image_bytes: bytes, ocr_cfg: dict) -> str:
    url = str(ocr_cfg.get("url", "")).strip()
    if not url:
        raise RuntimeError("尚未配置 OCR 接口地址：托盘右键 → 设置 → 文字识别")
    payload = {
        "token": str(ocr_cfg.get("token", "")),
        "method": int(ocr_cfg.get("method", 2)),
        "image": base64.b64encode(image_bytes).decode("utf-8"),
    }
    try:
        timeout = float(ocr_cfg.get("timeout", 30))
    except (TypeError, ValueError):
        timeout = 30
    try:
        resp = requests.post(url, json=payload, verify=False, timeout=timeout)
    except requests.RequestException as exc:
        raise RuntimeError(f"连接 OCR 接口失败: {exc}") from exc
    if resp.status_code >= 400:
        detail = (resp.text or "").strip()[:200]
        raise RuntimeError(
            f"OCR 接口返回 HTTP {resp.status_code}（服务可能暂时不可用）"
            + (f"：{detail}" if detail else "")
        )
    return _parse_response(resp)


# ---------------- 响应解析 ----------------

def _parse_response(resp) -> str:
    body = (resp.text or "").strip()
    if not body:
        return ""
    try:
        data = resp.json()
    except ValueError:
        return body

    server_error = _detect_server_error(data)
    if server_error:
        raise RuntimeError(server_error)

    text = _extract_text(data)
    if text:
        return text
    if data in (None, "", [], {}):
        return ""      # 服务端返回空结构：当作"没识别到文字"，不要显示 "[]"
    # 解析不出内容时把原始 JSON 露出来，方便排查接口变化
    try:
        return json.dumps(data, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return body


def _detect_server_error(data):
    """识别服务端以正常结果返回的错误文案（token 超期 / 次数不够 等）。

    只在"短文本 + 命中错误关键词"时才判定，避免把正常识别结果误杀。
    """
    text = ""
    if isinstance(data, dict):
        for key in ("result", "msg", "message", "error", "error_msg", "详情", "结果"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                text = value.strip()
                break
    elif isinstance(data, str):
        text = data.strip()

    if not text or len(text) > _MAX_ERROR_LEN:
        return ""
    lowered = text.lower()
    if any(hint in lowered for hint in _SERVER_ERROR_HINTS):
        return (
            f"OCR 服务返回：{text}（多为 Key 已超期 / 次数用尽）"
            "　请到服务商后台续期或充值，或在 设置 → 文字识别 换一个有效 Key"
        )
    return ""



def _extract_text(value) -> str:
    """尽量兼容各种返回结构，提取出纯文本。"""
    if isinstance(value, str):
        return value.strip() or ""

    if isinstance(value, list):
        # PaddleOCR 风格：[[box, [text, score]], ...]
        paddle = _try_paddle(value)
        if paddle:
            return paddle
        parts = [p for p in (_extract_text(v) for v in value) if p]
        return "\n".join(parts) if parts else ""

    if isinstance(value, dict):
        # 百度 OCR 风格：{"ParsedResults":[{"ParsedText": "..."}]}
        parsed = value.get("ParsedResults")
        if isinstance(parsed, list):
            texts = []
            for seg in parsed:
                if isinstance(seg, dict):
                    t = seg.get("ParsedText") or seg.get("ParsedText".lower())
                    if isinstance(t, str) and t.strip():
                        texts.append(t.strip())
            if texts:
                return "\n".join(texts)

        for key in _TEXT_KEYS:
            if key in value:
                got = _extract_text(value[key])
                if got:
                    return got
        for key in _ERROR_KEYS:
            if value.get(key):
                got = _extract_text(value[key])
                if got:
                    return f"[接口错误] {got}"
        parts = [p for p in (_extract_text(v) for v in value.values()) if p]
        return "\n".join(parts) if parts else ""

    return ""


# ---------------- PaddleOCR 风格还原 ----------------

def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _box_anchor(box):
    """把文本框转成 (中心 y, 最小 x, 高度)。无法解析返回 None。"""
    if not isinstance(box, (list, tuple)) or len(box) < 2:
        return None
    pts = []
    if all(isinstance(p, (list, tuple)) and len(p) >= 2 and _is_number(p[0]) and _is_number(p[1]) for p in box):
        pts = [(float(p[0]), float(p[1])) for p in box]
    elif all(_is_number(n) for n in box) and len(box) % 2 == 0:
        nums = [float(n) for n in box]
        pts = [(nums[i], nums[i + 1]) for i in range(0, len(nums), 2)]
    if not pts:
        return None
    ys = [p[1] for p in pts]
    xs = [p[0] for p in pts]
    return (sum(ys) / len(ys), min(xs), max(ys) - min(ys))


def _try_paddle(value):
    """识别 PaddleOCR 风格结果并还原成按阅读顺序排列的多行文本。"""
    entries = []

    def walk(node):
        if isinstance(node, list):
            # 形如 [box, [text, score]] / [box, (text, score)]
            if len(node) >= 2:
                anchor = _box_anchor(node[0])
                text = _ascii_text(node[1])
                if anchor is not None and text:
                    entries.append((anchor[0], anchor[1], anchor[2], text))
                    return
            for sub in node:
                walk(sub)

    walk(value)
    if not entries:
        return None

    heights = [h for _, _, h, _ in entries if h > 0]
    tol = (sum(heights) / len(heights)) * 0.6 if heights else 10.0
    entries.sort(key=lambda e: (e[0], e[1]))

    lines, cur, cur_y = [], [], None
    for cy, x, _h, text in entries:
        if cur_y is None or abs(cy - cur_y) <= tol:
            cur.append((x, text))
            if cur_y is None:
                cur_y = cy
        else:
            lines.append(cur)
            cur, cur_y = [(x, text)], cy
    if cur:
        lines.append(cur)

    out = []
    for line in lines:
        line.sort(key=lambda t: t[0])
        out.append(_smart_join([t for _, t in line]))
    return "\n".join(out)


def _ascii_text(node):
    """从 [text, score] / text 里取出文字。"""
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, (list, tuple)) and node:
        if isinstance(node[0], str):
            return node[0].strip()
        return _ascii_text(node[0])
    return ""


def _smart_join(parts):
    """相邻文本拼接：中英之间不加空格，两个 ASCII 词之间补空格。"""
    out = ""
    for part in parts:
        if not part:
            continue
        if out and _need_space(out[-1], part[0]):
            out += " "
        out += part
    return out


def _need_space(prev_ch, next_ch):
    def _ascii(ch):
        return ch.isascii() and (ch.isalnum() or ch in ")]},.;:!?%\"'")
    return _ascii(prev_ch) and _ascii(next_ch)
