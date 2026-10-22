"""文本处理工具类：语言检测、文本清洗与音频校验。"""

import io
import math
import re
import struct
import wave
from array import array
from typing import Tuple

# 语言检测正则
_CHINESE_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_ENGLISH_PATTERN = re.compile(r"[a-zA-Z]")
# 平假名 + 片假名（含长音符号）。中文文本不会出现假名，出现即可判定日语
_KANA_PATTERN = re.compile(r"[\u3040-\u309f\u30a0-\u30ff\u30fc]")
# 韩文
_KOREAN_PATTERN = re.compile(r"[\uac00-\ud7af\u1100-\u11ff]")

# 需要剔除的不可见/控制字符：
# \u200b-\u200f 零宽字符与方向标记、\ufeff BOM、\u00ad 软连字符、
# \u2060 词连接符、以及各类 C0/C1 控制符
# 注意：不删除 U+200D（ZWJ），它会破坏 emoji 组合序列
_INVISIBLE_PATTERN = re.compile(
    r"[\u200b\u200c\u200e\u200f\u202a-\u202e\ufeff\u00ad\u2060-\u206f"
    r"\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f-\u009f]"
)

# 各类空白字符（含 NBSP、行/段分隔符、全角空格）
_WHITESPACE_PATTERN = re.compile(r"[ \t\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+")

# 音频有效性判定阈值
MIN_AUDIO_SECONDS = 0.5     # 短于该时长视为无效
MIN_AUDIO_RMS = 100.0       # 低于该音量视为静音


def clean_text(text: str) -> str:
    """清洗文本：移除不可见字符、合并多余空白。

    GPT-SoVITS 的日语音素化会 print 音素，而零宽字符（如 \\u200c）
    无法用 GBK 编码，会导致 API 返回 400。这里提前剔除。
    """

    if not text:
        return ""

    cleaned = _INVISIBLE_PATTERN.sub("", text)
    # 合并各类空白为单个空格
    cleaned = _WHITESPACE_PATTERN.sub(" ", cleaned)
    return cleaned.strip()


def is_valid_audio(data: bytes) -> Tuple[bool, str]:
    """检查合成音频是否有效（非静音、时长合理）。

    仅检查字节大小不足以发现「静音崩坏」——GPT-SoVITS 在参考音频与
    提示文本不匹配时会返回结构完整但内容静音的 wav。

    Returns:
        (是否有效, 说明)
    """

    if not data:
        return False, "空数据"

    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            frames = w.getnframes()
            rate = w.getframerate() or 1
            width = w.getsampwidth()
            raw = w.readframes(frames)
    except (wave.Error, EOFError, struct.error) as exc:
        return False, f"无法解析为 wav: {exc}"

    duration = frames / rate
    if duration < MIN_AUDIO_SECONDS:
        return False, f"时长过短（{duration:.2f}s）"

    if not raw:
        return False, "无音频采样数据"

    # 按位宽解析并计算 RMS
    try:
        if width == 2:
            samples = array("h")
            samples.frombytes(raw[: len(raw) // 2 * 2])
            scale = 1.0
        elif width == 4:
            samples = array("i")
            samples.frombytes(raw[: len(raw) // 4 * 4])
            scale = 65536.0
        else:
            # 其他位宽跳过音量检查
            return True, f"{duration:.2f}s（未校验音量，位宽 {width * 8}bit）"
    except (ValueError, OverflowError) as exc:
        return False, f"采样解析失败: {exc}"

    if not samples:
        return False, "无有效采样"

    rms = math.sqrt(sum(v * v for v in samples) / len(samples)) / scale
    if rms < MIN_AUDIO_RMS:
        return False, f"音量过低（rms={rms:.0f}，疑似静音）"

    return True, f"{duration:.2f}s rms={rms:.0f}"


def detect_language(text: str) -> str:
    """检测文本语言，返回 ``zh`` / ``ja`` / ``en`` / ``ko``。

    判定优先级：假名 > 韩文 > 英文 > 中文。
    假名优先是关键：日语大量使用汉字，若按字符比例统计，
    「今日はいい天気ですね」会被误判为中文。
    """

    if not text:
        return "zh"

    # 出现假名即为日语（中文/英文文本中不会出现假名）
    if _KANA_PATTERN.search(text):
        return "ja"

    if _KOREAN_PATTERN.search(text):
        return "ko"

    chinese_chars = len(_CHINESE_PATTERN.findall(text))
    english_chars = len(_ENGLISH_PATTERN.findall(text))
    total = chinese_chars + english_chars

    if total == 0:
        return "zh"

    # 无汉字、英文占绝大多数 -> 英语
    if chinese_chars == 0 and english_chars > 0:
        return "en"

    # 中文为主
    if chinese_chars / total > 0.3:
        return "zh"

    return "en" if english_chars / total > 0.8 else "zh"
