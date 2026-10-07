"""语音合成：Edge-TTS（微软在线，音质好）与 Windows 本地 SAPI（离线）。"""
import logging
import os
import re
import tempfile

from PySide6.QtCore import QObject, QUrl, Signal

from core.utils import run_in_background

try:
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    MULTIMEDIA_OK = True
except Exception:  # pragma: no cover
    MULTIMEDIA_OK = False

# 常用 Edge 音色（value, 显示名）
EDGE_VOICES = [
    ("auto", "自动（根据文本语言选择）"),
    ("zh-CN-XiaoxiaoNeural", "晓晓 · zh-CN · 女"),
    ("zh-CN-XiaoyiNeural", "晓伊 · zh-CN · 女"),
    ("zh-CN-YunjianNeural", "云健 · zh-CN · 男"),
    ("zh-CN-YunxiNeural", "云希 · zh-CN · 男"),
    ("zh-CN-YunxiaNeural", "云夏 · zh-CN · 男"),
    ("zh-CN-YunyangNeural", "云扬 · zh-CN · 男"),
    ("zh-TW-HsiaoChenNeural", "曉臻 · zh-TW · 女"),
    ("zh-HK-HiuMaanNeural", "曉曼 · zh-HK · 女"),
    ("en-US-AriaNeural", "Aria · en-US · 女"),
    ("en-US-JennyNeural", "Jenny · en-US · 女"),
    ("en-US-GuyNeural", "Guy · en-US · 男"),
    ("en-US-ChristopherNeural", "Christopher · en-US · 男"),
    ("en-GB-SoniaNeural", "Sonia · en-GB · 女"),
    ("en-GB-RyanNeural", "Ryan · en-GB · 男"),
    ("ja-JP-NanamiNeural", "七海 · ja-JP · 女"),
    ("ja-JP-KeitaNeural", "圭太 · ja-JP · 男"),
    ("ko-KR-SunHiNeural", "善熙 · ko-KR · 女"),
    ("ko-KR-InJoonNeural", "仁俊 · ko-KR · 男"),
    ("ru-RU-SvetlanaNeural", "Svetlana · ru-RU · 女"),
    ("ru-RU-DmitryNeural", "Dmitry · ru-RU · 男"),
    ("fr-FR-DeniseNeural", "Denise · fr-FR · 女"),
    ("fr-FR-HenriNeural", "Henri · fr-FR · 男"),
    ("de-DE-KatjaNeural", "Katja · de-DE · 女"),
    ("de-DE-ConradNeural", "Conrad · de-DE · 男"),
    ("es-ES-ElviraNeural", "Elvira · es-ES · 女"),
    ("it-IT-ElsaNeural", "Elsa · it-IT · 女"),
    ("pt-BR-FranciscaNeural", "Francisca · pt-BR · 女"),
    ("vi-VN-HoaiMyNeural", "HoaiMy · vi-VN · 女"),
]

_AUTO_VOICE_BY_LANG = {
    "zh": "zh-CN-XiaoxiaoNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "ru": "ru-RU-DmitryNeural",
    "en": "en-US-AriaNeural",
}

_PERCENT_RE = re.compile(r"^[+-]?\d+%$")


def pick_auto_voice(text: str) -> str:
    t = text or ""
    if re.search(r"[\u3040-\u30ff]", t):
        lang = "ja"
    elif re.search(r"[\uac00-\ud7af]", t):
        lang = "ko"
    elif re.search(r"[\u4e00-\u9fff]", t):
        lang = "zh"
    elif re.search(r"[\u0400-\u04ff]", t):
        lang = "ru"
    else:
        lang = "en"
    return _AUTO_VOICE_BY_LANG[lang]


def _normalize_percent(value, default: str) -> str:
    s = str(value or "").strip()
    return s if _PERCENT_RE.match(s) else default


def _edge_synthesize(text: str, voice: str, rate: str, volume: str) -> bytes:
    """同步封装 edge-tts（在工作线程中调用），返回 mp3 字节。"""
    import asyncio
    import sys

    import edge_tts

    if sys.platform == "win32":
        # 避免 Windows Proactor 事件循环在 GC 时的 "Event loop is closed" 噪音
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    async def _run():
        communicate = edge_tts.Communicate(text, voice, rate=rate, volume=volume)
        buf = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)

    return asyncio.run(_run())


