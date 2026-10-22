"""插件 Pydantic 配置模型。"""

from typing import Any, List, Literal

from maibot_sdk import Field, PluginConfigBase
from pydantic import field_validator

# 底模版本可选值。与 utils.MODEL_VERSION_CHOICES 保持一致，
# 这里用 Literal 声明是为了让 WebUI 渲染成下拉框而不是文本框。
ModelVersion = Literal[
    "auto",
    "v1",
    "v2",
    "v2Pro",
    "v2ProPlus",
    "v3",
    "v4",
    "v5dev",
    "v5turbo",
]

# 版本名的小写 → 标准写法。手写 toml 时用户很容易写成 v2proplus / V2PROPLUS，
# 直接交给 Literal 会抛校验错误导致整份配置加载失败，这里先归一化。
_VERSION_ALIAS = {v.lower(): v for v in ("auto", "v1", "v2", "v2Pro", "v2ProPlus", "v3", "v4", "v5dev", "v5turbo")}


def _normalize_model_version(value: Any) -> Any:
    """把版本名归一化为标准写法，无法识别时原样返回交由 Literal 报错。"""

    if not isinstance(value, str):
        return value
    return _VERSION_ALIAS.get(value.strip().lower(), value)


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
        description="开启后，调用方未指定 style 时按文本内容自动选择风格",
    )
    fallback: str = Field(default="default", description="所有规则都未命中时使用的风格")
    rules: List[AutoStyleRule] = Field(
        default_factory=list,
        description="自定义匹配规则（留空则使用插件内置规则）",
    )
    number_style: str = Field(
        default="",
        description=(
            "纯数字文本使用的风格（如价格、时间、电话号码）。"
            "留空则按正常流程处理。播报数字与说话的韵律差异较大，"
            "指定专用风格往往效果更好"
        ),
    )
    number_style_include_chinese: bool = Field(
        default=True,
        description="判断纯数字时，是否把中文数字（一二三、十百千等）也算作数字",
    )


class RandomVoiceConfig(PluginConfigBase):
    """概率语音配置：按概率把普通文字回复自动转成语音。"""

    __ui_label__ = "概率语音"
    __ui_icon__ = "dice"
    __ui_order__ = 4

    enabled: bool = Field(
        default=False,
        description=(
            "开启后，机器人的普通文字回复会按 probability 概率自动转成语音发送。"
            "关闭时行为不变，仅响应 LLM 工具调用与 /tts 命令"
        ),
    )
    probability: float = Field(
        default=0.3,
        description="自动转语音的概率（0~1），0.3 表示约三成回复会变成语音",
    )
    decision_ttl: float = Field(
        default=8.0,
        description=(
            "同一条回复在多少秒内复用同一个随机结果。"
            "避免一条长回复被拆成多段时语音、文字混发"
        ),
    )
    min_text_length: int = Field(
        default=2,
        description="短于此长度的回复不转语音（如「嗯」「好」）",
    )
    max_text_length: int = Field(
        default=80,
        description="长于此长度的回复不转语音（长文用语音不好听，也容易超时）",
    )
    skip_patterns: List[str] = Field(
        default_factory=list,
        description=(
            "命中这些正则的文本不转语音，如代码块、网址、表格。留空则使用内置规则"
        ),
        json_schema_extra={
            "label": "不转语音的规则（正则）",
            "hint": (
                "留空 = 使用内置规则（自动跳过代码块、网址、表格、列表、HTML、Markdown 链接）。"
                "注意：一旦填写，内置规则会被完全替换，不会追加。"
            ),
            "placeholder": r"例如 ``` 或 https?://\S+",
        },
    )
    style: str = Field(
        default="",
        description="自动转语音时使用的风格，留空则走自动语气选择",
        json_schema_extra={
            "label": "随机语音使用的风格",
            "hint": (
                "留空 = 按文本内容自动判断语气（推荐）。"
                "填写则固定使用该风格，值需与 [[gpt_sovits.styles]] 的 name 一致，如「默认」。"
            ),
            "placeholder": "留空则自动选择，如 默认",
        },
    )


class GPTSovitsConfig(PluginConfigBase):
    """GPT-SoVITS 后端配置。"""

    __ui_label__ = "GPT-SoVITS"
    __ui_icon__ = "server"
    __ui_order__ = 2

    server: str = Field(default="http://127.0.0.1:9880", description="GPT-SoVITS 服务地址")
    timeout: int = Field(default=60, description="请求超时（秒）")
    model_version: ModelVersion = Field(
        default="auto",
        description="底模版本，决定支持哪些语言",
        json_schema_extra={
            "label": "底模版本",
            "hint": (
                "决定插件允许合成哪些语言，选错会导致 400 报错或空白音频。"
                "auto = 按权重路径自动推断（推荐），推断不出时按 v2ProPlus 处理。"
                "v1 = 中英日；其余版本 = 中英日粤。"
                "韩语在所有版本上都不可用（上游缺 eunjeon 依赖会抛内部异常），"
                "俄语、法语、德语、泰语等所有版本都不支持。"
            ),
        },
    )
    speed_factor: float = Field(default=1.0, description="语速倍率，小于 1 更慢（0.9 = 慢 10%）")
    fragment_interval: float = Field(
        default=0.0,
        description="分段之间的静音间隔（秒）",
        json_schema_extra={
            "label": "分段间隔（秒）",
            "hint": (
                "GPT-SoVITS 会把长文本按标点切成多段分别合成，再用纯静音拼接。"
                "间隔越大，句间停顿越空洞，还会在停顿前后产生衰减尾音和硬起音，听感像「颤音」。"
                "建议保持 0"
            ),
            "placeholder": "0",
        },
    )
    styles: List[GPTSovitsStyle] = Field(
        default_factory=lambda: [GPTSovitsStyle()],
        description="语音风格配置列表（每个风格独立完整参数）",
    )
    auto_style: AutoStyleConfig = Field(
        default_factory=AutoStyleConfig,
        description="根据文本内容自动选择语气",
    )

    @field_validator("model_version", mode="before")
    @classmethod
    def _check_model_version(cls, value: Any) -> Any:
        return _normalize_model_version(value)


class PluginConfig(PluginConfigBase):
    """插件总配置。"""

    plugin: PluginSectionConfig = Field(default_factory=PluginSectionConfig)
    general: GeneralConfig = Field(default_factory=GeneralConfig)
    gpt_sovits: GPTSovitsConfig = Field(default_factory=GPTSovitsConfig)
    random_voice: RandomVoiceConfig = Field(
        default_factory=RandomVoiceConfig,
        description="概率语音：按概率自动把文字回复转成语音",
    )
