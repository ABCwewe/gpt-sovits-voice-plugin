# GPT-SoVITS 语音插件

基于本地 [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) 服务的 MaiBot 文本转语音插件。
支持多种语音风格，LLM 可自主调用工具，用户也可通过 `/tts` 命令手动触发。

> MaiBot SDK 2.x（`MaiBotPlugin` + `@Tool` / `@Command` + Pydantic 配置）。

## 功能

- 🎙️ **LLM 自主触发**：planner 判断需要语音回复时自动调用 `tts_voice` 工具
- 🎛️ **多语音风格**：每个风格独立配置参考音频、提示文本、模型权重，风格缺失自动回退 `default`
- 📝 **用户命令**：`/tts <文本> [-v <风格>]`
- ✂️ **分句发送**：长文本按句切分逐段发送，听感更自然
- 📡 **双发送模式**：base64（IPC）或文件路径（voiceurl）

## 安装

将插件目录放入 MaiBot 的 `plugins/` 下即可（独立 Git 仓库，随插件中心安装或手动放置均可）。

依赖：`aiohttp`（MaiBot 通常已自带）。

## 配置

编辑 `plugins/gpt-sovits-voice-plugin/config.toml`：

```toml
[general]
max_text_length = 200        # 文本最大长度，超过降级为文字回复
split_sentences = true       # 长文本分句逐段发送
split_delay = 0.3            # 分句发送延迟（秒）
use_base64_audio = true      # true=base64 走 IPC；false=文件路径 voiceurl
audio_output_dir = "data/plugins/github.CGl123.gpt-sovits-voice-plugin"  # 文件模式输出目录，可修改
send_error_messages = true   # 合成失败时是否提示用户

[gpt_sovits]
server = "http://127.0.0.1:9880"   # GPT-SoVITS 服务地址
timeout = 60

# 每个风格独立完整参数（参考音频 / 提示文本 / 语言 / 模型权重均可不同）
[[gpt_sovits.styles]]
name = "default"
refer_wav = "/path/to/reference.wav"   # 参考音频
prompt_text = "参考音频对应的文本"
prompt_language = "zh"
gpt_weights = "/path/to/model.ckpt"    # 可选，动态切模型
sovits_weights = "/path/to/model.pth"  # 可选

[[gpt_sovits.styles]]
name = "流萤_伤感"
refer_wav = "/path/to/sad.wav"
prompt_text = "伤感的参考文本"
prompt_language = "zh"
```

## 使用方法

### 命令触发（用户手动）

```
/tts 你好世界                     # 使用 default 风格
/tts 今天真开心 -v 流萤_活泼      # 指定风格
/gptsovits 用本地语音合成          # 等价命令
/tts help                         # 查看帮助
```

### 自动触发（LLM 决定）

MaiBot 的 planner 判断需要语音回复时，会调用 `tts_voice` 工具并传入 `text`（必填）与可选的 `style`（风格名）。

- 风格不存在或未指定时，自动回退 `default`。

## 开发

```bash
# 本插件为独立 Git 仓库
cd plugins/gpt-sovits-voice-plugin
git init && git add -A && git commit -m "init"
```

## License

[AGPL-3.0](LICENSE)
