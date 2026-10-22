"""根据文本内容自动推断语音语气。

优先使用配置中的规则（config.toml 的 [[gpt_sovits.auto_style.rules]]），
未命中时回退到内置默认规则；都不命中则返回 default。
"""

import logging
import re
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("plugin.gpt_sovits_voice.style_router")

# 内置默认规则：(风格名, 权重, 正则)
# 权重用于多个规则同时命中时择优，数值越大优先级越高
DEFAULT_RULES: List[Tuple[str, int, str]] = [
    # 兴奋 / 欢呼：感叹号、欢呼词、正情绪爆点
    ("兴奋", 30, r"[!！]{2,}|[!！]$"),
    ("兴奋", 40, r"(好耶|太棒|太强|牛啊|厉害|爽|冲|绝了|起飞|芜湖|哇+|耶+|哈哈哈+|嘿嘿)"),
    ("兴奋", 25, r"(真的假的|不会吧|天呐|天啊|竟然|居然)[!！]?"),
    # 吐槽 / 生气：不耐烦、质疑、负情绪
    ("吐槽", 40, r"(无语|服了|烦死|烦人|搞什么|什么鬼|离谱|离谱|气死|离谱|扯|鬼知道)"),
    ("吐槽", 30, r"(你是不是|你在说什么|怎么可能|开什么玩笑|认真的吗)"),
    ("吐槽", 25, r"(唉|啧|切|哼)[，,。.！!]?"),
    # 撒娇 / 软萌：语气助词密集、拖音
    ("撒娇", 30, r"[～~]"),
    ("撒娇", 20, r"(嘛|呀|啦|哦|欸|诶)[。.！!]?$"),
]

# 编译缓存
_compiled: Dict[str, re.Pattern] = {}


def _compile(pattern: str) -> Optional[re.Pattern]:
    if pattern in _compiled:
        return _compiled[pattern]
    try:
        p = re.compile(pattern)
        _compiled[pattern] = p
        return p
    except re.error as exc:
        logger.warning("语气规则正则无效，已跳过: %s (%s)", pattern, exc)
        return None


def route_style(
    text: str,
    available: List[str],
    custom_rules: Optional[List[Dict]] = None,
    default_style: str = "default",
) -> Tuple[str, str]:
    """根据文本内容选择语气。

    Args:
        text: 待合成文本
        available: 当前可用的风格名列表
        custom_rules: 配置里的自定义规则，每项含 style/pattern/weight
        default_style: 兜底风格

    Returns:
        (风格名, 命中原因)；未命中返回 (default_style, "默认")
    """

    if not text:
        return default_style, "空文本"

    # 组装规则：自定义优先，其次内置
    rules: List[Tuple[str, int, str]] = []
    if custom_rules:
        for item in custom_rules:
            try:
                rules.append((
                    str(item.get("style", "")),
                    int(item.get("weight", 50)),
                    str(item.get("pattern", "")),
                ))
            except (TypeError, ValueError):
                continue
    rules.extend(DEFAULT_RULES)

    scores: Dict[str, int] = {}
    reasons: Dict[str, str] = {}

    for style_name, weight, pattern in rules:
        # 只考虑已配置的风格，避免选中不存在的语气
        if style_name not in available:
            continue
        regex = _compile(pattern)
        if regex is None:
            continue
        if regex.search(text):
            scores[style_name] = scores.get(style_name, 0) + weight
            reasons.setdefault(style_name, pattern)

    if not scores:
        return default_style, "未命中规则"

    best = max(scores, key=lambda k: scores[k])
    return best, f"命中规则 {reasons[best]} (得分 {scores[best]})"
