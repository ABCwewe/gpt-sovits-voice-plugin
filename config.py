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

    enabled: bool = Field(
        default=True,
        description="是否启用插件",
        json_schema_extra={
            "label": "启用插件",
            "hint": "关闭后插件不再响应 /tts 命令、工具调用和概率语音，已加载的资源会释放。",
        },
    )
    config_version: str = Field(
        default="1.0.0",
        description="配置版本",
        json_schema_extra={
            "label": "配置版本",
            "hint": "插件内部用于识别配置结构版本，升级时自动迁移。正常使用无需修改。",
        },
    )


class GeneralConfig(PluginConfigBase):
    """通用设置。"""

    __ui_label__ = "通用"
    __ui_icon__ = "settings"
    __ui_order__ = 1

    min_text_length: int = Field(
        default=2,
        description="文本最小长度，过短（如单字）合成效果差，低于此值拒绝合成",
        json_schema_extra={
            "label": "文本最小长度",
            "hint": (
                "低于该字数直接拒绝合成。单字缺少上下文，GPT-SoVITS 容易读成杂音或吞字，"
                "建议保持 2 以上。"
            ),
            "placeholder": "2",
        },
    )
    max_text_length: int = Field(
        default=200,
        description="文本最大长度，超过有超时风险，拒绝合成",
        json_schema_extra={
            "label": "文本最大长度",
            "hint": (
                "超过该字数拒绝合成。文本越长合成耗时越久，容易触发 timeout。"
                "另外 QQ 语音本身有长度限制，过长会被平台截断。建议 100~300。"
            ),
            "placeholder": "200",
        },
    )
    use_base64_audio: bool = Field(
        default=False,
        description=(
            "true=base64 走 IPC；false=文件路径 voiceurl。"
            "base64 模式会把整段音频编码后塞进消息体，"
            "该内容会作为文本进入 MaiBot 上下文，单条可达数十万 token，"
            "极易造成费用暴涨，除非明确了解后果否则请保持 false"
        ),
        json_schema_extra={
            "label": "base64 发送音频",
            "hint": (
                "⚠️ 强烈建议保持关闭。开启后音频会以 base64 编码塞进消息体，"
                "MaiBot 会把它当普通文本读入上下文并按 token 计费——"
                "一条十几秒的语音约 30 万字符，单次请求可消耗数十万 token，"
                "且历史裁剪按条数计算不会移除它，会在每轮请求中反复重发，导致费用暴涨。"
                "关闭时走文件路径（voiceurl），只传路径，需要 adapter 支持该消息段。"
            ),
        },
    )
    audio_output_dir: str = Field(
        default="data/plugins/github.lbcheng1145.gpt-sovits-voice-plugin",
        description="音频输出目录（相对项目根目录或绝对路径），默认存到主程序推荐的 data/plugins 下",
        json_schema_extra={
            "label": "音频输出目录",
            "hint": (
                "文件模式下临时音频的落盘位置，支持相对项目根目录或绝对路径。"
                "留空则优先用 Runner 分配的插件运行时目录。"
                "音频发出后会延迟 180 秒自动删除，不会长期占用磁盘。"
            ),
            "placeholder": "data/plugins/github.lbcheng1145.gpt-sovits-voice-plugin",
        },
    )
    send_error_messages: bool = Field(
        default=True,
        description="合成失败时是否向用户发送错误提示",
        json_schema_extra={
            "label": "发送错误提示",
            "hint": (
                "开启后合成失败会在聊天里提示原因（文本过长、服务未启动等），方便排查。"
                "关闭则静默失败，只写日志，适合不想打扰群友的场景。"
            ),
        },
    )


