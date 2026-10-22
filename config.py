"""插件 Pydantic 配置模型。"""

from typing import List

from maibot_sdk import Field, PluginConfigBase


class PluginSectionConfig(PluginConfigBase):
    """插件基础配置。"""

    __ui_label__ = "插件"
    __ui_icon__ = "package"
    __ui_order__ = 0

    enabled: bool = Field(default=True, description="是否启用插件")
    config_version: str = Field(default="1.0.0", description="配置版本")


class GeneralConfig(PluginConfigBase):
    """通用设置。"""

    __ui_label__ = "通用"
    __ui_icon__ = "settings"
    __ui_order__ = 1

    min_text_length: int = Field(default=2, description="文本最小长度，过短（如单字）合成效果差，低于此值拒绝合成")
    max_text_length: int = Field(default=200, description="文本最大长度，超过有超时风险，拒绝合成")
    use_base64_audio: bool = Field(default=True, description="true=base64 走 IPC；false=文件路径 voiceurl")
    audio_output_dir: str = Field(
        default="data/plugins/github.lbcheng1145.gpt-sovits-voice-plugin",
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
    languages: List[str] = Field(
        default_factory=list,
        description=(
            "该风格适用的语言列表（zh/ja/en/ko/yue），留空表示通用；"
            "非空时按检测到的语言优先匹配"
        ),
    )
    language: str = Field(
        default="",
        description=(
            "强制指定合成语言，跳过自动检测（如 zh/ja/en/ko/yue）。"
            "留空则自动检测。适用于纯汉字日文等无法自动区分的场景"
        ),
    )


class AutoStyleRule(PluginConfigBase):
    """自动语气匹配规则。"""

    style: str = Field(default="", description="命中后使用的风格名")
    pattern: str = Field(default="", description="正则表达式")
    weight: int = Field(default=50, description="命中权重，多个规则同时命中时取最高")


class AutoStyleConfig(PluginConfigBase):
    """自动语气选择配置。"""

    __ui_label__ = "自动语气"
    __ui_icon__ = "wand"
    __ui_order__ = 3

    enabled: bool = Field(
        default=True,
        description="开启后，调用方未指定 style 时按文本内容自动选择语气",
    )
    fallback: str = Field(default="default", description="所有规则都未命中时使用的风格")
    rules: List[AutoStyleRule] = Field(
        default_factory=list,
        description="自定义匹配规则（留空则使用插件内置规则）",
    )


class GPTSovitsConfig(PluginConfigBase):
    """GPT-SoVITS 后端配置。"""

    __ui_label__ = "GPT-SoVITS"
    __ui_icon__ = "server"
    __ui_order__ = 2

    server: str = Field(default="http://127.0.0.1:9880", description="GPT-SoVITS 服务地址")
    timeout: int = Field(default=60, description="请求超时（秒）")
    speed_factor: float = Field(default=1.0, description="语速倍率，小于 1 更慢（0.9 = 慢 10%）")
    styles: List[GPTSovitsStyle] = Field(
        default_factory=lambda: [GPTSovitsStyle()],
        description="语音风格配置列表（每个风格独立完整参数）",
    )
    auto_style: AutoStyleConfig = Field(
        default_factory=AutoStyleConfig,
        description="根据文本内容自动选择语气",
    )


class PluginConfig(PluginConfigBase):
    """插件总配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    gpt_sovits: GPTSovitsConfig = Field(default_factory=GPTSovitsConfig)
