# GPT-SoVITS 语音插件

基于本地 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 服务的 MaiBot 文本转语音插件。
支持多种语音风格、按文本内容自动匹配语气、按语言自动切换模型；
LLM 可自主调用工具，用户也可通过 `/tts` 命令手动触发。

> MaiBot SDK 2.x（`MaiBotPlugin` + `@Tool` / `@Command` + Pydantic 配置）。

## 功能

- 🎙️ **LLM 自主触发**：planner 判断需要语音回复时自动调用 `tts_voice` 工具
- 🎛️ **多语音风格**：每个风格独立配置参考音频、提示文本、模型权重
- 🧠 **自动语气**：调用方未指定风格时，按文本内容（感叹词、情绪词、标点）自动选择语气
- 🌏 **多语言路由**：检测文本语言，自动切换到对应语言的模型与参考音频
- ⏩ **语速控制**：`speed_factor` 全局调节合成语速
- 🧹 **文本清洗**：自动剔除零宽字符、BOM 等不可见字符，避免合成失败
- 📝 **用户命令**：`/tts <文本> [-v <风格>]`
- 🔢 **字数限制**：过短（单字）与超长文本自动拒绝，上下限可配置
- 📡 **双发送模式**：base64（IPC）或文件路径（voiceurl）

## 安装

将插件目录放入 MaiBot 的 `plugins/` 下即可（独立 Git 仓库，随插件中心安装或手动放置均可）。

依赖：`aiohttp`（MaiBot 通常已自带）。

## 配置

编辑 `plugins/gpt-sovits-voice-plugin/config.toml`：

```toml
[general]
min_text_length = 2          # 文本最小长度，过短（如单字）拒绝合成
max_text_length = 200        # 文本最大长度，超过拒绝合成（超时风险）
use_base64_audio = true      # true=base64 走 IPC；false=文件路径 voiceurl
audio_output_dir = "data/plugins/CGl123.gpt-sovits-voice-plugin"  # 文件模式输出目录，可修改
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

## 工作原理

### 语气选择

调用方未指定 `style` 时，插件按以下顺序决策：

1. **规则匹配**：遍历配置规则与内置规则，取权重最高者
2. **兜底**：无规则命中时使用 `auto_style.fallback`

### 语言路由

合成前检测文本语言（`zh` / `ja` / `en` / `ko`），然后按四级优先级选择风格：

| 优先级 | 规则 | 示例 |
|---|---|---|
| 1 | 当前风格本身支持该语言 | `zh` + `default` → `default` |
| 2 | 同名语言变体 `<风格>_<语言>` | `ja` + `default` → `default_ja` |
| 3 | 该语言的默认风格 | `ja` + `兴奋` → `default_ja` |
| 4 | 保持原风格（无对应语言模型时） | `en` + `default` → `default` |

语言检测以**假名为第一判据**：中文文本不会出现假名，因此出现平假名/片假名即判定为日语。
这避免了「今日はいい天気ですね」因汉字占比高而被误判为中文的问题。

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
cd plugins/gpt-sovits-voice-plugin
git init && git add -A && git commit -m "init"
```

模块结构：

| 文件 | 职责 |
|---|---|
| `plugin.py` | 插件入口、工具与命令定义、合成链路编排 |
| `gpt_sovits.py` | GPT-SoVITS HTTP 客户端、模型切换、风格解析与语言路由 |
| `style_router.py` | 语气规则引擎（正则匹配、权重排序、编译缓存） |
| `utils.py` | 语言检测与文本清洗 |
| `audio.py` | 音频发送（base64 / 文件路径）与临时文件清理 |
| `config.py` | Pydantic 配置模型 |

## License

[AGPL-3.0](LICENSE)
