"""音频载荷回归测试：语音不得以 base64 进入 MaiBot 上下文。

背景：v1.5.2 及更早版本中 ``use_base64_audio`` 默认开启，音频被 base64
编码后写入 ``raw_message``。MaiBot 会把这段内容当作文本读入上下文并按
token 计费，单条十几秒的语音约 30 万字符，单次请求可消耗数十万 token；
且历史裁剪只按消息条数计算，这类超长消息不会被移除，会在每轮请求中
反复重发。本测试锁定修复后的行为，防止回归。
"""

import sys
import types
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PASSED = 0
FAILED = 0


def check(name, cond, detail=""):
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  [PASS] {name}")
    else:
        FAILED += 1
        print(f"  [FAIL] {name}  {detail}")


def _load_package():
    """按包上下文加载插件模块，让相对导入成立。"""

    pkg = types.ModuleType("gsv_test")
    pkg.__path__ = [str(ROOT)]
    sys.modules["gsv_test"] = pkg

    loaded = {}
    for name in ("config", "utils", "audio", "random_voice", "gpt_sovits", "plugin"):
        spec = importlib.util.spec_from_file_location(f"gsv_test.{name}", ROOT / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"gsv_test.{name}"] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # 缺可选依赖时跳过，不影响其它用例
            print(f"  [SKIP] 无法加载 {name}.py: {type(exc).__name__}: {exc}")
            continue
        loaded[name] = module
    return loaded


def test_default_config():
    """默认配置必须是文件模式。"""

    cfg = _load_package()["config"]
    field = cfg.GeneralConfig.model_fields["use_base64_audio"]
    check("use_base64_audio 默认关闭", field.default is False, f"实际 {field.default!r}")

    description = str(field.description or "")
    check("默认值说明包含风险提示", "token" in description or "上下文" in description)


def test_voice_message_has_no_binary():
    """改写的语音消息只能携带路径，不能携带音频数据。"""

    plugin = _load_package()["plugin"]
    source = {"session_id": "s1", "raw_message": [{"type": "text", "data": "hi"}]}
    result = plugin.GPTSovitsVoicePlugin._build_voice_message(None, source, "/tmp/voice.wav")
    segment = result["raw_message"][0]

    check("消息段类型为 voiceurl", segment.get("type") == "voiceurl", str(segment.get("type")))
    check("不包含 binary_data_base64", "binary_data_base64" not in segment)
    check("data 只携带路径", segment.get("data") == "/tmp/voice.wav", str(segment.get("data")))
    check("载荷长度接近路径长度", len(str(segment.get("data"))) < 260, str(len(str(segment.get("data")))))


def test_original_message_not_mutated():
    """改写消息时不应修改调用方传入的原对象。"""

    plugin = _load_package()["plugin"]
    source = {"session_id": "s1", "raw_message": [{"type": "text", "data": "hi"}]}
    plugin.GPTSovitsVoicePlugin._build_voice_message(None, source, "/tmp/voice.wav")

    check("原消息 raw_message 未被改写", source["raw_message"][0]["type"] == "text")


def test_audio_sender_has_save_helper():
    """AudioSender 必须提供落盘辅助方法供出站消息复用。"""

    audio = _load_package()["audio"]
    check("存在 save_temp_file", hasattr(audio.AudioSender, "save_temp_file"))
    check("save_temp_file 是协程", __import__("asyncio").iscoroutinefunction(audio.AudioSender.save_temp_file))


def test_plugin_no_longer_imports_base64():
    """plugin.py 不应再直接编码音频。"""

    source = (ROOT / "plugin.py").read_text(encoding="utf-8")
    check("plugin.py 无 base64 导入", "import base64" not in source)
    check("plugin.py 无 b64encode 调用", "b64encode" not in source)


def test_audio_validation_is_fast():
    """音频校验必须足够快，避免阻塞事件循环。

    逐样本 Python 循环在 60 秒立体声上要 500ms 以上，会让整个 bot 卡顿。
    """

    import array
    import io
    import time
    import wave

    utils = _load_package()["utils"]

    def make_wav(seconds: int) -> bytes:
        rate = 44100
        frames = rate * seconds
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(2)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(array.array("h", [3000] * frames * 2).tobytes())
        return buffer.getvalue()

    data = make_wav(60)
    started = time.perf_counter()
    valid, detail = utils.is_valid_audio(data)
    elapsed = (time.perf_counter() - started) * 1000

    check("60 秒音频校验通过", valid, detail)
    check(f"校验耗时 {elapsed:.0f}ms < 100ms", elapsed < 100, f"实际 {elapsed:.0f}ms")


