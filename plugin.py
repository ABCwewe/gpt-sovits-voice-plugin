"""GPT-SoVITS 语音合成插件（MaiBot SDK 2.x）

基于本地 GPT-SoVITS 服务合成语音，支持多种语音风格：
- LLM 通过 ``tts_voice`` 工具自主调用；
- 用户通过 ``/tts`` 命令手动触发；
- 风格在 ``config.toml`` 的 ``[[gpt_sovits.styles]]`` 中配置，每个风格独立
  拥有参考音频、提示文本、模型权重等全部参数；风格不存在或未指定时回退 default。

Version: 1.0.0
"""

import asyncio
import base64
import logging
import os
import uuid
from typing import Any, Dict, List, Optional, Tuple

import aiohttp

from maibot_sdk import Command, Field, MaiBotPlugin, PluginConfigBase, Tool
from maibot_sdk.types import ToolParameterInfo, ToolParamType

from .utils import detect_language, split_sentences

logger = logging.getLogger("plugin.gpt_sovits_voice")

# 音频数据最小有效大小（字节），小于该值视为合成失败
MIN_AUDIO_SIZE = 100

# 文件路径模式发送时，临时音频的延迟清理时间（秒），给 adapter 上传留足时间
CLEANUP_DELAY = 180

# 工具 style 参数的可选枚举（与 config.toml 中的风格保持一致；执行时以配置为准）
STYLE_ENUM = ["default", "流萤_活泼", "流萤_伤感", "流萤_疑惑", "流萤_平静"]


class GeneralConfig(PluginConfigBase):
    """通用设置。"""

    __ui_label__ = "通用"
    __ui_icon__ = "settings"
    __ui_order__ = 1

    max_text_length: int = Field(default=200, description="文本最大长度，超过则降级为文字回复")
    split_sentences: bool = Field(default=True, description="长文本分句逐段发送")
    split_delay: float = Field(default=0.3, description="分句发送时每条语音之间的延迟（秒）")
    use_base64_audio: bool = Field(default=True, description="true=base64 走 IPC；false=文件路径 voiceurl")
    audio_output_dir: str = Field(default="", description="音频输出目录，留空使用项目根目录")
    send_error_messages: bool = Field(default=True, description="合成失败时是否向用户发送错误提示")


class GPTSovitsStyle(PluginConfigBase):
    """GPT-SoVITS 单个语音风格配置。"""

    name: str = Field(default="default", description="风格名称")
    refer_wav: str = Field(default="", description="参考音频路径")
    prompt_text: str = Field(default="", description="参考音频对应的文本")
    prompt_language: str = Field(default="zh", description="参考音频语言")
    gpt_weights: str = Field(default="", description="GPT 模型权重路径（可选，动态切模型）")
    sovits_weights: str = Field(default="", description="SoVITS 模型权重路径（可选）")


class GPTSovitsConfig(PluginConfigBase):
    """GPT-SoVITS 后端配置。"""

    __ui_label__ = "GPT-SoVITS"
    __ui_icon__ = "server"
    __ui_order__ = 2

    server: str = Field(default="http://127.0.0.1:9880", description="GPT-SoVITS 服务地址")
    timeout: int = Field(default=60, description="请求超时（秒）")
    styles: List[GPTSovitsStyle] = Field(
        default_factory=lambda: [GPTSovitsStyle()],
        description="语音风格配置列表（每个风格独立完整参数）",
    )


class PluginConfig(PluginConfigBase):
    """插件总配置。"""

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    gpt_sovits: GPTSovitsConfig = Field(default_factory=GPTSovitsConfig)


