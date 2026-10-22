"""文本处理工具类：语言检测与文本清洗。"""

import re

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
_INVISIBLE_PATTERN = re.compile(
    r"[\u200b-\u200f\u202a-\u202e\ufeff\u00ad\u2060-\u206f\u0000-\u0008"
    r"\u000b\u000c\u000e-\u001f\u007f-\u009f]"
)


def clean_text(text: str) -> str:
    """清洗文本：移除不可见字符、合并多余空白。

    GPT-SoVITS 的日语音素化会 print 音素，而零宽字符（如 \\u200c）
    无法用 GBK 编码，会导致 API 返回 400。这里提前剔除。
    """

    if not text:
        return ""

    cleaned = _INVISIBLE_PATTERN.sub("", text)
    # 合并连续空白（保留单个空格）
    cleaned = re.sub(r"[ \t\u3000]+", " ", cleaned)
    return cleaned.strip()


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
