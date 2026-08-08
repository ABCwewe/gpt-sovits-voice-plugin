"""插件 Pydantic 配置模型。"""

from typing import List

from maibot_sdk import Field, PluginConfigBase


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")


class GeneralConfig(PluginConfigBase):
    """通用设置。"""

    __ui_label__ = "通用"
    __ui_icon__ = "settings"
    __ui_order__ = 1

    max_text_length: int = Field(default=200, description="文本最大长度，超过则降级为文字回复")
    split_sentences: bool = Field(default=True, description="长文本分句逐段发送")
    split_delay: float = Field(default=0.3, description="分句发送时每条语音之间的延迟（秒）")
    use_base64_audio: bool = Field(default=True, description="true=base64 走 IPC；false=文件路径 voiceurl")
    audio_output_dir: str = Field(
        default="data/plugins/github.CGl123.gpt-sovits-voice-plugin",
        description="音频输出目录（相对项目根目录或绝对路径），默认存到主程序推荐的 data/plugins 下",
    )
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

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    gpt_sovits: GPTSovitsConfig = Field(default_factory=GPTSovitsConfig)
