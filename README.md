# GPT-SoVITS 语音插件（优化版）

基于本地 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 服务的 MaiBot 文本转语音插件。
支持多种语音风格、按文本内容自动匹配语气、按语言自动切换模型；
LLM 可自主调用工具，用户也可通过 `/tts` 命令手动触发。

> MaiBot SDK 2.x（`MaiBotPlugin` + `@Tool` / `@Command` + Pydantic 配置）。

## ⚠️ 与原版互斥，请勿同时安装

| | 插件 ID |
|---|---|
| **本插件（优化版）** | `github.lbcheng1145.gpt-sovits-voice-plugin` |
| 原版 | `CGl123.gpt-sovits-voice-plugin` |

**两者功能重叠，不能同时启用**，原因：

- **命令冲突**：都注册了 `/tts`、`/voice`、`/gptsovits`，同时安装会导致命令重复注册
- **工具冲突**：都声明了 `tts_voice` 工具，LLM 调用时无法确定用哪个
- **重复合成**：若两个都响应同一条消息，会重复发语音

两者数据目录与插件 ID 已分离（本版为 `data/plugins/github.lbcheng1145.gpt-sovits-voice-plugin`），
但**功能完全重叠，同时启用没有意义**，且会因上面三条产生实际故障。

**安装前请先卸载原版**，或反之。切换时：

1. 在 MaiBot 插件管理中禁用/卸载已安装的那个
2. 安装另一个
3. 重载插件

配置（`config.toml`）不能直接复用，因为两者的字段结构不同（本版新增 `auto_style`、`speed_factor`、`languages`）。
请参照本仓库的 `config.example.toml` 重新配置。

## 来源说明

本项目基于以下项目优化：

