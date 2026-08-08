"""文本处理工具类：语言检测。"""

import re

# 语言检测正则
_CHINESE_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_ENGLISH_PATTERN = re.compile(r"[a-zA-Z]")
_JAPANESE_PATTERN = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")


def detect_language(text: str) -> str:
    """检测文本语言，返回 ``zh`` / ``ja`` / ``en``（供 GPT-SoVITS ``text_lang`` 使用）。"""

    if not text:
        return "zh"

    chinese_chars = len(_CHINESE_PATTERN.findall(text))
    english_chars = len(_ENGLISH_PATTERN.findall(text))
    japanese_chars = len(_JAPANESE_PATTERN.findall(text))
    total_chars = chinese_chars + english_chars + japanese_chars

    if total_chars == 0:
        return "zh"

    chinese_ratio = chinese_chars / total_chars
    japanese_ratio = japanese_chars / total_chars
    english_ratio = english_chars / total_chars

    if chinese_ratio > 0.3:
        return "zh"
    if japanese_ratio > 0.3:
        return "ja"
    if english_ratio > 0.8:
        return "en"
    return "zh"