def test_audio_validation_fallback_is_bounded():
    """audioop 缺失时的回退路径也必须受控。

    audioop 在 Python 3.13 起移除，回退路径不能退化成全量逐样本循环。
    """

    import array
    import builtins
    import io
    import time
    import wave

    utils = _load_package()["utils"]

    def make_wav(seconds: int) -> bytes:
        rate = 44100
        frames = rate * seconds
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(2)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(array.array("h", [3000] * frames * 2).tobytes())
        return buffer.getvalue()

    real_import = builtins.__import__

    def blocked_import(name, *args, **kwargs):
        if name == "audioop":
            raise ImportError("audioop removed")
        return real_import(name, *args, **kwargs)

    data = make_wav(60)
    builtins.__import__ = blocked_import
    try:
        started = time.perf_counter()
        valid, detail = utils.is_valid_audio(data)
        elapsed = (time.perf_counter() - started) * 1000
    finally:
        builtins.__import__ = real_import

    check("回退路径仍能判定有效性", valid, detail)
    check(f"回退路径耗时 {elapsed:.0f}ms < 300ms", elapsed < 300, f"实际 {elapsed:.0f}ms")


def test_audio_validation_detects_silence():
    """静音音频必须被判为无效。"""

    import array
    import io
    import wave

    utils = _load_package()["utils"]

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(array.array("h", [0] * 44100 * 3).tobytes())

    valid, detail = utils.is_valid_audio(buffer.getvalue())
    check("静音被判为无效", not valid, detail)


def test_random_voice_cache_bounded():
    """决策缓存必须在长 TTL 下也受上限约束。"""

    random_voice = _load_package()["random_voice"]
    decider = random_voice.RandomVoiceDecider()
    limit = random_voice.MAX_CACHE_SIZE

    for index in range(limit + 500):
        decider.should_convert(f"stream{index}", 0.5, ttl=86400)

    check(
        f"长 TTL 下缓存不超过 {limit}",
        len(decider._decisions) <= limit,
        f"实际 {len(decider._decisions)}",
    )


def test_temp_files_cleaned_on_close():
    """AudioSender.close 必须删除未到期的临时文件。"""

    import asyncio
    import os
    import tempfile

    audio = _load_package()["audio"]
    config_module = _load_package()["config"]

    async def scenario() -> tuple[bool, bool]:
        sender = audio.AudioSender(config_module.GeneralConfig(), tempfile.mkdtemp())

        class Ctx:
            class paths:
                runtime_dir = tempfile.mkdtemp()

        path = await sender.save_temp_file(b"RIFF" + b"\0" * 200, Ctx())
        existed = bool(path) and os.path.exists(path)
        await sender.close()
        return existed, bool(path) and os.path.exists(path)

    existed_before, exists_after = asyncio.run(scenario())
    check("落盘后文件存在", existed_before)
    check("close 后文件已删除", not exists_after)


def test_audio_response_has_size_cap():
    """音频响应必须有大小上限，防止异常响应撑爆内存。"""

    gpt_sovits = _load_package()["gpt_sovits"]
    check("存在 MAX_AUDIO_SIZE", hasattr(gpt_sovits, "MAX_AUDIO_SIZE"))
    check("上限取值合理", 1024 * 1024 <= gpt_sovits.MAX_AUDIO_SIZE <= 256 * 1024 * 1024)

    source = (ROOT / "gpt_sovits.py").read_text(encoding="utf-8")
    check("不再一次性 read() 整个响应", "await response.read()" not in source)
    check("使用分块读取", "iter_chunked" in source)


