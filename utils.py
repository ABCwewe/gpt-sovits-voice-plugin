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
# GPT-SoVITS 各底模实际可用的语言集合。
#
# 依据 GPT-SoVITS 源码与实测：
#
# 1. api_v2.py 用 tts_config.languages 做参数校验，只有两套列表：
#       v1     = [auto, en, zh, ja, all_zh, all_ja]          → 不支持 yue
#       v2 及以上 = [..., en, zh, ja, yue, ko, ...]           → 支持 yue
#    所以 v1 不能合成粤语，v2 及以上可以。
#
# 2. 韩语（ko）在所有版本上都不可用，原因是 Windows 下 text/korean.py 依赖
#    eunjeon（mecab 的韩语分词绑定）。没装时该模块会自动尝试
#    「pip install eunjeon」，失败后抛出一个并非 Exception 的对象，
#    最终表现为 api_v2 返回 500：
#        tts failed: exceptions must derive from BaseException
#    这不是版本差异 —— v2/v3/v4/v5 共用同一个 korean.py，都会这样。
#    而 eunjeon 需要 MSVC 构建工具才能编译，多数环境装不上。
#    因此所有版本都不开放韩语，避免触发这个看不懂的内部错误。
#
# 3. ru/th/ar/hi/el 等所有版本都不支持，API 直接返回 not supported。
SUPPORTED_LANGUAGES_BY_VERSION = {
    "v1": {"zh", "en", "ja"},
    "v2": {"zh", "en", "ja", "yue"},
    "v2Pro": {"zh", "en", "ja", "yue"},
    "v2ProPlus": {"zh", "en", "ja", "yue"},
    "v3": {"zh", "en", "ja", "yue"},
    "v4": {"zh", "en", "ja", "yue"},
    "v5dev": {"zh", "en", "ja", "yue"},
    "v5turbo": {"zh", "en", "ja", "yue"},
}

# 可选值：``auto`` 表示按权重路径自动推断，其余为 GPT-SoVITS 的底模版本名
MODEL_VERSION_CHOICES = ("auto", "v1", "v2", "v2Pro", "v2ProPlus", "v3", "v4", "v5dev", "v5turbo")

AUTO_VERSION = "auto"

# 版本名的大小写归一表（用户可能写成 V2ProPlus / v2proplus）
_VERSION_CANONICAL = {v.lower(): v for v in MODEL_VERSION_CHOICES if v != AUTO_VERSION}

# 无法推断版本、且用户未指定时的安全集合
DEFAULT_SUPPORTED_LANGUAGES = {"zh", "en", "ja", "yue"}

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


def normalize_version(version: str | None) -> str:
    """把用户填写的版本名归一为标准写法（大小写不敏感）。

    无法识别时返回空字符串。
    """

    text = (version or "").strip()
    if not text:
        return ""
    if text.lower() == AUTO_VERSION:
        return AUTO_VERSION
    return _VERSION_CANONICAL.get(text.lower(), "")


# 版本 token 的匹配顺序：长的放前面，避免 v2ProPlus 被当成 v2Pro、v5turbo 被当成 v5
_VERSION_TOKEN = re.compile(r"v2proplus|v2pro|v5turbo|v5dev|v4|v3|v2|v1", re.IGNORECASE)


def _version_from_path(path: str) -> str:
    """从权重路径推断版本，只看文件名与父目录。

    GPT-SoVITS 的根目录本身就常带版本信息（如 ``GPT-SoVITS-v2pro-xxxx``），
    若对整条路径匹配，v1 的权重会被根目录里的 ``v2pro`` 误判成 v2Pro。
    所以这里只取「文件名」和「直接父目录」两段，例：

        D:/GPT-SoVITS-v2pro-xxx/SoVITS_weights_v1/azuma_e10.pth
                                     ^^^^^^^^^^^^^^^^^^ 只看这两段
    """

    normalized = (path or "").replace("\\", "/").strip()
    segments = [seg for seg in normalized.split("/") if seg]
    if not segments:
        return ""

    # 严重误判的来源（根目录名）不参与匹配
    candidates = [segments[-1]]
    if len(segments) >= 2:
        candidates.append(segments[-2])

    for segment in candidates:
        match = _VERSION_TOKEN.search(segment)
        if match:
            return _VERSION_CANONICAL.get(match.group(0).lower(), "")
    return ""


