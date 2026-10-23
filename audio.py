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
        # 持有延迟清理任务的强引用。asyncio 只保存任务的弱引用，
        # 不持有的话任务可能在 sleep 期间被 GC 回收，临时文件就永远留着了
        self._cleanup_tasks: set[asyncio.Task] = set()
        # 尚未清理的文件路径。任务被取消时靠它兜底删除，避免残留。
        self._pending_paths: set[str] = set()

    async def save_temp_file(self, audio_data: bytes, ctx: "PluginContext") -> Optional[str]:
        """把音频写入临时文件并安排延迟清理，返回路径供出站消息引用。"""

        audio_path = await self._save_file(audio_data, ctx)
        if audio_path is None:
            return None
        self._pending_paths.add(audio_path)
        task = asyncio.create_task(self._cleanup_async(audio_path))
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)
        return audio_path

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
            # 显式开启了 base64 模式。该模式会把整段音频编码进消息体，
            # 随后作为文本进入 MaiBot 上下文并按 token 计费，代价极高，
            # 因此这里额外打一条警告，便于用户对照费用排查。
            logger.warning(
                "%s 使用 base64 模式发送语音，音频会进入上下文并按 token 计费，"
                "建议改用文件模式（use_base64_audio = false）",
                log_prefix,
            )
            audio_b64 = base64.b64encode(audio_data).decode("utf-8")
            await ctx.send.custom("voice", audio_b64, stream_id)
            logger.info(
                "%s 语音已发送（base64，%s 字节，风格=%s）",
                log_prefix, len(audio_data), style_name,
            )
            return True, f"已发送语音（风格: {style_name}）"

        # 文件模式统一走 save_temp_file，保证待清理路径被登记，
        # 卸载或热重载时才能正确收尾。
        audio_path = await self.save_temp_file(audio_data, ctx)
        if audio_path is None:
            return False, "保存音频文件失败"

        await ctx.send.custom("voiceurl", audio_path, stream_id)
        logger.info("%s 语音已发送（文件，%s，风格=%s）", log_prefix, audio_path, style_name)
        return True, f"已发送语音（风格: {style_name}）"

    def _resolve_output_dir(self, ctx: "PluginContext") -> str:
        """确定音频落盘目录。

        优先级：
        1. 配置里的 ``audio_output_dir``（支持绝对路径），未配置时留空
        2. Runner 分配的 ``ctx.paths.runtime_dir``——临时音频放这里最稳，
           不用猜主程序装在哪，也不会污染插件仓库
        3. 退化为项目根目录
        """

        configured = self._config.audio_output_dir.strip()
        if configured:
            return (
                configured
                if os.path.isabs(configured)
                else os.path.join(self._project_root, configured)
            )

        # ctx.paths 由 Runner 按插件 ID 分配，比手搓路径可靠
        paths = getattr(ctx, "paths", None)
        runtime_dir = getattr(paths, "runtime_dir", None)
        if runtime_dir:
            return str(runtime_dir)

        return self._project_root

    async def _save_file(self, audio_data: bytes, ctx: "PluginContext") -> Optional[str]:
        """在后台线程写入音频文件，返回文件路径。"""

        loop = asyncio.get_running_loop()
        output_dir = self._resolve_output_dir(ctx)
        return await loop.run_in_executor(None, self._write_sync, audio_data, output_dir)

    def _write_sync(self, audio_data: bytes, output_dir: str) -> Optional[str]:
        """同步写入音频文件（内部方法，在后台线程执行）。"""

        if output_dir:
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

        try:
            await asyncio.sleep(CLEANUP_DELAY)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._cleanup_sync, path)
        finally:
            self._pending_paths.discard(path)

    async def close(self) -> None:
        """取消待执行的清理任务并立即删除已落盘的临时文件。

        插件卸载或进程退出时，延迟清理任务会被取消，音频文件就会残留。
        这里主动收尾，避免临时目录持续堆积。
        """

        tasks = list(self._cleanup_tasks)
        self._cleanup_tasks.clear()
        for task in tasks:
            task.cancel()

        pending_paths = list(self._pending_paths)
        self._pending_paths.clear()
        if not pending_paths:
            return

        loop = asyncio.get_running_loop()
        for path in pending_paths:
            await loop.run_in_executor(None, self._cleanup_sync, path)

    @staticmethod
    def _cleanup_sync(path: str) -> None:
        """同步删除文件（内部方法，在后台线程执行）。"""

        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            logger.debug("清理临时音频失败 %s: %s", path, exc)