def test_runtime_rebuild_cleans_audio():
    """热重载替换 AudioSender 前必须清理旧实例的待删文件。"""

    source = (ROOT / "plugin.py").read_text(encoding="utf-8")
    rebuild = source.split("async def _build_runtime", 1)
    check("_build_runtime 存在", len(rebuild) == 2)
    if len(rebuild) != 2:
        return

    body = rebuild[1].split("async def ", 1)[0]
    check("重建前调用 _audio.close()", "_audio.close()" in body)

    # 顺序必须正确：先 close 旧实例，再赋新值
    close_pos = body.find("_audio.close()")
    assign_pos = body.find("self._audio = AudioSender")
    check("先清理再替换", 0 <= close_pos < assign_pos, f"close={close_pos} assign={assign_pos}")


def test_send_registers_temp_file():
    """send() 的文件分支也必须登记待清理路径。

    Tool/Command 路径走的是 send()，若它自己写文件而不登记，
    卸载时就无法回收，会留下孤立音频。
    """

    import asyncio
    import tempfile

    audio = _load_package()["audio"]
    config_module = _load_package()["config"]

    class StubSend:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def custom(self, custom_type: str, data: str, stream_id: str) -> None:
            self.calls.append((custom_type, data))

    class StubContext:
        def __init__(self) -> None:
            self.send = StubSend()

            class Paths:
                runtime_dir = tempfile.mkdtemp()

            self.paths = Paths()

    async def scenario() -> tuple[bool, str, int, int]:
        config = config_module.GeneralConfig()
        config.use_base64_audio = False
        sender = audio.AudioSender(config, tempfile.mkdtemp())
        context = StubContext()

        ok, message = await sender.send(context, b"RIFF" * 200, "default", "s1", "[t]")
        kind = context.send.calls[0][0] if context.send.calls else ""
        pending_before = len(sender._pending_paths)

        await sender.close()
        return ok, kind, pending_before, len(sender._pending_paths)

    ok, kind, pending_before, pending_after = asyncio.run(scenario())
    check("send 成功", ok)
    check("使用 voiceurl 段", kind == "voiceurl", kind)
    check("登记了待清理路径", pending_before == 1, str(pending_before))
    check("close 后清空", pending_after == 0, str(pending_after))


def test_base64_mode_warns():
    """显式开启 base64 时应输出警告，提醒费用风险。"""

    source = (ROOT / "audio.py").read_text(encoding="utf-8")
    check("base64 分支有警告日志", "logger.warning" in source and "token" in source)


def test_sentence_end_is_completed():
    """无标点的文本必须补上句末标点。

    GPT-SoVITS 按标点切段合成，末尾缺标点时最后一段没有终止信号，
    合成结果里最后一个字会被压缩甚至吞掉，听感上就是「最后字听不清」。
    """

    utils = _load_package()["utils"]
    complete = utils.ensure_sentence_end

    check("无标点补句号", complete("今天天气不错") == "今天天气不错。")
    check("单字补句号", complete("好") == "好。")
    check("末尾空格被裁剪后补句号", complete("我饿了  ") == "我饿了。")

    check("已有句号不改动", complete("今天天气不错。") == "今天天气不错。")
    check("已有问号不改动", complete("真的假的？") == "真的假的？")
    check("已有感叹号不改动", complete("太好了！") == "太好了！")
    check("已有省略号不改动", complete("哈哈哈……") == "哈哈哈……")
    check("已有波浪号不改动", complete("你好~") == "你好~")

    check("空串保持空", complete("") == "")
    check("全空格保持空", complete("   ") == "")


def test_sentence_end_keeps_number_style():
    """补句号不能影响纯数字风格的判定。"""

    utils = _load_package()["utils"]
    check("原始纯数字", utils.is_number_only("2333"))
    check("补句号后仍是纯数字", utils.is_number_only(utils.ensure_sentence_end("2333")))


def test_handle_uses_sentence_end():
    """两条合成路径都应使用句末补全。"""

    source = (ROOT / "plugin.py").read_text(encoding="utf-8")
    check("导入了 ensure_sentence_end", "ensure_sentence_end" in source.split("\n")[30] or "ensure_sentence_end" in source)
    # Tool/Command 路径（_handle）与概率语音路径都要调用
    check("_handle 调用补全", "raw_text = ensure_sentence_end(raw_text)" in source)
    check("概率语音路径调用补全", "ensure_sentence_end(clean_text(text))" in source)