def _sapi_say(text: str, voice_id: str, holder: dict) -> None:
    """Windows 本地 SAPI 朗读（在工作线程中调用）。"""
    import pyttsx3

    try:
        import comtypes
        comtypes.CoInitializeEx()
    except Exception:  # noqa: BLE001
        pass
    engine = pyttsx3.init()
    holder["engine"] = engine
    try:
        if voice_id:
            try:
                engine.setProperty("voice", voice_id)
            except Exception:  # noqa: BLE001
                logging.exception("设置 SAPI 音色失败，使用系统默认")
        engine.say(text)
        engine.runAndWait()
    finally:
        holder["engine"] = None


def list_sapi_voices() -> list:
    """枚举本地 SAPI 音色（在工作线程中调用）。"""
    import pyttsx3

    engine = pyttsx3.init()
    try:
        return [(v.id, v.name) for v in engine.getProperty("voices") or []]
    finally:
        try:
            engine.stop()
        except Exception:  # noqa: BLE001
            pass


class TTSService(QObject):
    stateChanged = Signal(str)   # idle / playing / error
    failed = Signal(str)

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self._cfg = cfg
        self._player = None
        self._audio_out = None
        self._temp_files = []
        self._sapi_holder = {}
        self._workers = set()
        if MULTIMEDIA_OK:
            self._player = QMediaPlayer(self)
            self._audio_out = QAudioOutput(self)
            self._audio_out.setVolume(1.0)
            self._player.setAudioOutput(self._audio_out)
            self._player.errorOccurred.connect(self._on_player_error)
            self._player.playbackStateChanged.connect(self._on_playback_state)
        else:
            logging.warning("PySide6.QtMultimedia 不可用，Edge 在线语音将被禁用")

    def set_config(self, cfg: dict):
        self._cfg = cfg

    # ---- 对外接口 ----
    def speak(self, text: str):
        text = (text or "").strip()
        if not text:
            return
        engine = str(self._cfg.get("tts", {}).get("engine", "edge"))
        if engine == "off":
            self.failed.emit("语音合成已关闭（可在设置中开启）")
            return
        self.stop()
        if engine == "sapi":
            self._speak_sapi(text)
        else:
            if not MULTIMEDIA_OK:
                self.failed.emit("当前环境缺少 QtMultimedia，无法播放在线语音")
                return
            self._speak_edge(text)

    def stop(self):
        if self._player is not None:
            self._player.stop()
        engine = self._sapi_holder.get("engine")
        if engine is not None:
            try:
                engine.stop()
            except Exception:  # noqa: BLE001
                pass
        self._set_state("idle")

    # ---- Edge 在线语音 ----
    def _speak_edge(self, text: str):
        tts_cfg = self._cfg.get("tts", {})
        voice = str(tts_cfg.get("voice", "auto") or "auto")
        if voice in ("", "auto"):
            voice = pick_auto_voice(text)
        rate = _normalize_percent(tts_cfg.get("rate"), "+0%")
        volume = _normalize_percent(tts_cfg.get("volume"), "+0%")
        self._set_state("playing")
        run_in_background(
            _edge_synthesize, text, voice, rate, volume,
            on_done=self._on_edge_done, on_error=self._on_synth_error,
            owner=self._workers,
        )

    def _on_edge_done(self, audio: bytes):
        try:
            fd, path = tempfile.mkstemp(suffix=".mp3", prefix="shituruanjian_tts_")
            with os.fdopen(fd, "wb") as f:
                f.write(audio)
        except OSError as exc:
            self.failed.emit(f"写入临时音频文件失败: {exc}")
            self._set_state("error")
            return
        self._temp_files.append(path)
        while len(self._temp_files) > 3:
            old = self._temp_files.pop(0)
            try:
                os.unlink(old)
            except OSError:
                pass
        self._player.setSource(QUrl.fromLocalFile(path))
        self._player.play()

    # ---- Windows 本地语音 ----
    def _speak_sapi(self, text: str):
        voice = str(self._cfg.get("tts", {}).get("sapi_voice", "") or "")
        self._set_state("playing")
        run_in_background(
            _sapi_say, text, voice, self._sapi_holder,
            on_done=lambda _res: self._set_state("idle"),
            on_error=self._on_synth_error,
            owner=self._workers,
        )

    # ---- 信号处理 ----
    def _on_synth_error(self, msg: str):
        self._set_state("error")
        self.failed.emit(f"语音合成失败: {msg}")

    def _on_player_error(self, _err, msg):
        self._set_state("error")
        self.failed.emit(f"音频播放失败: {msg}")

    def _on_playback_state(self, state):
        if not MULTIMEDIA_OK:
            return
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self._set_state("playing" if playing else "idle")

    def _set_state(self, state: str):
        self.stateChanged.emit(state)