def resolve_version(
    configured: str | None = None,
    sovits_weights: str = "",
    gpt_weights: str = "",
) -> str:
    """确定当前使用的底模版本。

    优先级：
    1. 配置里显式指定的 ``model_version``（非 ``auto``）
    2. 从 SoVITS 权重路径推断
    3. 从 GPT 权重路径推断

    Args:
        configured: 配置中的 ``model_version``，``auto`` 表示自动推断
        sovits_weights: SoVITS 权重路径
        gpt_weights: GPT 权重路径

    Returns:
        标准版本名（如 ``v2ProPlus``）；无法确定时返回空字符串
    """

    explicit = normalize_version(configured)
    if explicit and explicit != AUTO_VERSION:
        return explicit

    return _version_from_path(sovits_weights) or _version_from_path(gpt_weights)


def version_from_weights(sovits_weights: str) -> str:
    """从 SoVITS 权重路径推断底模版本（保留旧接口）。"""

    return _version_from_path(sovits_weights)


def supported_languages(version: str | None = None) -> set:
    """返回指定底模支持的语言集合。

    版本未知时返回安全集合，避免放行会触发 400 或空白音频的语言。
    """

    canonical = normalize_version(version)
    if canonical in SUPPORTED_LANGUAGES_BY_VERSION:
        return set(SUPPORTED_LANGUAGES_BY_VERSION[canonical])
    return set(DEFAULT_SUPPORTED_LANGUAGES)


def is_language_supported(lang: str, version: str | None = None) -> bool:
    """判断语言是否被底模支持。"""

    if not lang:
        return False
    return lang.lower() in supported_languages(version)


# 各语言「只有该文字系统才读得出音」的字符集。
# 用中文/英文模型硬读韩文、俄文等，TTS 只会产出接近空白的音频，
# 所以这类文本直接判定为不可朗读，而不是回退后生成垃圾音频。
_SCRIPT_ONLY_CHARS = {
    "ko": re.compile(r"[\uac00-\ud7af\u1100-\u11ff]"),   # 谚文
    "ru": re.compile(r"[\u0400-\u04ff]"),                # 西里尔
    "ar": re.compile(r"[\u0600-\u06ff]"),                # 阿拉伯
    "th": re.compile(r"[\u0e00-\u0e7f]"),                # 泰文
    "hi": re.compile(r"[\u0900-\u097f]"),                # 天城文
    "el": re.compile(r"[\u0370-\u03ff]"),                # 希腊
}


def is_text_readable_as(text: str, lang: str) -> bool:
    """判断 text 能否用 ``lang`` 的模型读出声。

    拉丁字母文本（英/法/德/西等）用带拉丁音素的模型都能勉强读出，
    但谚文、西里尔、阿拉伯、泰文、天城文、希腊文这类文字，
    换成中文/英文模型后完全读不出音，强行合成只会得到空白音频。

    Args:
        text: 待朗读文本
        lang: 目标语言代码

    Returns:
        是否可朗读
    """

    if not text or not lang:
        return False

    # 目标语言自身就是这种文字，当然可读
    if lang in _SCRIPT_ONLY_CHARS:
        return True

    letters = len(re.findall(r"[^\W\d_]", text, re.UNICODE))
    if letters == 0:
        return True

    for script_lang, pattern in _SCRIPT_ONLY_CHARS.items():
        hits = len(pattern.findall(text))
        if hits and hits >= max(1, letters * 0.15):
            return False

    return True
