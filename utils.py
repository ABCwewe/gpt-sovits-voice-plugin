"""文本处理工具类：语言检测、文本清洗与音频校验。"""

import io
import math
import re
import struct
import wave
from array import array
from typing import Optional, Tuple

# ─── 字符集 ──────────────────────────────────────────────────────────
# 汉字（中日共用）
_CHINESE_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_ENGLISH_PATTERN = re.compile(r"[a-zA-Z]")
# 平假名 + 片假名（含长音符号）。中文文本不会出现假名，出现即可判定日语
_KANA_PATTERN = re.compile(r"[\u3040-\u309f\u30a0-\u30ff\u30fc]")
# 韩文
_KOREAN_PATTERN = re.compile(r"[\uac00-\ud7af\u1100-\u11ff]")

# 粤语独占字：这些字在普通话书面语中基本不用，可用于区分粤语与普通话。
# 参考粤语常用字表，选取辨识度高的部分。
_YUE_CHARS = frozenset(
    "唔咁咗哋佢乜嘢咩嘅嗰嚟攰冇睇喺啱㗎冚嬲咁啲嗮嘞喎咁嘅㗎囉"
    "係哋啲嘅嗰咁咗唔佢乜嘢嚟睇喺啱㗎冇嘢咩嬲冚攰"
    "叻咁嗰啲嘢嘅佢唔咗哋乜嚟睇喺啱㗎冇咩嬲"
    "嘅嗮嘞喎噉嚿嘥啱傾俾搵揾掟攞瞓睇㗎"
)

# 拉丁字母以外的常见文字系统（用于识别「非中英日韩粤」的语言）
_SCRIPT_PATTERNS = {
    "ru": re.compile(r"[\u0400-\u04ff]"),        # 西里尔字母
    "th": re.compile(r"[\u0e00-\u0e7f]"),        # 泰文
    "ar": re.compile(r"[\u0600-\u06ff]"),        # 阿拉伯文
    "hi": re.compile(r"[\u0900-\u097f]"),        # 天城文
    "el": re.compile(r"[\u0370-\u03ff]"),        # 希腊文
}

# 各语言代码的显示名（用于日志与错误提示）
LANGUAGE_NAMES = {
    "zh": "中文",
    "ja": "日语",
    "en": "英语",
    "ko": "韩语",
    "yue": "粤语",
    "ru": "俄语",
    "th": "泰语",
    "ar": "阿拉伯语",
    "hi": "印地语",
    "el": "希腊语",
}

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
            channels = w.getnchannels() or 1
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

    # 立体声等多声道按帧求 RMS，避免声道间相位相消影响判定
    step = max(1, channels)
    frame_count = len(samples) // step
    if frame_count == 0:
        return False, "无有效采样"

    total = 0.0
    for i in range(frame_count):
        base = i * step
        for c in range(step):
            v = samples[base + c]
            total += v * v

    rms = math.sqrt(total / (frame_count * step)) / scale
    if rms < MIN_AUDIO_RMS:
        return False, f"音量过低（rms={rms:.0f}，疑似静音）"

    return True, f"{duration:.2f}s rms={rms:.0f}"


# 需要忽略的符号（判断「纯数字」时不计入内容）
_IGNORABLE_SYMBOLS = re.compile(r"[\s\.,!?;:'\"()\[\]{}<>@#$%^&*\-_=+~`|\\/，。！？；：、（）【】《》「」『』…—·]")

# 阿拉伯数字与中文数字
_ARABIC_DIGITS = re.compile(r"[0-9]")
_CHINESE_DIGITS = re.compile(r"[零〇一二三四五六七八九十百千万亿两壹贰叁肆伍陆柒捌玖拾佰仟]")

# 数字相关的常见单位与量词（判断时视作数字内容的一部分）
# 只收录「数字+单位」本身即构成完整语义的词，
# 不含「个/位/名」等过于宽泛的量词（避免「第3个」被误判）
_NUMBER_UNITS = re.compile(
    r"[年月日号点分秒元块毛角%‰°℃℉]"
    r"|[线路楼层栋期届轮次条张]"
    r"|(?:公斤|千克|克|毫克|吨|米|厘米|毫米|公里|千米|升|毫升)"
    r"|(?:美元|欧元|日元|港币|人民币)"
    r"|(?:小时|分钟|秒钟|个月|星期|礼拜|工作日)"
    r"|(?:上午|下午|中午|凌晨|晚上|早上)"
)


