"""音频发送：base64 / 文件路径双模式。"""

import asyncio
import base64
import logging
import os
import uuid
from typing import TYPE_CHECKING, Optional, Tuple

from .config import GeneralConfig

if TYPE_CHECKING:
    from maibot_sdk.context import PluginContext

logger = logging.getLogger("plugin.gpt_sovits_voice.audio")

# 文件路径模式发送时，临时音频的延迟清理时间（秒），给 adapter 上传留足时间
CLEANUP_DELAY = 180


class AudioSender:
    """把合成好的音频发送到会话：base64 直接走 IPC，或写文件后发 voiceurl。"""

    def __init__(self, config: GeneralConfig, project_root: str) -> None:
        self._config = config
        self._project_root = project_root

    async def send(
        self,
        ctx: "PluginContext",
        audio_data: bytes,
        style_name: str,
        stream_id: str,
        log_prefix: str,
    ) -> Tuple[bool, str]:
        """发送语音，返回 ``(success, message)``。"""

        if self._config.use_base64_audio:
            audio_b64 = base64.b64encode(audio_data).decode("utf-8")
            await ctx.send.custom("voice", audio_b64, stream_id)
            logger.info(
                "%s 语音已发送（base64，%s 字节，风格=%s）",
                log_prefix, len(audio_data), style_name,
            )
            return True, f"已发送语音（风格: {style_name}）"

        audio_path = await self._save_file(audio_data)
        if audio_path is None:
            return False, "保存音频文件失败"

        await ctx.send.custom("voiceurl", audio_path, stream_id)
        logger.info("%s 语音已发送（文件，%s，风格=%s）", log_prefix, audio_path, style_name)
        # 延迟清理：给 adapter 上传到 QQ 留足时间
        asyncio.create_task(self._cleanup_async(audio_path))
        return True, f"已发送语音（风格: {style_name}）"

    async def _save_file(self, audio_data: bytes) -> Optional[str]:
        """在后台线程写入音频文件，返回文件路径。"""

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._write_sync, audio_data)

    def _write_sync(self, audio_data: bytes) -> Optional[str]:
        """同步写入音频文件（内部方法，在后台线程执行）。"""

        output_dir = self._config.audio_output_dir.strip()
        if output_dir:
            output_dir = (
                output_dir
                if os.path.isabs(output_dir)
                else os.path.join(self._project_root, output_dir)
            )
            try:
                os.makedirs(output_dir, exist_ok=True)
            except OSError as exc:
                logger.error("创建输出目录失败 %s: %s", output_dir, exc)
                return None
        else:
            output_dir = self._project_root

        filename = f"tts_gpt_sovits_{uuid.uuid4().hex[:12]}.wav"
        path = os.path.join(output_dir, filename)
        try:
            with open(path, "wb") as f:
                f.write(audio_data)
            return path
        except OSError as exc:
            logger.error("写入音频文件失败 %s: %s", path, exc)
            return None

    async def _cleanup_async(self, path: str) -> None:
        """延迟删除临时音频文件。"""

        await asyncio.sleep(CLEANUP_DELAY)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._cleanup_sync, path)

    @staticmethod
    def _cleanup_sync(path: str) -> None:
        """同步删除文件（内部方法，在后台线程执行）。"""

        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            logger.debug("清理临时音频失败 %s: %s", path, exc)
