"""GPT-SoVITS 语音合成插件（MaiBot SDK 2.x）

基于本地 GPT-SoVITS 服务合成语音，支持多种语音风格：
- LLM 通过 ``tts_voice`` 工具自主调用；
- 用户通过 ``/tts`` 命令手动触发；
- 风格在 ``config.toml`` 的 ``[[gpt_sovits.styles]]`` 中配置，每个风格独立
  拥有参考音频、提示文本、模型权重等全部参数；
- 未指定风格时按文本内容自动选择语气，并按检测到的语言切换对应模型；
- 可配置概率语音，按比例把普通文字回复自动转成语音。

版本号以 ``_manifest.json`` 为准。
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import asyncio
import logging

import aiohttp

from maibot_sdk import Command, HookHandler, MaiBotPlugin, Tool
from maibot_sdk.components import ErrorPolicy, HookMode, HookOrder
from maibot_sdk.types import ToolParameterInfo, ToolParamType

from .audio import AudioSender
from .config import PluginConfig
from .gpt_sovits import GPTSovitsClient
from .random_voice import RandomVoiceDecider
from .style_router import DEFAULT_RULES, route_style
from .utils import clean_text, ensure_sentence_end, is_number_only

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
        self._decider = RandomVoiceDecider()
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
        self._check_config_ready()

    def _check_config_ready(self) -> None:
        """首次加载时检查配置是否可用，配置缺失时给出明确引导。"""

        if self._client is None:
            return

        broken = []
        for name in self._client.styles():
            err = self._client.validate_style(name)
            if err:
                broken.append((name, err))

        if not broken:
            return

        self.ctx.logger.warning(
            "配置尚未完成：%d/%d 个风格缺少必要参数，合成会失败",
            len(broken), len(self._client.styles()),
        )
        for name, err in broken:
            self.ctx.logger.warning("  风格 %s：%s", name, err)
        self.ctx.logger.warning(
            "请编辑插件目录下的 config.toml 补齐 refer_wav 与 prompt_text；"
            "可参考同目录的 config.example.toml。"
            "另外请确认识别 GPT-SoVITS 服务已在 %s 启动",
            self.config.gpt_sovits.server,
        )

    async def on_unload(self) -> None:
        """插件卸载：关闭 HTTP 会话并清理未到期的临时音频。"""

        if self._audio is not None:
            try:
                await self._audio.close()
            except Exception as exc:
                self.ctx.logger.warning("清理临时音频失败: %s", exc)
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

        # 热重载会替换 AudioSender，旧实例的待清理文件必须在这里收尾，
        # 否则延迟删除任务随对象一起被丢弃，音频文件就永远留在磁盘上。
        if self._audio is not None:
            try:
                await self._audio.close()
            except Exception as exc:
                self.ctx.logger.warning("重建运行时清理临时音频失败: %s", exc)

        timeout = max(self.config.gpt_sovits.timeout, 10)
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout),
        )
        self._client = GPTSovitsClient(self._session, self.config.gpt_sovits)
        self._audio = AudioSender(self.config.general, self._project_root())
        # 配置可能改了权重路径，清空缓存以强制重新切换
        self._client.reset_model_state()
        # 概率语音的随机结果也要清空，否则新旧概率会混在一起
        self._decider.reset()
        self._missing_styles_warned = False

    # ─── 工具：LLM 自主调用 ──────────────────────────────────────────────

    @Tool(
        "tts_voice",
        description="用语音回复，适合简短问候与活泼场景；用户明确要求语音时必须使用。",
        parameters=[
            ToolParameterInfo(
                name="text",
                param_type=ToolParamType.STRING,
                description="要转换为语音的文本（2~100字）",
                required=True,
            ),
            ToolParameterInfo(
                name="style",
                param_type=ToolParamType.STRING,
                description=(
                    "语音风格，可选。不传则按文本内容自动选语气，"
                    "常用：兴奋、吐槽、默认"
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

            # 补齐句末标点：没有标点时最后一段缺少终止信号，
            # 合成出来的最后一个字会被压缩或吞掉。
            # 保留补齐前的文本用于长度校验与错误提示。
            cleaned_text = raw_text
            raw_text = ensure_sentence_end(raw_text)

            min_length = self.config.general.min_text_length
            max_length = self.config.general.max_text_length
            # 用补齐标点前的长度校验：补上的句号是插件自己加的，
            # 不该让刚好等于上限的文本被判为过长，报错字数也更贴近用户输入。
            text_length = len(cleaned_text)

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

            style_name = self._resolve_style_chain(raw_text, style, log_prefix)
            if style_name is None:
                await self._send_error(stream_id, "语音合成失败：配置不完整")
                return False, "配置不完整"

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

    # ─── 概率语音：拦截出站文字并转成语音 ──────────────────────────────

    @staticmethod
    def _extract_plain_text(message: Dict[str, Any]) -> Optional[str]:
        """从出站消息里取出纯文本内容。

        只有「全部组件都是 text」时才返回文本，否则返回 ``None``：
        带图片、语音、文件等组件的消息不应该被改写成语音。
        """

        raw_message = message.get("raw_message")
        if not isinstance(raw_message, list) or not raw_message:
            return None

        parts: List[str] = []
        for item in raw_message:
            if not isinstance(item, dict):
                return None
            # reply 是引用元数据，不影响正文，跳过即可
            if str(item.get("type", "")).strip().lower() == "reply":
                continue
            if item.get("type") != "text":
                return None
            parts.append(str(item.get("data") or ""))

        text = "".join(parts).strip()
        return text or None

    def _build_voice_message(self, message: Dict[str, Any], audio_path: str) -> Dict[str, Any]:
        """把出站消息改写成一条语音消息。

        使用 voiceurl 段携带文件路径：voice 段需要 binary_data_base64，
        把整段音频编码进消息体后会被当作文本读入上下文并按 token 计费，
        单条可达数十万 token。voiceurl 只传路径，适配器自行读取文件。
        """

        updated = dict(message)
        updated["raw_message"] = [
            {
                "type": "voiceurl",
                "data": audio_path,
            }
        ]
        return updated

    @HookHandler(
        "send_service.before_send",
        name="gpt_sovits_random_voice",
        description="按配置概率把机器人的纯文本回复替换成语音",
        mode=HookMode.BLOCKING,
        order=HookOrder.LATE,
        # 与 gpt_sovits.timeout 的默认值同档。
        # 这是阻塞钩子，超时越长出站消息被堵得越久，
        # 所以不跟随用户把 timeout 调大，宁可在极端情况下放弃转语音。
        timeout_ms=60000,
        error_policy=ErrorPolicy.SKIP,
    )
    async def random_voice_hook(self, **kwargs: Any) -> Optional[Dict[str, Any]]:
        """出站拦截：按概率把纯文本回复改成语音。

        返回 ``{"action": "continue", "modified_kwargs": {...}}`` 表示改写消息；
        返回 ``None`` 表示不干预，消息原样发出。

        任何一步判断不通过都直接放行文字，绝不因为插件故障吞掉用户消息。
        """

        config = getattr(self.config, "random_voice", None)
        if config is None or not config.enabled:
            return None

        message = kwargs.get("message")
        if not isinstance(message, dict):
            return None

        text = self._extract_plain_text(message)
        if text is None:
            return None

        # 已经是语音消息的不要再套一层
        raw_message = message.get("raw_message")
        if isinstance(raw_message, list) and any(
            isinstance(item, dict) and item.get("type") == "voice"
            for item in raw_message
        ):
            return None

        stream_id = str(kwargs.get("stream_id") or message.get("session_id") or "").strip()

        suitable, reason = self._decider.text_is_suitable(
            text,
            config.min_text_length,
            config.max_text_length,
            config.skip_patterns or None,
        )
        if not suitable:
            self.ctx.logger.debug("概率语音跳过（%s）：%s", reason, text[:30])
            return None

        should_convert, from_cache = self._decider.should_convert(
            stream_id, config.probability, config.decision_ttl,
        )
        if not should_convert:
            return None

        log_prefix = f"[gpt_sovits_voice][random][{stream_id}]"

        # 合成失败时回退发送原始文字，不让用户收不到回复
        try:
            audio_data = await self._synthesize_for_random(text, config.style, log_prefix)
        except Exception as exc:  # 兜底：无论如何都不能吞掉消息
            self.ctx.logger.error("%s 概率语音合成异常，回退文字：%s", log_prefix, exc)
            return None

        if audio_data is None:
            self.ctx.logger.info("%s 概率语音合成失败，回退文字", log_prefix)
            return None

        # base64 模式会把整段音频编码进消息体，随后作为文本进入 MaiBot 上下文，
        # 单条可达数十万 token；这里统一走文件路径，避免上下文被二进制载荷撑爆。
        if self._audio is None:
            self.ctx.logger.warning("%s 音频发送器未就绪，回退文字", log_prefix)
            return None
        audio_path = await self._audio.save_temp_file(audio_data, self.ctx)
        if audio_path is None:
            self.ctx.logger.warning("%s 音频落盘失败，回退文字", log_prefix)
            return None

        updated_kwargs = dict(kwargs)
        updated_kwargs["message"] = self._build_voice_message(message, audio_path)

        # QQ 不支持「引用 + 语音」组合，会返回成功回执但不实际投递，
        # 所以转语音时必须去掉引用
        if kwargs.get("set_reply"):
            updated_kwargs["set_reply"] = False
            updated_kwargs["reply_message_id"] = ""

        self.ctx.logger.info(
            "%s 已将文字回复转为语音（%d 字节，%s）%s",
            log_prefix, len(audio_data), "缓存命中" if from_cache else "新掷骰",
            f"，文本前 30 字：{text[:30]}",
        )
        return {"action": "continue", "modified_kwargs": updated_kwargs}

    async def _synthesize_for_random(
        self,
        text: str,
        style: str,
        log_prefix: str,
    ) -> Optional[bytes]:
        """概率语音路径的合成：复用与 Tool/Command 相同的选风格逻辑。"""

        if self._client is None:
            return None

        raw_text = ensure_sentence_end(clean_text(text))
        if not raw_text:
            return None

        style_name = self._resolve_style_chain(raw_text, style, log_prefix)
        if style_name is None:
            return None

        return await self._client.synthesize(raw_text, style_name, log_prefix)

    def _resolve_style_chain(
        self,
        raw_text: str,
        style: str,
        log_prefix: str,
    ) -> Optional[str]:
        """按「纯数字 → 自动语气 → 语言路由」的顺序确定最终风格。

        这段逻辑原本内嵌在 ``_handle`` 里，概率语音也要用，
        提出来避免两处实现走偏。
        """

        if self._client is None:
            return None

        style_name = self._client.resolve_style(style, log_prefix)

        number_style_used = False
        if not (style or "").strip():
            auto = getattr(self.config.gpt_sovits, "auto_style", None)
            if auto is not None and auto.number_style:
                if is_number_only(
                    raw_text,
                    allow_chinese=auto.number_style_include_chinese,
                ):
                    available = list(self._client.styles().keys())
                    if auto.number_style in available:
                        style_name = auto.number_style
                        number_style_used = True
                        self.ctx.logger.info(
                            "%s 检测到纯数字，使用风格: %s", log_prefix, style_name,
                        )

        if not (style or "").strip() and not number_style_used:
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
                    self._warn_missing_styles(available)

        lang = self._client.effective_language(style_name, raw_text)
        lang_style = self._client.resolve_by_language(lang, style_name, log_prefix)
        if lang_style != style_name:
            self.ctx.logger.info(
                "%s 语言路由: %s -> %s（语言=%s）",
                log_prefix, style_name, lang_style, lang,
            )
            style_name = lang_style

        config_error = self._client.validate_style(style_name)
        if config_error:
            self.ctx.logger.error("%s %s", log_prefix, config_error)
            return None

        return style_name

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