class GPTSovitsStyle(PluginConfigBase):
    """GPT-SoVITS 单个语音风格配置。"""

    name: str = Field(
        default="default",
        description="风格名称",
        json_schema_extra={
            "label": "风格名称",
            "hint": "调用时用来指定风格的标识，需与自动语气规则、/tts -v 的值对应。",
            "placeholder": "default",
        },
    )
    refer_wav: str = Field(
        default="",
        description="参考音频路径",
        json_schema_extra={
            "label": "参考音频",
            "hint": (
                "必填。用于克隆音色的参考音频，建议 3~10 秒、干净无背景音。"
                "末尾若是突然截断，合成时最后一个字也容易含糊，"
                "建议选一段自然收尾的录音。"
            ),
            "placeholder": "C:/path/to/reference.wav",
        },
    )
    prompt_text: str = Field(
        default="",
        description="参考音频对应的文本",
        json_schema_extra={
            "label": "参考音频文本",
            "hint": (
                "必填。必须与参考音频里说的话逐字一致，标点可省略。"
                "填错会导致合成的音频音色漂移或整段静音。"
            ),
            "placeholder": "参考音频里说的那句话",
        },
    )
    prompt_language: str = Field(
        default="zh",
        description="参考音频语言",
        json_schema_extra={
            "label": "参考音频语言",
            "hint": "参考音频所用的语言代码：zh / ja / en / ko / yue，需与音频内容一致。",
            "placeholder": "zh",
        },
    )
    gpt_weights: str = Field(
        default="",
        description="GPT 模型权重路径（可选，动态切模型）",
        json_schema_extra={
            "label": "GPT 权重路径",
            "hint": (
                "可选。留空表示沿用 GPT-SoVITS 服务当前已加载的权重。"
                "填写后每次合成前会调用接口切换，多风格共用同一服务时使用。"
            ),
            "placeholder": "留空则用服务当前权重",
        },
    )
    sovits_weights: str = Field(
        default="",
        description="SoVITS 模型权重路径（可选）",
        json_schema_extra={
            "label": "SoVITS 权重路径",
            "hint": "可选。与 GPT 权重配套使用，留空则沿用服务当前已加载的权重。",
            "placeholder": "留空则用服务当前权重",
        },
    )
    languages: List[str] = Field(
        default_factory=list,
        description=(
            "该风格适用的语言列表（zh/ja/en/ko/yue），留空表示通用；"
            "非空时按检测到的语言优先匹配"
        ),
        json_schema_extra={
            "label": "适用语言",
            "hint": (
                "留空 = 所有语言都用这个风格。"
                "填写后按检测到的语言优先匹配，适合为不同语种配不同音色。"
            ),
            "placeholder": "如 zh, ja；留空表示通用",
        },
    )
    language: str = Field(
        default="",
        description=(
            "强制指定合成语言，跳过自动检测（如 zh/ja/en/ko/yue）。"
            "留空则自动检测。适用于纯汉字日文等无法自动区分的场景"
        ),
        json_schema_extra={
            "label": "强制合成语言",
            "hint": (
                "留空 = 自动检测。日文里有大量汉字，自动检测可能误判为中文，"
                "这种情况可在此固定为 ja。"
            ),
            "placeholder": "留空则自动检测",
        },
    )


class AutoStyleRule(PluginConfigBase):
    """自动语气匹配规则。"""

    style: str = Field(
        default="",
        description="命中后使用的风格名",
        json_schema_extra={
            "label": "目标风格",
            "hint": "规则命中时切换到该风格，值需与 [[gpt_sovits.styles]] 的 name 一致。",
            "placeholder": "如 兴奋",
        },
    )
    pattern: str = Field(
        default="",
        description="正则表达式",
        json_schema_extra={
            "label": "匹配正则",
            "hint": "对回复文本做正则匹配，命中即采用该风格。支持 Python re 语法。",
            "placeholder": r"如 [！!]{2,}",
        },
    )
    weight: int = Field(
        default=50,
        description="命中权重，多个规则同时命中时取最高",
        json_schema_extra={
            "label": "命中权重",
            "hint": "多条规则同时命中时，累加同一风格的权重后取最高者。默认 50。",
            "placeholder": "50",
        },
    )


