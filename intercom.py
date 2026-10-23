"""语音回复窗口管理。

用于「收到语音就用语音回」的对话模式（v1.6.0 起内置）。

规则很简单：收到一条语音后开一个窗口（默认 180 秒），
窗口内该会话的回复都用语音发出；每条新语音都会续期。
不维护对话历史——那部分交给宿主，消息正常入库并由 Planner
用完整上下文生成回复。
"""

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger("plugin.gpt_sovits_voice.intercom")


@dataclass
class Window:
    """一个会话的语音回复窗口。"""

    stream_id: str
    opened_at: float
    expires_at: float
    # 触发次数，仅用于日志观察
    hits: int = 0


@dataclass
class Decision:
    """本次语音的处理结论。"""

    respond: bool
    reason: str


class IntercomWindowManager:
    """按 stream_id 维护语音回复窗口。"""

    def __init__(self) -> None:
        self._windows: Dict[str, Window] = {}

    # ------------------------------------------------------------- 判定

    def evaluate(
        self,
        *,
        stream_id: str,
        text: str,
        now: Optional[float] = None,
        min_text_length: int = 1,
        hold_seconds: float = 180.0,
        require_wake_word: bool = False,
        wake_words: Optional[List[str]] = None,
    ) -> Decision:
        """判断这条语音是否要开启/续期语音回复窗口。"""

        current = time.time() if now is None else now
        content = str(text or "").strip()

        if not content:
            return Decision(False, "转写为空")

        if len(content) < max(1, int(min_text_length or 1)):
            return Decision(False, f"文本过短（{len(content)} < {min_text_length}）")

        hold = max(1.0, float(hold_seconds or 180.0))
        words = [str(w) for w in (wake_words or []) if str(w).strip()]
        hit_wake = bool(words) and any(word in content for word in words)

        window = self._windows.get(stream_id)

        # 需要唤醒词时：没有现存窗口且本条不是唤醒词，就不开启
        if require_wake_word and not hit_wake and window is None:
            return Decision(False, "未唤醒")

        if window is None:
            window = Window(stream_id=stream_id, opened_at=current, expires_at=current + hold)
            self._windows[stream_id] = window
            logger.info("语音窗口开启[%s]（%.0f 秒）%s", stream_id, hold, "·唤醒词" if hit_wake else "")
        else:
            window.expires_at = current + hold
            logger.debug("语音窗口续期[%s]", stream_id)

        window.hits += 1
        return Decision(True, "ok")

    # ------------------------------------------------------------- 查询

    def is_voice_active(self, stream_id: str, now: Optional[float] = None) -> bool:
        """该会话当前是否在语音回复窗口内。"""

        current = time.time() if now is None else now
        window = self._windows.get(stream_id)
        if window is None:
            return False
        if current >= window.expires_at:
            self._windows.pop(stream_id, None)
            logger.debug("语音窗口到期[%s]", stream_id)
            return False
        return True

    def force_open(self, stream_id: str, *, hold_seconds: float = 180.0, now: Optional[float] = None) -> None:
        """直接开启（或续期）窗口，跳过唤醒词与字数检查。"""

        current = time.time() if now is None else now
        hold = max(1.0, float(hold_seconds or 180.0))
        window = self._windows.get(stream_id)
        if window is None:
            window = Window(stream_id=stream_id, opened_at=current, expires_at=current + hold)
            self._windows[stream_id] = window
            logger.info("语音窗口被外部强制开启[%s]（%.0f 秒）", stream_id, hold)
        else:
            window.expires_at = current + hold
            logger.info("语音窗口被外部续期[%s]（%.0f 秒）", stream_id, hold)
        window.hits += 1

    def close(self, stream_id: str) -> None:
        """主动关闭某个会话的窗口。"""

        self._windows.pop(stream_id, None)

    def active_streams(self, now: Optional[float] = None) -> List[str]:
        """当前真正有效的窗口对应的 stream_id 列表。

        顺手清掉已过期的条目：窗口只在 ``is_voice_active`` 被调用时才顺带
        过期，没人调用的会话会一直留在字典里。查询统计时统一清理，
        避免长时间运行后字典缓慢增长。
        """

        current = time.time() if now is None else now
        for sid, window in list(self._windows.items()):
            if current >= window.expires_at:
                self._windows.pop(sid, None)
        return list(self._windows.keys())
