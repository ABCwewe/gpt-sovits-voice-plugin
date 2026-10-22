"""GPT-SoVITS 语音合成插件（MaiBot SDK 2.x）

基于本地 GPT-SoVITS 服务合成语音，支持多种语音风格：
- LLM 通过 ``tts_voice`` 工具自主调用；
- 用户通过 ``/tts`` 命令手动触发；
- 风格在 ``config.toml`` 的 ``[[gpt_sovits.styles]]`` 中配置，每个风格独立
  拥有参考音频、提示文本、模型权重等全部参数；
- 未指定风格时按文本内容自动选择语气，并按检测到的语言切换对应模型。

Version: 1.1.0
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import asyncio
import logging

import aiohttp

from maibot_sdk import Command, MaiBotPlugin, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

from .audio import AudioSender
from .config import PluginConfig
from .gpt_sovits import GPTSovitsClient
from .style_router import DEFAULT_RULES, route_style
from .utils import clean_text, detect_language

logger = logging.getLogger("plugin.gpt_sovits_voice")


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
        self._missing_styles_warned = False

    # ─── 生命周期 ────────────────────────────────────────────────────────

    async def on_load(self) -> None:
        """插件加载：创建会话与客户端，并打印风格概况。"""

        await self._build_runtime()
        self.ctx.logger.info(
            "GPT-SoVITS 语音插件已加载，服务=%s，风格=%s",
            self.config.gpt_sovits.server,
            list(self._client.styles().keys()) if self._client else [],
        )

    async def on_unload(self) -> None:
        """插件卸载：关闭 HTTP 会话。"""

        await self._close_session()
        self.ctx.logger.info("GPT-SoVITS 语音插件已卸载")

    async def on_config_update(self, scope: str, config_data: Dict[str, Any], version: str) -> None:
        """配置热更新回调。

        重建客户端与会话，否则旧的超时、服务地址、风格列表不会生效。
        """

        del config_data
        self.ctx.logger.info(
            "GPT-SoVITS 语音插件配置已更新: scope=%s version=%s", scope, version,
        )
        await self._build_runtime()
        self.ctx.logger.info(
            "配置已重新加载：服务=%s，风格=%s",
            self.config.gpt_sovits.server,
            list(self._client.styles().keys()) if self._client else [],
        )

    async def _close_session(self) -> None:
        """关闭当前 HTTP 会话（若存在）。"""

        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _build_runtime(self) -> None:
        """按当前配置重建会话、客户端与发送器。"""

        await self._close_session()

        timeout = max(self.config.gpt_sovits.timeout, 10)
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout),
        )
        self._client = GPTSovitsClient(self._session, self.config.gpt_sovits)
        self._audio = AudioSender(self.config.general, self._project_root())
        # 配置可能改了权重路径，清空缓存以强制重新切换
        self._client.reset_model_state()
        self._missing_styles_warned = False

    # ─── 工具：LLM 自主调用 ──────────────────────────────────────────────

    @Tool(
        "tts_voice",
        description="用语音回复（本地 GPT-SoVITS），让对话更生动亲切",
        brief_description="用语音回复",
        detailed_description=(
            "【使用建议】\n"
            "- 用户明确要求用语音回复时必须使用；也适合简短问候、活泼场景\n"
            "- 过长（>100字）或过短（<3字）的内容不适合语音，请直接文字回复\n"
            "\n【语气选择】\n"
            "- style 参数可选，不传时插件会按文本内容自动判断语气\n"
            "- 你也可以主动指定以表达更准确的情绪，常见风格：\n"
            "  · 兴奋：表达惊喜、夸赞、欢呼时（如「好耶！」「太棒了吧」）\n"
            "  · 吐槽：表达无语、嫌弃、吐槽时（如「你在说什么啊」「服了」）\n"
            "  · 默认：日常陈述、平淡对话\n"
            "- 实际可用风格以 config.toml 的 [[gpt_sovits.styles]] 为准；"
            "风格不存在时会自动回退到默认风格"
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
                description=(
                    "语音风格（可选，不传则按文本内容自动选择；"
                    "可用风格以 config.toml 的 styles 配置为准）"
                ),
                required=False,
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

            # 先清洗不可见字符（零宽符等会导致 GPT-SoVITS 侧 GBK 编码失败）
            original = (text or "").strip()
            raw_text = clean_text(original)
            if not raw_text:
                if original:
                    self.ctx.logger.warning(
                        "%s 文本仅含不可见字符，拒绝合成", log_prefix,
                    )
                return False, "文本为空"
            if len(original) != len(raw_text):
                self.ctx.logger.info(
                    "%s 已清洗 %d 个不可见字符",
                    log_prefix, len(original) - len(raw_text),
                )

            min_length = self.config.general.min_text_length
            max_length = self.config.general.max_text_length
            text_length = len(raw_text)

            if not min_length <= text_length <= max_length:
                if text_length < min_length:
                    message = f"文本过短（{text_length}字），无法合成语音"
                    result = "文本过短"
                else:
                    message = f"文本过长（{text_length}字），超过语音长度限制（{max_length}）"
                    result = "文本过长"
                self.ctx.logger.warning("%s %s", log_prefix, message)
                await self._send_error(stream_id, message)
                return False, result

            style_name = self._client.resolve_style(style, log_prefix)

            # 调用方未指定风格时，按文本内容自动选择语气
            if not (style or "").strip():
                auto = getattr(self.config.gpt_sovits, "auto_style", None)
                if auto is not None and auto.enabled:
                    available = list(self._client.styles().keys())
                    picked, reason = route_style(
                        raw_text,
                        available,
                        [r.model_dump() for r in auto.rules],
                        auto.fallback or self._client.default_style_name(),
                    )
                    if picked in available:
                        style_name = picked
                        self.ctx.logger.info(
                            "%s 自动语气: %s（%s）", log_prefix, picked, reason,
                        )
                        # 首次提示：内置规则引用了未配置的风格
                        self._warn_missing_styles(available)

            # 按文本语言挑选对应语言的模型/参考音频
            lang = detect_language(raw_text)
            lang_style = self._client.resolve_by_language(lang, style_name, log_prefix)
            if lang_style != style_name:
                self.ctx.logger.info(
                    "%s 语言路由: %s -> %s", log_prefix, style_name, lang_style,
                )
                style_name = lang_style

            # 不分句，整段直接合成（分句交给合成服务智能处理）
            audio_data = await self._client.synthesize(raw_text, style_name, log_prefix)
            if audio_data is None:
                await self._send_error(stream_id, "语音合成失败")
                return False, "合成失败"

            success, message = await self._audio.send(
                self.ctx, audio_data, style_name, stream_id, log_prefix,
            )
            if not success:
                await self._send_error(stream_id, f"语音合成失败: {message}")
            return success, message
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
        except OSError as exc:
            self.ctx.logger.error("%s TTS 文件系统错误: %s", log_prefix, exc)
            await self._send_error(stream_id, f"语音合成文件错误: {exc}")
            return False, str(exc)
        except Exception as exc:  # 兜底，避免异常冒泡中断会话
            self.ctx.logger.error(
                "%s TTS 未预期错误: %s", log_prefix, exc, exc_info=True,
            )
            await self._send_error(stream_id, "语音合成发生内部错误")
            return False, str(exc)

    # ─── 辅助 ────────────────────────────────────────────────────────────

    def _warn_missing_styles(self, available: List[str]) -> None:
        """首次发现内置规则引用了未配置的风格时，输出一次提示。"""

        if self._missing_styles_warned:
            return
        self._missing_styles_warned = True

        # 只需关注用户已配置了部分语气、但内置规则还引用其它语气的情况
        referenced = {name for name, _w, _p in DEFAULT_RULES}
        missing = sorted(referenced - set(available))
        if missing:
            self.ctx.logger.info(
                "内置语气规则引用了未配置的风格 %s，对应规则不会生效"
                "（如需启用请在 config.toml 的 [[gpt_sovits.styles]] 中补充）",
                missing,
            )

    async def _send_error(self, stream_id: str, message: str) -> None:
        """根据配置决定是否向用户发送错误提示。"""

        if self.config.general.send_error_messages:
            await self.ctx.send.text(message, stream_id)

    async def _send_help(self, stream_id: str) -> None:
        """发送帮助信息。"""

        styles = self._client.styles()
        style_desc = "、".join(styles.keys()) if styles else "（未配置）"
        fallback = self._client.default_style_name() or "default"
        auto = getattr(self.config.gpt_sovits, "auto_style", None)
        auto_desc = "开启" if (auto is not None and auto.enabled) else "关闭"
        help_text = (
            "【GPT-SoVITS 语音插件帮助】\n\n"
            "📝 基本语法：\n"
            "/tts <文本> [-v <风格>]\n\n"
            "🎯 示例：\n"
            "/tts 你好世界\n"
            "/tts 今天真开心 -v 兴奋\n"
            "/gptsovits 用本地语音合成\n\n"
            f"🎤 可用风格：{style_desc}\n"
            f"🧠 自动语气：{auto_desc}"
            f"（不指定风格时按文本内容自动选择，未命中则用 {fallback}）\n"
            f"🌏 多语言：检测到日语等语言时自动切换到对应语言模型（若已配置）"
        )
        await self.ctx.send.text(help_text, stream_id)

    def _project_root(self) -> str:
        """推断 MaiBot 项目根目录（plugins/<本插件>/plugin.py 向上三级）。"""

        return str(Path(__file__).resolve().parents[2])


def create_plugin() -> GPTSovitsVoicePlugin:
    """创建 GPT-SoVITS 语音插件实例（SDK 入口）。"""

    return GPTSovitsVoicePlugin()