class AutoStyleConfig(PluginConfigBase):
    """自动语气选择配置。"""

    __ui_label__ = "自动语气"
    __ui_icon__ = "wand"
    __ui_order__ = 3

    enabled: bool = Field(
        default=True,
        description="开启后，调用方未指定 style 时按文本内容自动选择风格",
        json_schema_extra={
            "label": "启用自动语气",
            "hint": (
                "开启后按文本内容（感叹号、语气词等）自动挑风格，更有表现力。"
                "关闭则始终使用下方的兜底风格。"
            ),
        },
    )
    fallback: str = Field(
        default="default",
        description="所有规则都未命中时使用的风格",
        json_schema_extra={
            "label": "兜底风格",
            "hint": "没有规则命中时使用的风格名，需与已配置的 styles 名称一致。",
            "placeholder": "default",
        },
    )
    rules: List[AutoStyleRule] = Field(
        default_factory=list,
        description="自定义匹配规则（留空则使用插件内置规则）",
        json_schema_extra={
            "label": "自定义匹配规则",
            "hint": (
                "留空 = 使用插件内置规则（兴奋/吐槽/撒娇等）。"
                "填写后内置规则仍会生效，自定义规则优先参与评分。"
            ),
        },
    )
    number_style: str = Field(
        default="",
        description=(
            "纯数字文本使用的风格（如价格、时间、电话号码）。"
            "留空则按正常流程处理。播报数字与说话的韵律差异较大，"
            "指定专用风格往往效果更好"
        ),
        json_schema_extra={
            "label": "纯数字专用风格",
            "hint": (
                "文本全是数字时（如「2333」「123」）改用该风格，值需与 styles 的 name 一致。"
                "留空则按常规语气规则处理。"
            ),
            "placeholder": "留空则不启用，如 数字",
        },
    )
    number_style_include_chinese: bool = Field(
        default=True,
        description="判断纯数字时，是否把中文数字（一二三、十百千等）也算作数字",
        json_schema_extra={
            "label": "中文数字也算数字",
            "hint": "开启后「一二三」也会被判定为纯数字文本，从而走纯数字专用风格。",
        },
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
        json_schema_extra={
            "label": "启用概率语音",
            "hint": (
                "开启后机器人会自己决定偶尔用语音回复，更像真人。"
                "关闭时只有 LLM 主动调用工具或用户发 /tts 才会合成。"
            ),
        },
    )
    probability: float = Field(
        default=0.3,
        description="自动转语音的概率（0~1），0.3 表示约三成回复会变成语音",
        json_schema_extra={
            "label": "转语音概率",
            "hint": (
                "0~1 之间。0.3 表示约三成回复转成语音。"
                "太高会显得吵闹且明显增加合成开销，建议 0.1~0.3。"
            ),
            "placeholder": "0.3",
        },
    )
    decision_ttl: float = Field(
        default=8.0,
        description=(
            "同一条回复在多少秒内复用同一个随机结果。"
            "避免一条长回复被拆成多段时语音、文字混发"
        ),
        json_schema_extra={
            "label": "决策缓存时长（秒）",
            "hint": (
                "一条回复常被拆成多段发送。该时长内沿用第一次的随机结果，"
                "保证整段回复要么全是语音、要么全是文字，不会中途切换。"
            ),
            "placeholder": "8",
        },
    )
    min_text_length: int = Field(
        default=2,
        description="短于此长度的回复不转语音（如「嗯」「好」）",
        json_schema_extra={
            "label": "最短字数",
            "hint": "短于该字数的回复不转语音。太短的话合成出来很突兀，建议 2 以上。",
            "placeholder": "2",
        },
    )
    max_text_length: int = Field(
        default=80,
        description="长于此长度的回复不转语音（长文用语音不好听，也容易超时）",
        json_schema_extra={
            "label": "最长字数",
            "hint": (
                "长于该字数的回复不转语音。语音念长文既费时又难听，"
                "而且容易触发超时。默认 80。"
            ),
            "placeholder": "80",
        },
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

    server: str = Field(
        default="http://127.0.0.1:9880",
        description="GPT-SoVITS 服务地址",
        json_schema_extra={
            "label": "服务地址",
            "hint": (
                "GPT-SoVITS 的 API 地址，通常是 api_v2.py 启动后监听的端口。"
                "本机默认 9880；换机器或改端口时同步修改这里。"
                "填错会提示网络错误，请先用浏览器打开 /docs 确认服务已启动。"
            ),
            "placeholder": "http://127.0.0.1:9880",
        },
    )
    timeout: int = Field(
        default=60,
        description="请求超时（秒）",
        json_schema_extra={
            "label": "请求超时（秒）",
            "hint": (
                "单次合成请求的最长等待时间。文本越长、模型越大耗时越久，"
                "长文本或首次加载模型时可能需要放宽到 90~120。"
                "超时不会重试，只会返回失败提示。"
            ),
            "placeholder": "60",
        },
    )
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
    speed_factor: float = Field(
        default=1.0,
        description="语速倍率，小于 1 更慢（0.9 = 慢 10%）",
        json_schema_extra={
            "label": "语速倍率",
            "hint": (
                "1.0 = 原始语速；小于 1 更慢（0.9 = 慢 10%），大于 1 更快。"
                "注意：放慢会让音素边界更明显，若参考音频本身收尾仓促，"
                "末尾字听起来会更含糊。建议范围 0.85~1.15。"
            ),
            "placeholder": "1.0",
        },
    )
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
    text_split_method: str = Field(
        default="cut1",
        description="文本切分方式（cut0~cut5）",
        json_schema_extra={
            "label": "文本切分方式",
            "x-widget": "select",
            "options": ["cut0", "cut1", "cut2", "cut3", "cut4", "cut5"],
            "x-option-labels": {
                "cut0": "cut0 不切分（最连贯，长文本易出错）",
                "cut1": "cut1 四句一切（推荐）",
                "cut2": "cut2 按标点切且块更长",
                "cut3": "cut3 按标点切",
                "cut4": "cut4 按标点切且块较短",
                "cut5": "cut5 标点+长度混合（GPT-SoVITS 默认）",
            },
            "hint": (
                "决定长文本如何切成小块再合成。切块越碎，块与块交界处越容易产生"
                "硬起音和吞字——开头第一个字听不清通常就是第一块太短导致的。\n"
                "cut0 = 整段合成，最连贯，但长文本容易显存不足或超时，建议配合 80 字以内的文本。\n"
                "cut1 = 按句号等标点切，每块尽量凑够 4 句，交界处最少，推荐。\n"
                "cut5 = GPT-SoVITS 官方默认，标点与长度混合切分，块较碎。\n"
                "实测同一段文本：cut0 2.28s / cut1 2.36s / cut5 3.02s，cut5 多出的时长"
                "主要来自切块处插入的冗余。"
            ),
        },
    )
    min_chunk_length: int = Field(
        default=16,
        description="切块的最小长度（字），过短的块会被合并",
        json_schema_extra={
            "label": "最小切块长度",
            "hint": (
                "切分后短于该长度的块会与相邻块合并。块太短时开头起音来不及建立，"
                "听感上就是第一个字含糊。16 是官方默认；开头吞字明显时可调到 24~32。"
            ),
            "placeholder": "16",
        },
    )
    overlap_length: int = Field(
        default=0,
        description="切块之间的重叠字数",
        json_schema_extra={
            "label": "切块重叠字数",
            "hint": (
                "相邻块在交界处重复的字数，用来减少接缝处的断裂感。"
                "官方默认 2，但重复会导致听感上有个字被念两遍。"
                "如果听到交界处有重复字，调为 0。"
            ),
            "placeholder": "0",
        },
    )
    top_k: int = Field(
        default=15,
        description="采样 top_k",
        json_schema_extra={
            "label": "top_k",
            "hint": "采样时只保留概率最高的前 k 个候选。数值越低越稳定但越平淡，默认 15。",
            "placeholder": "15",
        },
    )
    top_p: float = Field(
        default=1.0,
        description="采样 top_p",
        json_schema_extra={
            "label": "top_p",
            "hint": "核采样阈值，与 top_k 配合控制随机性。默认 1.0 表示不额外截断。",
            "placeholder": "1.0",
        },
    )
    temperature: float = Field(
        default=1.0,
        description="采样温度",
        json_schema_extra={
            "label": "温度",
            "hint": "越高越有变化但越容易跑调，越低越平稳但可能呆板。默认 1.0。",
            "placeholder": "1.0",
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
