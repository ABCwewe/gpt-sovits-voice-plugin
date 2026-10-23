"""概率语音：判断一条回复是否该转成语音。

随机决定本身很简单，麻烦的是两个工程问题：

1. 一条回复可能被拆成多段分别发送。如果每段都独立掷一次骰子，
   就会出现「前一句是语音、后一句是文字」的割裂感。所以按 stream_id
   缓存一段时间的决定，让同一次回复内的所有消息保持一致。

2. 不是所有内容都适合语音。代码块、网址、表格、长列表用语音念出来
   毫无意义，所以先做内容过滤，再谈概率。
"""

import logging
import random
import re
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("plugin.gpt_sovits_voice.random_voice")

# 缓存超过这个数量时清理过期项，避免长期运行内存膨胀
MAX_CACHE_SIZE = 1000

# 内置的不适合语音的内容特征。用可读的判断而不是纯正则，
# 因为正则很难表达「有 3 行以上且每行都以 - 开头」这类结构
DEFAULT_SKIP_PATTERNS: List[str] = [
    r"```",                      # 代码块
    r"https?://\S+",             # 网址
    r"\|\s*[-\w]+\s*\|",         # 表格
    r"^\s*[-*+]\s+\S",           # Markdown 列表
    r"^\s*\d+\.\s+\S",           # 有序列表
    r"</?[a-zA-Z][^>]*>",        # HTML 标签
    r"\[[^\]]*\]\([^)]*\)",      # Markdown 链接
]


class RandomVoiceDecider:
    """维护「哪条回复该转语音」的决策缓存。"""

    def __init__(self) -> None:
        # stream_id -> (过期时间戳, 是否转语音, 命中的随机数)
        self._decisions: Dict[str, Tuple[float, bool, float]] = {}
        self._pattern_cache: Dict[str, re.Pattern] = {}

    def reset(self) -> None:
        """配置热更新或插件重载时清空缓存。"""

        self._decisions.clear()
        self._pattern_cache.clear()

    # ─── 内容过滤 ────────────────────────────────────────────────────────

    def _compile(self, patterns: List[str]) -> List[re.Pattern]:
        """把配置里的正则编译成 Pattern，失败的跳过并记录一次。"""

        compiled: List[re.Pattern] = []
        for raw in patterns:
            key = raw.strip()
            if not key:
                continue
            cached = self._pattern_cache.get(key)
            if cached is not None:
                compiled.append(cached)
                continue
            try:
                pattern = re.compile(key, re.MULTILINE)
            except re.error as exc:
                logger.warning("概率语音跳过无效正则 %r：%s", key, exc)
                continue
            self._pattern_cache[key] = pattern
            compiled.append(pattern)
        return compiled

    def text_is_suitable(
        self,
        text: str,
        min_length: int,
        max_length: int,
        patterns: Optional[List[str]] = None,
    ) -> Tuple[bool, str]:
        """判断文本是否适合转语音，返回 ``(是否适合, 原因)``。

        原因会写进日志，方便排查「为什么这条没转语音」。
        """

        content = (text or "").strip()
        if not content:
            return False, "文本为空"

        # 用「去空白后的有效字符数」判断长短，避免纯空格或换行占位
        effective = len(re.sub(r"\s+", "", content))
        if effective < min_length:
            return False, f"文本过短（{effective} < {min_length}）"
        if effective > max_length:
            return False, f"文本过长（{effective} > {max_length}）"

        use_patterns = DEFAULT_SKIP_PATTERNS if patterns is None else patterns
        for pattern in self._compile(use_patterns):
            if pattern.search(content):
                return False, f"命中不转语音规则 {pattern.pattern!r}"

        return True, "适合语音"

    # ─── 随机决策 ────────────────────────────────────────────────────────

    def should_convert(
        self,
        stream_id: str,
        probability: float,
        ttl: float,
    ) -> Tuple[bool, bool]:
        """掷骰子判断是否转语音，返回 ``(是否转语音, 是否命中缓存)``。

        同一条回复被拆成多段时，``ttl`` 秒内的后续消息沿用第一次的结果，
        保证一次回复要么整段语音、要么整段文字。
        """

        now = time.monotonic()
        cache_key = stream_id or "__global__"

        cached = self._decisions.get(cache_key)
        if cached is not None:
            expires_at, enabled, roll = cached
            if expires_at > now:
                logger.debug(
                    "概率语音复用缓存 stream=%s 结果=%s 随机数=%.4f（剩余 %.1fs）",
                    cache_key, enabled, roll, expires_at - now,
                )
                return enabled, True

        # 概率为 0 或 1 时不必消耗随机数，也便于日志阅读
        if probability <= 0.0:
            enabled, roll = False, 1.0
        elif probability >= 1.0:
            enabled, roll = True, 0.0
        else:
            roll = random.random()
            enabled = roll < probability

        # ttl<=0 表示不缓存：每次都重新掷骰，避免「每段回复都跟前一段一致」
        if ttl > 0.0:
            self._decisions[cache_key] = (now + ttl, enabled, roll)
            self._prune(now)
        else:
            self._decisions.pop(cache_key, None)

        logger.info(
            "概率语音掷骰 stream=%s 随机数=%.4f 阈值=%.2f 结果=%s",
            cache_key, roll, probability, "转语音" if enabled else "保持文字",
        )
        return enabled, False

    def _prune(self, now: float) -> None:
        """缓存过大时清理，先删过期项，仍超限再按最早过期时间淘汰。"""

        if len(self._decisions) <= MAX_CACHE_SIZE:
            return

        # 先清已过期项
        self._decisions = {
            key: value for key, value in self._decisions.items() if value[0] >= now
        }
        if len(self._decisions) <= MAX_CACHE_SIZE:
            return

        # 仍超限说明大量条目 TTL 很长（配置了较大的 decision_ttl），
        # 按过期时间从早到晚淘汰，避免缓存无界增长。
        overflow = len(self._decisions) - MAX_CACHE_SIZE
        for key in sorted(self._decisions, key=lambda k: self._decisions[k][0])[:overflow]:
            self._decisions.pop(key, None)
