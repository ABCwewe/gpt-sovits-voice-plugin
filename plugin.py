"""GPT-SoVITS 语音合成插件（MaiBot SDK 2.x）

基于本地 GPT-SoVITS 服务合成语音，支持多种语音风格：
- LLM 通过 ``tts_voice`` 工具自主调用；
- 用户通过 ``/tts`` 命令手动触发；
- 风格在 ``config.toml`` 的 ``[[gpt_sovits.styles]]`` 中配置，每个风格独立
  拥有参考音频、提示文本、模型权重等全部参数；风格不存在或未指定时回退 default。

Version: 1.0.0
"""

import asyncio
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from maibot_sdk import Command, MaiBotPlugin, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

from .audio import AudioSender
from .config import PluginConfig
from .gpt_sovits import GPTSovitsClient
from .utils import split_sentences

logger = logging.getLogger("plugin.gpt_sovits_voice")

# 工具 style 参数的可选枚举（与 config.toml 中的风格保持一致；执行时以配置为准）
STYLE_ENUM = ["default", "流萤_活泼", "流萤_伤感", "流萤_疑惑", "流萤_平静"]


class GPTSovitsVoicePlugin(MaiBotPlugin):
    """GPT-SoVITS 语音合成插件。

    ``tts_voice`` 工具由 LLM 自主决定是否调用；
    ``/tts`` 命令由用户手动触发，均支持 ``-v <风格>`` 指定语音风格。
    """

    config_model = PluginConfig

    def __init__(self) -> None:
        super().__init__()
        self._session: Optional[aiohttp.ClientSession] = None
        self._client: Optional[GPTSovitsClient] = None
        self._audio: Optional[AudioSender] = None

    # ─── 生命周期 ────────────────────────────────────────────────────────

    async def on_load(self) -> None:
        """插件加载：创建会话与客户端，并打印风格概况。"""

        timeout = max(self.config.gpt_sovits.timeout, 10)
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout),
        )
        self._client = GPTSovitsClient(self._session, self.config.gpt_sovits)
        self._audio = AudioSender(self.config.general, self._project_root())
        style_names = list(self._client.styles().keys())
        self.ctx.logger.info(
            "GPT-SoVITS 语音插件已加载，服务=%s，风格=%s",
            self.config.gpt_sovits.server,
            style_names or ["default"],
        )

    async def on_unload(self) -> None:
        """插件卸载：关闭 HTTP 会话。"""

        if self._session is not None and not self._session.closed:
            await self._session.close()
        self.ctx.logger.info("GPT-SoVITS 语音插件已卸载")

    async def on_config_update(self, scope: str, config_data: Dict[str, Any], version: str) -> None:
        """配置热更新回调。"""

        del config_data
        self.ctx.logger.info(
            "GPT-SoVITS 语音插件配置已更新: scope=%s version=%s", scope, version,
        )

    # ─── 工具：LLM 自主调用 ──────────────────────────────────────────────

    @Tool(
        "tts_voice",
        description="用语音回复（本地 GPT-SoVITS），让对话更生动亲切",
        brief_description="用语音回复",
        detailed_description=(
            "【使用建议】\n"
            "- 用户明确要求用语音回复时必须使用；也适合简短问候、活泼场景\n"
            "- 过长（>100字）或过短（<3字）的内容不适合语音，请直接文字回复\n"
            "- 可选 style 参数选择语音风格；不传则用 default，风格不存在会自动回退 default\n"
            "- 可用风格：default、流萤_活泼、流萤_伤感、流萤_疑惑、流萤_平静"
        ),
        parameters=[
            ToolParameterInfo(
                name="text",
                param_type=ToolParamType.STRING,
                description="要转换为语音的文本内容（必填）",
                required=True,
            ),
            ToolParameterInfo(
                name="style",
                param_type=ToolParamType.STRING,
                description="语音风格（可选，不传则用 default；风格不存在会自动回退 default）",
                required=False,
                enum_values=STYLE_ENUM,
            ),
        ],
    )
    async def handle_tts(
        self,
        stream_id: str = "",
        text: str = "",
        style: str = "",
        **kwargs: Any,
    ) -> Tuple[bool, str]:
        """处理 LLM 调用的 ``tts_voice`` 工具。"""

        del kwargs
        if not stream_id:
            return False, "缺少 stream_id，无法发送"
        return await self._handle(stream_id, text, style, "tool")

    # ─── 命令：用户手动触发 ─────────────────────────────────────────────

    @Command(
        "tts",
        description="将文本转换为语音（GPT-SoVITS）",
        pattern=r"^/(?:tts|voice|gptsovits)\s+(?P<text>.+?)(?:\s+-v\s+(?P<style>\S+))?$",
    )
    async def handle_tts_command(
        self,
        stream_id: str = "",
        text: str = "",
        matched_groups: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> Tuple[bool, str, bool]:
        """处理 ``/tts`` 系列命令。

        Returns:
            (success, message, intercept) 三元组，``intercept=True`` 表示拦截消息不再继续。
        """

        del kwargs
        groups = matched_groups or {}
        user_text = (groups.get("text") or "").strip()
        user_style = (groups.get("style") or "").strip()

        if not stream_id:
            return False, "缺少 stream_id", True

        if user_text.lower() == "help":
            await self._send_help(stream_id)
            return True, "显示帮助信息", True

        if not user_text:
            await self._send_error(stream_id, "请输入要转换为语音的文本内容")
            return False, "缺少文本内容", True

        success, message = await self._handle(stream_id, user_text, user_style, "command")
        return success, message, True

    # ─── 公共处理链路 ────────────────────────────────────────────────────

    async def _handle(
        self,
        stream_id: str,
        text: str,
        style: str,
        source_desc: str,
    ) -> Tuple[bool, str]:
        """公共合成发送链路：Tool 与 Command 共用。"""

        log_prefix = f"[gpt_sovits_voice][{source_desc}][{stream_id}]"
        try:
            if self._client is None or self._audio is None:
                return False, "插件尚未完成加载"

            raw_text = (text or "").strip()
            if not raw_text:
                return False, "文本为空"

            max_length = self.config.general.max_text_length
            if len(raw_text) > max_length:
                # 超过长度限制降级为文字回复，避免超长内容被截断播放
                self.ctx.logger.warning(
                    "%s 文本长度 %d 超过限制 %d，降级为文字回复",
                    log_prefix, len(raw_text), max_length,
                )
                await self.ctx.send.text(
                    f"文本过长（{len(raw_text)}字符），超过语音长度限制（{max_length}），"
                    f"已改为文字发送。\n\n{raw_text}",
                    stream_id,
                )
                return True, "文本过长，已改为文字回复"

            style_name = self._client.resolve_style(style, log_prefix)

            # 分句：长文本逐段发送，提升听感
            if self.config.general.split_sentences:
                sentences = [s.strip() for s in split_sentences(raw_text) if s.strip()]
            else:
                sentences = [raw_text]
            if not sentences:
                sentences = [raw_text]

            return await self._send_in_segments(
                sentences=sentences,
                style_name=style_name,
                stream_id=stream_id,
                log_prefix=log_prefix,
            )
        except asyncio.TimeoutError:
            timeout_sec = self.config.gpt_sovits.timeout
            self.ctx.logger.error("%s TTS 超时（%ss）", log_prefix, timeout_sec)
            await self._send_error(stream_id, f"语音合成超时（{timeout_sec}s）")
            return False, "timeout"
        except aiohttp.ClientError as exc:
            self.ctx.logger.error("%s TTS 网络错误: %s", log_prefix, exc)
            await self._send_error(stream_id, f"语音合成网络错误: {type(exc).__name__}")
            return False, str(exc)
        except (KeyError, AttributeError, ValueError) as exc:
            self.ctx.logger.error(
                "%s TTS 配置或参数错误: %s", log_prefix, exc, exc_info=True,
            )
            await self._send_error(stream_id, f"语音合成参数错误: {exc}")
            return False, str(exc)

    async def _send_in_segments(
        self,
        sentences: List[str],
        style_name: str,
        stream_id: str,
        log_prefix: str,
    ) -> Tuple[bool, str]:
        """逐句合成发送；单句直接发送。"""

        if len(sentences) <= 1:
            success, message = await self._synthesize_and_send(
                sentences[0], style_name, stream_id, log_prefix,
            )
            if not success:
                await self._send_error(stream_id, f"语音合成失败: {message}")
            return success, message

        success_count = 0
        for index, sentence in enumerate(sentences):
            self.ctx.logger.debug(
                "%s 分段发送 %d/%d: %s",
                log_prefix, index + 1, len(sentences), sentence[:30],
            )
            success, _ = await self._synthesize_and_send(
                sentence, style_name, stream_id, log_prefix,
            )
            if success:
                success_count += 1
            else:
                self.ctx.logger.warning(
                    "%s 分段 %d 失败", log_prefix, index + 1,
                )
            if index < len(sentences) - 1 and self.config.general.split_delay > 0:
                await asyncio.sleep(self.config.general.split_delay)

        if success_count == 0:
            await self._send_error(stream_id, "语音合成失败")
            return False, "所有语音发送失败"
        return True, f"成功发送 {success_count}/{len(sentences)} 条语音"

    async def _synthesize_and_send(
        self,
        text: str,
        style_name: str,
        stream_id: str,
        log_prefix: str,
    ) -> Tuple[bool, str]:
        """合成单句语音并发送。"""

        audio_data = await self._client.synthesize(text, style_name, log_prefix)
        if audio_data is None:
            return False, f"合成失败（风格: {style_name}）"
        return await self._audio.send(
            self.ctx, audio_data, style_name, stream_id, log_prefix,
        )

    # ─── 辅助 ────────────────────────────────────────────────────────────

    async def _send_error(self, stream_id: str, message: str) -> None:
        """根据配置决定是否向用户发送错误提示。"""

        if self.config.general.send_error_messages:
            await self.ctx.send.text(message, stream_id)

    async def _send_help(self, stream_id: str) -> None:
        """发送帮助信息。"""

        styles = self._client.styles()
        style_desc = "、".join(styles.keys()) if styles else "default"
        help_text = (
            "【GPT-SoVITS 语音插件帮助】\n\n"
            "📝 基本语法：\n"
            "/tts <文本> [-v <风格>]\n\n"
            "🎯 示例：\n"
            "/tts 你好世界\n"
            "/tts 今天真开心 -v 流萤_活泼\n"
            "/gptsovits 用本地语音合成\n\n"
            f"🎤 可用风格：{style_desc}\n"
            "（不指定风格时使用 default；风格不存在会自动回退 default）"
        )
        await self.ctx.send.text(help_text, stream_id)

    def _project_root(self) -> str:
        """推断 MaiBot 项目根目录（plugins/<本插件>/plugin.py 向上三级）。"""

        return os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )


def create_plugin() -> GPTSovitsVoicePlugin:
    """创建 GPT-SoVITS 语音插件实例（SDK 入口）。"""

    return GPTSovitsVoicePlugin()
