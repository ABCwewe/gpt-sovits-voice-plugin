"""GPT-SoVITS 客户端：模型权重切换与语音合成。"""

import logging
from typing import ClassVar, Dict, Optional, Tuple

import aiohttp

from .config import GPTSovitsConfig, GPTSovitsStyle
from .utils import detect_language

logger = logging.getLogger("plugin.gpt_sovits_voice.gpt_sovits")

# 音频数据最小有效大小（字节），小于该值视为合成失败
MIN_AUDIO_SIZE = 100


class GPTSovitsClient:
    """封装 GPT-SoVITS HTTP 接口：按风格切换模型并合成音频。

    每个风格拥有独立的参考音频、提示文本与模型权重；
    风格不存在或未指定时由 :meth:`resolve_style` 回退到 ``default``。
    """

    # 类变量：记录当前已加载的模型权重，避免重复切换
    _current_gpt_weights: ClassVar[Optional[str]] = None
    _current_sovits_weights: ClassVar[Optional[str]] = None

    def __init__(self, session: aiohttp.ClientSession, config: GPTSovitsConfig) -> None:
        self._session = session
        self._config = config

    def styles(self) -> Dict[str, GPTSovitsStyle]:
        """将配置中的风格列表转换为 ``{name: style}`` 映射。"""

        return {style.name: style for style in self._config.styles if style.name}

    def resolve_style(self, style: str, log_prefix: str) -> str:
        """解析最终风格：精确命中配置则使用，否则（缺失/错误参数）回退 default。"""

        styles = self.styles()
        if style and style in styles:
            return style
        if style:
            logger.warning("%s 风格 '%s' 不存在，回退 default", log_prefix, style)
        return "default"

    async def synthesize(self, text: str, style_name: str, log_prefix: str) -> Optional[bytes]:
        """调用 GPT-SoVITS ``/tts`` 接口合成音频，失败返回 ``None``。"""

        styles = self.styles()
        style = styles.get(style_name) or styles.get("default")
        if style is None:
            logger.error("%s 找不到可用风格配置", log_prefix)
            return None

        # 风格配置了模型权重时，先切换模型
        if style.gpt_weights or style.sovits_weights:
            switch_ok, switch_error = await self._switch_model(style)
            if not switch_ok:
                logger.error("%s %s", log_prefix, switch_error)
                return None

        request_data = {
            "text": text,
            "text_lang": detect_language(text),
            "ref_audio_path": style.refer_wav,
            "prompt_text": style.prompt_text,
            "prompt_lang": style.prompt_language,
        }
        tts_url = f"{self._config.server.rstrip('/')}/tts"

        logger.info(
            "%s GPT-SoVITS 请求: text='%s...', style=%s",
            log_prefix, text[:30], style_name,
        )

        async with self._session.post(tts_url, json=request_data) as response:
            if response.status != 200:
                error_text = (await response.text())[:200]
                logger.error(
                    "%s GPT-SoVITS API 失败[%s]: %s", log_prefix, response.status, error_text,
                )
                return None
            audio_data = await response.read()

        # 验证音频数据
        if not audio_data or len(audio_data) < MIN_AUDIO_SIZE:
            logger.error(
                "%s 音频数据无效（%s 字节）", log_prefix, len(audio_data or b""),
            )
            return None

        return audio_data

    async def _switch_model(self, style: GPTSovitsStyle) -> Tuple[bool, str]:
        """按风格切换 GPT-SoVITS 模型权重（已加载的权重不重复切换）。"""

        server = self._config.server

        # 切换 GPT 权重
        if style.gpt_weights and style.gpt_weights != GPTSovitsClient._current_gpt_weights:
            gpt_url = f"{server.rstrip('/')}/set_gpt_weights?weights_path={style.gpt_weights}"
            async with self._session.get(gpt_url) as response:
                if response.status != 200:
                    return False, f"GPT模型切换失败: {await response.text()}"
            GPTSovitsClient._current_gpt_weights = style.gpt_weights
            logger.info("已切换 GPT 模型: %s", style.gpt_weights)

        # 切换 SoVITS 权重
        if style.sovits_weights and style.sovits_weights != GPTSovitsClient._current_sovits_weights:
            sovits_url = f"{server.rstrip('/')}/set_sovits_weights?weights_path={style.sovits_weights}"
            async with self._session.get(sovits_url) as response:
                if response.status != 200:
                    return False, f"SoVITS模型切换失败: {await response.text()}"
            GPTSovitsClient._current_sovits_weights = style.sovits_weights
            logger.info("已切换 SoVITS 模型: %s", style.sovits_weights)

        return True, ""