def is_number_only(text: str, allow_chinese: bool = True) -> bool:
    """判断文本是否只由数字构成（可含标点、符号、数字单位）。

    用于决定纯数字内容走哪个模型——播报数字与正常说话的韵律差异较大，
    单独指定模型往往效果更好。

    Args:
        text: 待判断文本
        allow_chinese: 是否把中文数字（一二三、十、百等）也算作数字

    Returns:
        是否为纯数字文本

    Examples:
        >>> is_number_only("12345")
        True
        >>> is_number_only("1,234.56 元")
        True
        >>> is_number_only("2024年10月22日")
        True
        >>> is_number_only("一百二十三")
        True
        >>> is_number_only("今天123")
        False
    """

    if not text:
        return False

    # 去掉标点、符号、空白
    stripped = _IGNORABLE_SYMBOLS.sub("", text)
    if not stripped:
        # 全是符号，视为非数字内容（交由上层拒绝或走默认）
        return False

    # 去掉数字单位
    core = _NUMBER_UNITS.sub("", stripped)
    if not core:
        return False

    # 剩余部分必须全是数字
    digits = _ARABIC_DIGITS.findall(core)
    if allow_chinese:
        digits += _CHINESE_DIGITS.findall(core)

    if not digits:
        return False

    # 数字数量应覆盖剩余字符（允许小数点、负号等已被忽略的符号）
    return len(digits) == len(core)


def detect_language(text: str, fallback: str = "zh") -> str:
    """检测文本语言，返回语言代码（``zh`` / ``ja`` / ``en`` / ``ko`` / ``yue`` 等）。

    判定优先级与依据：

    1. **假名** → ``ja``
       中文文本不会出现平假名/片假名，出现即判定日语。
       这是解决「今日はいい天気ですね」被误判为中文的关键。
    2. **韩文** → ``ko``
       韩文字母（谚文）为韩语独有字符集。
    3. **粤语独占字** → ``yue``
       出现「唔 咁 咗 哋 佢 乜 嘢 嘅 嗰 嚟」等普通话不用的字时判为粤语。
    4. **其他文字系统** → 对应语言
       西里尔、泰文、阿拉伯文、天城文、希腊文等。
    5. **纯拉丁字母** → ``en``
    6. **汉字为主** → ``zh``
    7. **无任何可识别字符** → ``fallback``

    局限：中日共用汉字，纯汉字的日文（如人名「東雪蓮」）无法与技术区分，
    会落入 ``zh``。需要精确控制时请在配置中用 ``language`` 显式指定。

    Args:
        text: 待检测文本
        fallback: 无法判定时的返回值（默认 ``zh``）

    Returns:
        语言代码
    """

    if not text:
        return fallback

    # 1) 假名 → 日语（最可靠，中文不会出现假名）
    if _KANA_PATTERN.search(text):
        return "ja"

    # 2) 韩文 → 韩语
    if _KOREAN_PATTERN.search(text):
        return "ko"

    # 3) 粤语独占字 → 粤语
    yue_hits = sum(1 for ch in text if ch in _YUE_CHARS)
    if yue_hits:
        # 至少命中 1 个字即判定，因为这些字普通话几乎不用
        return "yue"

    # 4) 其他文字系统
    for lang, pattern in _SCRIPT_PATTERNS.items():
        if pattern.search(text):
            return lang

    # 5) 统计汉字与拉丁字母数量
    chinese_chars = len(_CHINESE_PATTERN.findall(text))
    english_chars = len(_ENGLISH_PATTERN.findall(text))
    total = chinese_chars + english_chars

    # 6) 无汉字无字母（纯数字/符号）→ 无法判定
    if total == 0:
        return fallback

    # 7) 纯拉丁字母 → 英语
    if chinese_chars == 0:
        return "en"

    # 8) 有汉字，按占比区分中英混排
    if chinese_chars / total > 0.3:
        return "zh"

    return "en" if english_chars / total > 0.8 else "zh"