class GPTSovitsVoicePlugin(MaiBotPlugin):
    """GPT-SoVITS 语音合成插件。

    ``tts_voice`` 工具由 LLM 自主决定是否调用；
    ``/tts`` 命令由用户手动触发，均支持 ``-v <风格>`` 指定语音风格。
    """

    config_model = PluginConfig

    # 类变量：记录当前已加载的模型权重，避免重复切换
    _current_gpt_weights: Optional[str] = None
    _current_sovits_weights: Optional[str] = None

    def __init__(self) -> None:
        super().__init__()
        self._session: Optional[aiohttp.ClientSession] = None

    # ─── 生命周期 ────────────────────────────────────────────────────────

    async def on_load(self) -> None:
        """插件加载：创建 HTTP 会话并打印风格概况。"""

        timeout = max(self.config.gpt_sovits.timeout, 10)
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout),
        )
        style_names = [style.name for style in self.config.gpt_sovits.styles if style.name]
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

            style_name = self._resolve_style(style, log_prefix)

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

        audio_data = await self._synthesize(text, style_name, log_prefix)
        if audio_data is None:
            return False, f"合成失败（风格: {style_name}）"
        return await self._send_audio(audio_data, style_name, stream_id, log_prefix)

    # ─── GPT-SoVITS 调用 ────────────────────────────────────────────────

    def _styles(self) -> Dict[str, GPTSovitsStyle]:
        """将配置中的风格列表转换为 ``{name: style}`` 映射。"""

        return {style.name: style for style in self.config.gpt_sovits.styles if style.name}

    def _resolve_style(self, style: str, log_prefix: str) -> str:
        """解析最终风格：精确命中配置则使用，否则（缺失/错误参数）回退 default。"""

        styles = self._styles()
        if style and style in styles:
            return style
        if style:
            self.ctx.logger.warning(
                "%s 风格 '%s' 不存在，回退 default", log_prefix, style,
            )
        return "default"

    async def _switch_model(
        self,
        server: str,
        gpt_weights: str,
        sovits_weights: str,
    ) -> Tuple[bool, str]:
        """按风格切换 GPT-SoVITS 模型权重（已加载的权重不重复切换）。"""

        # 切换 GPT 权重
        if gpt_weights and gpt_weights != GPTSovitsVoicePlugin._current_gpt_weights:
            gpt_url = f"{server.rstrip('/')}/set_gpt_weights?weights_path={gpt_weights}"
            async with self._session.get(gpt_url) as response:
                if response.status != 200:
                    return False, f"GPT模型切换失败: {await response.text()}"
            GPTSovitsVoicePlugin._current_gpt_weights = gpt_weights
            logger.info("已切换 GPT 模型: %s", gpt_weights)

        # 切换 SoVITS 权重
        if sovits_weights and sovits_weights != GPTSovitsVoicePlugin._current_sovits_weights:
            sovits_url = f"{server.rstrip('/')}/set_sovits_weights?weights_path={sovits_weights}"
            async with self._session.get(sovits_url) as response:
                if response.status != 200:
                    return False, f"SoVITS模型切换失败: {await response.text()}"
            GPTSovitsVoicePlugin._current_sovits_weights = sovits_weights
            logger.info("已切换 SoVITS 模型: %s", sovits_weights)

        return True, ""

    async def _synthesize(
        self,
        text: str,
        style_name: str,
        log_prefix: str,
    ) -> Optional[bytes]:
        """调用 GPT-SoVITS ``/tts`` 接口合成音频，失败返回 ``None``。"""

        styles = self._styles()
        style = styles.get(style_name) or styles.get("default")
        if style is None:
            self.ctx.logger.error("%s 找不到可用风格配置", log_prefix)
            return None

        cfg = self.config.gpt_sovits

        # 风格配置了模型权重时，先切换模型
        if style.gpt_weights or style.sovits_weights:
            switch_ok, switch_error = await self._switch_model(
                cfg.server, style.gpt_weights, style.sovits_weights,
            )
            if not switch_ok:
                self.ctx.logger.error("%s %s", log_prefix, switch_error)
                return None

        request_data = {
            "text": text,
            "text_lang": detect_language(text),
            "ref_audio_path": style.refer_wav,
            "prompt_text": style.prompt_text,
            "prompt_lang": style.prompt_language,
        }
        tts_url = f"{cfg.server.rstrip('/')}/tts"

        self.ctx.logger.info(
            "%s GPT-SoVITS 请求: text='%s...', style=%s",
            log_prefix, text[:30], style_name,
        )

        async with self._session.post(tts_url, json=request_data) as response:
            if response.status != 200:
                error_text = (await response.text())[:200]
                self.ctx.logger.error(
                    "%s GPT-SoVITS API 失败[%s]: %s", log_prefix, response.status, error_text,
                )
                return None
            audio_data = await response.read()

        # 验证音频数据
        if not audio_data or len(audio_data) < MIN_AUDIO_SIZE:
            self.ctx.logger.error(
                "%s 音频数据无效（%s 字节）", log_prefix, len(audio_data or b""),
            )
            return None

        return audio_data

    # ─── 音频发送 ────────────────────────────────────────────────────────

    async def _send_audio(
        self,
        audio_data: bytes,
        style_name: str,
        stream_id: str,
        log_prefix: str,
    ) -> Tuple[bool, str]:
        """发送语音：base64 模式直接走 IPC，文件模式写入本地后发送 voiceurl。"""

        if self.config.general.use_base64_audio:
            audio_b64 = base64.b64encode(audio_data).decode("utf-8")
            await self.ctx.send.custom("voice", audio_b64, stream_id)
            logger.info("%s 语音已发送（base64，%s 字节，风格=%s）", log_prefix, len(audio_data), style_name)
            return True, f"已发送语音（风格: {style_name}）"

        audio_path = await self._save_audio_file(audio_data)
        if audio_path is None:
            return False, "保存音频文件失败"

        await self.ctx.send.custom("voiceurl", audio_path, stream_id)
        logger.info("%s 语音已发送（文件，%s，风格=%s）", log_prefix, audio_path, style_name)
        # 延迟清理：给 adapter 上传到 QQ 留足时间
        asyncio.create_task(self._cleanup_audio_async(audio_path))
        return True, f"已发送语音（风格: {style_name}）"

    async def _save_audio_file(self, audio_data: bytes) -> Optional[str]:
        """在后台线程写入音频文件，返回文件路径。"""

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._write_audio_sync, audio_data)

    def _write_audio_sync(self, audio_data: bytes) -> Optional[str]:
        """同步写入音频文件（内部方法，在后台线程执行）。"""

        project_root = self._project_root()
        output_dir = self.config.general.audio_output_dir.strip()
        if output_dir:
            output_dir = output_dir if os.path.isabs(output_dir) else os.path.join(project_root, output_dir)
            try:
                os.makedirs(output_dir, exist_ok=True)
            except OSError as exc:
                logger.error("创建输出目录失败 %s: %s", output_dir, exc)
                return None
        else:
            output_dir = project_root

        filename = f"tts_gpt_sovits_{uuid.uuid4().hex[:12]}.wav"
        path = os.path.join(output_dir, filename)
        try:
            with open(path, "wb") as f:
                f.write(audio_data)
            return path
        except OSError as exc:
            logger.error("写入音频文件失败 %s: %s", path, exc)
            return None

    async def _cleanup_audio_async(self, path: str) -> None:
        """延迟删除临时音频文件。"""

        await asyncio.sleep(CLEANUP_DELAY)
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._cleanup_audio_sync, path)

    @staticmethod
    def _cleanup_audio_sync(path: str) -> None:
        """同步删除文件（内部方法，在后台线程执行）。"""

        try:
            if path and os.path.exists(path):
                os.remove(path)
        except OSError as exc:
            logger.debug("清理临时音频失败 %s: %s", path, exc)

    def _project_root(self) -> str:
        """推断 MaiBot 项目根目录（plugins/<本插件>/plugin.py 向上三级）。"""

        return os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )

    # ─── 辅助 ────────────────────────────────────────────────────────────

    async def _send_error(self, stream_id: str, message: str) -> None:
        """根据配置决定是否向用户发送错误提示。"""

        if self.config.general.send_error_messages:
            await self.ctx.send.text(message, stream_id)

    async def _send_help(self, stream_id: str) -> None:
        """发送帮助信息。"""

        styles = self._styles()
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


def create_plugin() -> GPTSovitsVoicePlugin:
    """创建 GPT-SoVITS 语音插件实例（SDK 入口）。"""

    return GPTSovitsVoicePlugin()