- 原仓库：[CGl123/gpt-sovits-voice-plugin](https://github.com/CGl123/gpt-sovits-voice-plugin)
- 原作者：**CGl123**（原始功能与整体设计均出自其手）

在此基础上优化的主要内容：

| 功能 | 说明 |
|---|---|
| 自动语气 | 未指定风格时按文本内容自动选择语气，规则可配置 |
| 多语言路由 | 检测文本语言，自动切换到对应语言的模型与参考音频 |
| 纯数字专用风格 | 纯数字文本可指定独立风格，与日常语气区分 |
| 概率语音 | 按概率自动把文字回复转成语音，无需 LLM 主动调用 |
| 语速控制 | `speed_factor` 全局调节语速 |
| 文本清洗 | 剔除零宽字符、BOM 等不可见字符 |
| 音频校验 | 拦截静音崩坏音频，避免发出空白语音 |
| 配置校验 | 配置缺失时给出明确指引，而非直接抛出服务端错误 |
| 并发保护 | 权重切换与合成加锁，避免并发请求串音色 |

同时修复了若干问题，详见提交记录。

**本版本独立维护**，问题请反馈到[本仓库 Issues](https://github.com/Lbcheng1145/gpt-sovits-voice-plugin/issues)。
若不需要新增功能，建议直接使用原版。

## 功能

- 🎙️ **LLM 自主触发**：planner 判断需要语音回复时自动调用 `tts_voice` 工具
- 🎛️ **多语音风格**：每个风格独立配置参考音频、提示文本、模型权重
- 🧠 **自动语气**：调用方未指定风格时，按文本内容（感叹词、情绪词、标点）自动选择语气
- 🌏 **多语言路由**：检测文本语言，自动切换到对应语言的模型与参考音频
- 🔢 **纯数字专用风格**：纯数字类文本可指定独立风格，适合播报数字/报数场景
- 🎲 **概率语音**：按概率把普通文字回复自动转成语音，让聊天更像真人随手发语音
- ⏩ **语速控制**：`speed_factor` 全局调节合成语速
- 🧹 **文本清洗**：自动剔除零宽字符、BOM 等不可见字符，避免合成失败
- 🛡️ **音频校验**：拦截静音或时长异常的合成结果
- 🧭 **配置校验**：缺失必要参数时给出明确修复指引
- 📝 **用户命令**：`/tts <文本> [-v <风格>]`
- 🔢 **字数限制**：过短（单字）与超长文本自动拒绝，上下限可配置
- 📡 **双发送模式**：base64（IPC）或文件路径（voiceurl）

## 安装

将插件目录放入 MaiBot 的 `plugins/` 下即可（独立 Git 仓库，随插件中心安装或手动放置均可）。

依赖：`aiohttp`（MaiBot 通常已自带）。

### 首次安装后必做

插件首次加载时，MaiBot 会依据配置模型自动生成一份 `config.toml`，
其中 `refer_wav` 与 `prompt_text` 为空，**此时合成会失败**。

请按以下步骤完成配置：

1. 复制模板：`config.example.toml` → `config.toml`（模板含完整注释）
2. 至少填写一个风格的 `refer_wav`（参考音频路径）与 `prompt_text`（该音频对应的文本）
3. 确认 GPT-SoVITS API 服务已启动（默认 `http://127.0.0.1:9880`）
4. 重载插件

插件加载时若检测到配置不完整，会在日志中列出缺少的参数并给出指引；
合成时也会提前拦截并提示具体缺哪一项，而不是抛出服务端的原始错误。

> **参考音频要求**：3~10 秒、单句、干净无背景音乐的人声。
> `prompt_text` 必须与音频内容逐字对应，否则合成会出现杂音或空白。

## 配置

编辑插件目录下的 `config.toml`（完整带注释模板见同目录 `config.example.toml`）：

```toml
[general]
min_text_length = 2          # 文本最小长度，过短（如单字）拒绝合成
max_text_length = 200        # 文本最大长度，超过拒绝合成（超时风险）
use_base64_audio = true      # true=base64 走 IPC；false=文件路径 voiceurl
audio_output_dir = "data/plugins/github.lbcheng1145.gpt-sovits-voice-plugin"  # 文件模式输出目录，可修改
send_error_messages = true   # 合成失败时是否提示用户

[gpt_sovits]
server = "http://127.0.0.1:9880"   # GPT-SoVITS 服务地址
timeout = 60
speed_factor = 1.0                 # 语速倍率，<1 更慢（0.9 = 慢 10%）

# 自动语气（调用方未指定 style 时生效）
[gpt_sovits.auto_style]
enabled = true                     # 关闭后未指定 style 时固定使用 fallback
fallback = "default"               # 所有规则都未命中时使用的风格

# 每个风格独立完整参数（参考音频 / 提示文本 / 语言 / 模型权重均可不同）
[[gpt_sovits.styles]]
name = "default"
languages = ["zh"]                 # 可选：该风格适用的语言，留空表示通用
refer_wav = "/path/to/reference.wav"   # 参考音频
prompt_text = "参考音频对应的文本"
prompt_language = "zh"
gpt_weights = "/path/to/model.ckpt"    # 可选，动态切模型
sovits_weights = "/path/to/model.pth"  # 可选

# 语言变体：命名规则 <风格名>_<语言>
[[gpt_sovits.styles]]
name = "default_ja"
languages = ["ja"]
refer_wav = "/path/to/ja_reference.wav"
prompt_text = "日本語の参照テキスト"
prompt_language = "ja"
gpt_weights = "/path/to/ja_model.ckpt"
sovits_weights = "/path/to/ja_model.pth"

[[gpt_sovits.styles]]
name = "流萤_伤感"
refer_wav = "/path/to/sad.wav"
prompt_text = "伤感的参考文本"
prompt_language = "zh"
```

### 自定义语气规则

内置规则覆盖常见的兴奋、吐槽、撒娇特征。如需自定义，在配置中追加：

```toml
[[gpt_sovits.auto_style.rules]]
style = "兴奋"                     # 命中后使用的风格名
pattern = "(抽到|出货|金了)"        # Python 正则表达式
weight = 60                        # 命中权重，多个规则同时命中时取最高
```

自定义规则**优先于**内置规则；`weight` 越大优先级越高。

## 使用方法

### 命令触发（用户手动）

```
/tts 你好世界                     # 自动选择语气
/tts 今天真开心 -v 流萤_活泼      # 指定风格
/gptsovits 用本地语音合成          # 等价命令
/tts help                         # 查看帮助
```

### 自动触发（LLM 决定）

MaiBot 的 planner 判断需要语音回复时，会调用 `tts_voice` 工具并传入 `text`（必填）与可选的 `style`（风格名）。

- 未指定 `style` 时，插件按文本内容自动选择语气
- 风格不存在时回退到兜底风格（优先 `default`，否则取第一个已配置风格）

### 概率语音（自动随机）

除了等 LLM 主动调用工具，也可以让插件**自己按概率**把普通文字回复转成语音，更像真人随手发语音。

```toml
[random_voice]
enabled = true
probability = 0.3      # 约三成回复会变成语音
decision_ttl = 8.0     # 同一条回复 8 秒内复用同一结果
min_text_length = 2
max_text_length = 80
```

插件通过 `send_service.before_send` 钩子拦截出站消息：掷骰命中后，把纯文本消息改写成语音组件再发出。

几个关键设计：

| 行为 | 说明 |
|---|---|
| **一次回复只用一种形式** | 长回复常被拆成多段发送。按 `stream_id` 缓存随机结果 `decision_ttl` 秒，避免「前句语音、后句文字」 |
| **不适合的内容自动跳过** | 代码块、网址、表格、Markdown 列表、HTML 标签、Markdown 链接一律不转语音 |
| **合成失败回退文字** | 语音合成出错时原样发送文字，保证消息不丢 |
| **非纯文本消息不碰** | 带图片、文件、语音的消息不会被改写 |
| **不抢占引用** | 转语音时自动去掉引用（QQ 不支持「引用 + 语音」组合，会返回成功但不投递） |

与 LLM 工具调用的关系：两者独立，可同时开启。LLM 主动调用的语音一定会发；概率语音只作用于 LLM 选择文字回复的场合。

> 默认 `enabled = false`，不配置完全不影响原有行为。

## 工作原理

### 语气选择

调用方未指定 `style` 时，插件按以下顺序决策：

1. **纯数字判定**：文本只由数字构成时，若配置了 `number_style` 则直接使用该风格
2. **规则匹配**：遍历配置规则与内置规则，取权重最高者
3. **兜底**：无规则命中时使用 `auto_style.fallback`

#### 纯数字专用风格

播报价格、时间、电话号码等纯数字内容时，韵律与正常说话差异较大，
用同一个风格合成往往效果不佳。可以指定专用风格：

```toml
[gpt_sovits.auto_style]
number_style = "数字"                  # 纯数字使用的风格，留空则不启用
number_style_include_chinese = true    # 是否把中文数字（一二三十百千）也算作数字
```

**判断范围**（满足即视为纯数字）：

| 类型 | 示例 |
|---|---|
| 阿拉伯数字 | `12345`、`3.14`、`-42`、`1,234.56` |
| 带单位 | `99元`、`2024年10月22日`、`12:30`、`50%`、`3号线` |
| 中文数字 | `一百二十三`、`九十`、`三千五百` |

**不算纯数字**（会走正常流程）：

| 类型 | 示例 |
|---|---|
| 中文夹数字 | `今天123`、`价格是99元` |
| 序数/量词 | `第3个`、`iPhone 15` |
| 纯符号 | `!!!`、`...` |

**优先级**：数字判断优先于自动语气规则，例如配置了 `number_style` 后，
`2333` 会走数字风格而非命中「兴奋」规则。

**注意**：`number_style` 不受 `auto_style.enabled` 影响，
即使关闭了自动语气，纯数字仍会使用该风格。留空则不启用此功能。

### 语言路由

合成前检测文本语言，然后按四级优先级选择风格：

| 优先级 | 规则 | 示例 |
|---|---|---|
| 1 | 当前风格本身支持该语言 | `zh` + `default` → `default` |
| 2 | 同名语言变体 `<风格>_<语言>` | `ja` + `default` → `default_ja` |
| 3 | 该语言的默认风格 | `ja` + `兴奋` → `default_ja` |
| 4 | 保持原风格（无对应语言模型时） | `en` + `default` → `default` |

#### 语言检测规则

按以下优先级判定，前面的命中即返回：

| 顺序 | 依据 | 结果 | 说明 |
|---|---|---|---|
| 1 | 出现**假名**（平假名/片假名） | `ja` | 中文文本不会出现假名，最可靠 |
| 2 | 出现**韩文**（谚文） | `ko` | 韩语独有字符集 |
| 3 | 出现**粤语独占字** | `yue` | 如 唔 咁 咗 哋 佢 乜 嘢 嘅 嗰 嚟 冇 睇 喺 |
| 4 | 出现西里尔/泰文/阿拉伯文/天城文/希腊文 | `ru`/`th`/`ar`/`hi`/`el` | 按文字系统识别 |
| 5 | 纯拉丁字母 | `en` | |
| 6 | 汉字占比 > 30% | `zh` | |
| 7 | 无任何可识别字符 | `fallback`（默认 `zh`） | 纯数字/符号 |

**为什么假名优先**：中日共用汉字，若按字符比例统计，「今日はいい天気ですね」的汉字占比为 40%，会被误判为中文。假名是中文绝不会出现的字符，因此作为第一判据。

**为什么粤语能单独识别**：粤语有大量普通话不用的独占字（唔、咁、咗、哋、佢、乜、嘢、嘅、嗰、嚟、冇、睇、喺、啱、㗎），命中即判定，误判率很低。GPT-SoVITS 原生支持 `yue`。

#### 已知局限

**纯汉字的日文无法自动区分**。例如人名「東雪蓮」，字符与中文完全相同，没有任何技术手段能判断它属于哪种语言，会落入 `zh`。

遇到这类情况，在该风格配置中显式指定 `language`：

```toml
[[gpt_sovits.styles]]
name = "default_ja"
languages = ["ja"]        # 声明支持的语言，供路由匹配
language = "ja"           # 强制合成语言，跳过自动检测
refer_wav = "..."
prompt_text = "..."
prompt_language = "ja"
```

两个字段的区别：

- `languages`：**声明**该风格支持哪些语言，供语言路由挑选风格
- `language`：**强制**使用指定语言合成，跳过自动检测

### 文本清洗

合成前自动剔除以下字符，避免 GPT-SoVITS 服务端因编码问题返回 400：

- 零宽字符（`U+200B`–`U+200F`、`U+2060`–`U+206F`）
- BOM（`U+FEFF`）、软连字符（`U+00AD`）
- 方向标记（`U+202A`–`U+202E`）、C0/C1 控制符

## 常见问题

**合成返回 400，日志显示 GBK 编码错误**

GPT-SoVITS 服务端在打印日语音素时会用到系统默认编码。启动服务时请设置 UTF-8：

```bash
# Windows
set PYTHONIOENCODING=utf-8
python -X utf8 api_v2.py -a 127.0.0.1 -p 9880

# Linux / macOS
PYTHONIOENCODING=utf-8 python api_v2.py -a 127.0.0.1 -p 9880
```

插件侧的文本清洗可以挡住大部分情况，但服务端设置 UTF-8 是更彻底的解决方式。

**日语合成发音不准**

插件只负责路由，发音质量取决于所用模型的训练数据。若使用纯中文数据训练的模型合成日语，
只能依赖底模的跨语言能力，发音会有偏差。要获得准确的日语发音，需用日语语音数据单独训练
一份模型，并通过 `languages = ["ja"]` 声明。

## 开发

```bash
# 本插件为独立 Git 仓库
cd plugins/CGl123_gpt-sovits-voice-plugin
git init && git add -A && git commit -m "init"
```

### 测试

```bash
python tests/test_units.py
```

覆盖音频有效性校验、文本清洗、语言检测、语气路由、语言路由、URL 转义、
并发锁与死代码检查，共 51 项断言。测试不依赖 `maibot_sdk`，按文件路径
直接加载被测模块。

模块结构：

| 文件 | 职责 |
|---|---|
| `plugin.py` | 插件入口、工具与命令定义、合成链路编排、出站拦截 |
| `gpt_sovits.py` | GPT-SoVITS HTTP 客户端、模型切换、风格解析与语言路由 |
| `style_router.py` | 语气规则引擎（正则匹配、权重排序、编译缓存） |
| `random_voice.py` | 概率语音（内容过滤、随机决策、结果缓存） |
| `utils.py` | 语言检测、文本清洗与音频有效性校验 |
| `audio.py` | 音频发送（base64 / 文件路径）与临时文件清理 |
| `config.py` | Pydantic 配置模型 |
| `tests/test_units.py` | 单元测试 |
| `tests/test_random_voice.py` | 概率语音测试 |

## License

[AGPL-3.0](LICENSE)