def test_leading_silence_trimmed():
    """开头必须重整为「固定静音 + 干净起音」。

    GPT-SoVITS 结果固定带 80~140ms 前导静音，其后还有几十毫秒极弱起音。
    播放链路会连第一个字的声母一起截掉，所以裁净弱起音后改补一段固定
    长度的静音，给播放器留出启动时间。
    """

    import array
    import io
    import wave

    utils = _load_package()["utils"]
    trim = utils.trim_leading_silence
    pad_ms = utils._LEAD_PAD_SECONDS * 1000

    def make(lead_ms: int, total_ms: int = 900, amp: int = 5000,
             rate: int = 32000, weak_ms: int = 0) -> bytes:
        total = int(rate * total_ms / 1000)
        lead = int(rate * lead_ms / 1000)
        weak = int(rate * weak_ms / 1000)
        samples = array.array("h", [0] * lead + [50] * weak + [amp] * (total - lead - weak))
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(rate)
            handle.writeframes(samples.tobytes())
        return buffer.getvalue()

    def lead_of(data: bytes) -> float:
        with wave.open(io.BytesIO(data), "rb") as handle:
            rate = handle.getframerate()
            raw = handle.readframes(handle.getnframes())
        samples = array.array("h")
        samples.frombytes(raw)
        peak = max(abs(value) for value in samples) or 1
        for index in range(len(samples)):
            if abs(samples[index]) > peak * 0.05:
                return index / rate * 1000
        return 0.0

    # 不同前导静音与弱起音组合，处理后统一到补静音的长度
    for original_ms, weak_ms in ((80, 50), (120, 0), (85, 40), (140, 60)):
        result = lead_of(trim(make(original_ms, weak_ms=weak_ms)))
        check(
            f"前导 {original_ms}ms/弱起音 {weak_ms}ms 统一到 {pad_ms:.0f}ms",
            abs(result - pad_ms) <= 20,
            f"实际 {result:.1f}ms",
        )

    check("空数据原样返回", trim(b"") == b"")
    check("非 wav 数据原样返回", trim(b"not a wav") == b"not a wav")

    # 全静音：找不到有力起点，原样返回，不能被裁成空文件
    silent = make(900, total_ms=900, amp=0)
    check("全静音原样返回", trim(silent) == silent)

    # 超长前导（>1.5s）视为异常，不处理
    long_lead = make(1800, total_ms=2400)
    check("超长前导不处理", trim(long_lead) == long_lead)

    # 处理后要能正常解析且比原音频长（补了静音、裁了弱起音）
    out = trim(make(120, weak_ms=50))
    with wave.open(io.BytesIO(out), "rb") as handle:
        out_dur = handle.getnframes() / handle.getframerate()
    check("补静音后时长增加", out_dur > 0.8, f"实际 {out_dur:.2f}s")


def test_trim_runs_in_executor():
    """裁剪必须在线程池执行，避免阻塞事件循环。"""

    source = (ROOT / "gpt_sovits.py").read_text(encoding="utf-8")
    check("调用了 trim_leading_silence", "trim_leading_silence" in source)
    check("通过 run_in_executor 执行", "run_in_executor(None, trim_leading_silence" in source)


if __name__ == "__main__":
    test_default_config()
    test_voice_message_has_no_binary()
    test_original_message_not_mutated()
    test_audio_sender_has_save_helper()
    test_plugin_no_longer_imports_base64()
    test_audio_validation_is_fast()
    test_audio_validation_fallback_is_bounded()
    test_audio_validation_detects_silence()
    test_random_voice_cache_bounded()
    test_temp_files_cleaned_on_close()
    test_runtime_rebuild_cleans_audio()
    test_send_registers_temp_file()
    test_base64_mode_warns()
    test_sentence_end_is_completed()
    test_sentence_end_keeps_number_style()
    test_handle_uses_sentence_end()
    test_leading_silence_trimmed()
    test_trim_runs_in_executor()
    test_audio_response_has_size_cap()

    print("\n" + "=" * 60)
    print(f"音频载荷测试: {PASSED}/{PASSED + FAILED} 通过")
    print("=" * 60)
    sys.exit(0 if FAILED == 0 else 1)
