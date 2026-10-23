"""语音对讲功能测试（v1.6.0 新增）。

不依赖 maibot_sdk：直接按文件路径加载被测模块。
运行: python tests/test_intercom.py
"""

import importlib.util
import os
import sys

P = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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


def load(name, filename):
    """按文件路径加载模块。

    必须把插件目录作为包的搜索路径传进去，否则模块内的
    ``from .utils import ...`` 这类相对导入会失败——Runner 加载插件时
    也是这么做的。
    """

    spec = importlib.util.spec_from_file_location(
        name,
        os.path.join(P, filename),
        submodule_search_locations=[P],
    )
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


def load_plugin_module():
    """按 Runner 的方式加载 plugin.py（它用相对导入）。"""
    spec = importlib.util.spec_from_file_location(
        "gp_plugin_under_test",
        os.path.join(P, "plugin.py"),
        submodule_search_locations=[P],
    )
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


# ────────────────────────────────────────────────── 窗口状态机


def test_window():
    print("\n[1] 语音窗口状态机")
    intercom = load("ic_mod", "intercom.py")
    Mgr = intercom.IntercomWindowManager

    mgr = Mgr()
    d = mgr.evaluate(stream_id="s1", text="你好", now=1000.0)
    check("首条语音开启窗口", d.respond is True and mgr.is_voice_active("s1", now=1000.0))

    mgr = Mgr()
    mgr.evaluate(stream_id="s1", text="你好", now=1000.0, hold_seconds=60)
    check("窗口内有效", mgr.is_voice_active("s1", now=1030.0) is True)
    check("窗口外失效", mgr.is_voice_active("s1", now=1100.0) is False)

    mgr = Mgr()
    mgr.evaluate(stream_id="s1", text="A", now=1000.0, hold_seconds=100)
    mgr.evaluate(stream_id="s1", text="B", now=1080.0, hold_seconds=100)
    check("新语音续期", mgr.is_voice_active("s1", now=1170.0) is True)
    check("续期后仍会到期", mgr.is_voice_active("s1", now=1200.0) is False)

    mgr = Mgr()
    d = mgr.evaluate(stream_id="s1", text="嗯", now=1000.0, min_text_length=3)
    check("文本过短不开窗口", d.respond is False and mgr.is_voice_active("s1", now=1000.0) is False, d.reason)

    mgr = Mgr()
    d = mgr.evaluate(stream_id="s1", text="   ", now=1000.0)
    check("空白文本不开窗口", d.respond is False, d.reason)

    mgr = Mgr()
    d = mgr.evaluate(stream_id="s1", text="随便说", now=1000.0, require_wake_word=True, wake_words=["打电话"])
    check("未唤醒不开窗口", d.respond is False and "未唤醒" in d.reason, d.reason)
    d = mgr.evaluate(stream_id="s1", text="打电话", now=1001.0, require_wake_word=True, wake_words=["打电话"])
    check("唤醒词开启窗口", d.respond is True and mgr.is_voice_active("s1", now=1001.0))
    d = mgr.evaluate(stream_id="s1", text="聊别的", now=1002.0, require_wake_word=True, wake_words=["打电话"])
    check("唤醒后普通语音续期", d.respond is True)

    mgr = Mgr()
    mgr.evaluate(stream_id="s1", text="A", now=1000.0)
    check("不同流隔离", mgr.is_voice_active("s2", now=1000.0) is False)

    mgr = Mgr()
    mgr.evaluate(stream_id="s1", text="A", now=1000.0, hold_seconds=10)
    check("查询时清掉到期窗口", mgr.active_streams(now=1100.0) == [])
    check("到期窗口不再计入", mgr.active_streams(now=1100.0) == [], "应已清理")

    mgr = Mgr()
    mgr.evaluate(stream_id="s1", text="A", now=1000.0, hold_seconds=100)
    mgr.evaluate(stream_id="s2", text="B", now=1000.0, hold_seconds=100)
    check("未到期窗口全部保留", sorted(mgr.active_streams(now=1010.0)) == ["s1", "s2"])

    mgr = Mgr()
    mgr.evaluate(stream_id="s1", text="A", now=1000.0)
    mgr.force_open("s1", hold_seconds=60, now=2000.0)
    check("force_open 生效", mgr.is_voice_active("s1", now=2030.0) is True)
    check("force_open 到期", mgr.is_voice_active("s1", now=2100.0) is False)

    mgr = Mgr()
    mgr.force_open("s1", hold_seconds=100, now=1000.0)
    mgr.close("s1")
    check("可以主动关闭", mgr.is_voice_active("s1", now=1001.0) is False)


# ────────────────────────────────────────────────── 消息解析


def test_message_parsing():
    print("\n[2] 入站消息解析")
    plugin = load_plugin_module()
    P_cls = plugin.GPTSovitsVoicePlugin

    extract = P_cls._extract_voice_transcript

    text, ok = extract({"raw_message": [{"type": "voice", "data": "[语音: 你好呀]"}]})
    check("纯语音取出正文", ok is True and text == "你好呀", f"{ok} {text!r}")

    text, ok = extract({"raw_message": [{"type": "voice", "data": "[语音消息，转录失败]"}]})
    check("转录失败算语音但无文本", ok is True and text == "", f"{ok} {text!r}")

    text, ok = extract({"raw_message": [{"type": "image", "data": ""}]})
    check("图片不参与判定", ok is False)

    text, ok = extract({})
    check("空消息", (text, ok) == ("", False))

    text, ok = extract({
        "raw_message": [
            {"type": "reply", "data": {}},
            {"type": "voice", "data": "[语音: 在吗]"},
        ]
    })
    check("跳过引用段", ok is True and text == "在吗", f"{ok} {text!r}")

    text, ok = extract({
        "raw_message": [
            {"type": "text", "data": "看这个"},
            {"type": "voice", "data": "[语音: 好]"},
        ]
    })
    check("混合消息合并文本", ok is True and text == "看这个 好", f"{ok} {text!r}")

    text = plugin.GPTSovitsVoicePlugin._extract_plain_text({"raw_message": [{"type": "text", "data": "回复"}]})
    check("出站纯文本可提取", text == "回复", repr(text))

    check(
        "出站含图片不转语音",
        plugin.GPTSovitsVoicePlugin._extract_plain_text({
            "raw_message": [{"type": "text", "data": "x"}, {"type": "image", "data": ""}]
        }) is None,
    )

    check("strip wrapper 去前缀", plugin._strip_voice_wrapper("[语音: 内容]") == "内容")
    check("strip wrapper 无内容返回空", plugin._strip_voice_wrapper("[语音消息]") == "")


def test_group_detection():
    print("\n[3] 群聊判定")

    plugin = load_plugin_module()
    is_group = plugin.GPTSovitsVoicePlugin._is_group_message

    check("私聊 group_info=None", is_group({"message_info": {"group_info": None}}) is False)
    check("缺 group_info 键", is_group({"message_info": {}}) is False)
    check("群聊标准结构", is_group({
        "message_info": {"group_info": {"group_id": "123", "group_name": "群"}}
    }) is True)
    check("群号为空仍是群", is_group({
        "message_info": {"group_info": {"group_id": "", "group_name": ""}}
    }) is True)
    check("message_info 缺失", is_group({}) is False)
    check("message_info 非字典", is_group({"message_info": []}) is False)


def test_config():
    print("\n[4] 配置模型")

    cfg_mod = load("cfg_mod", "config.py")
    c = cfg_mod.PluginConfig()

    check("存在 intercom 段", hasattr(c, "intercom"))
    check("默认不启用对讲", c.intercom.enabled is False)
    check("默认仅私聊", c.intercom.private_only is True)
    check("默认窗口 180 秒", c.intercom.hold_seconds == 180)
    check("默认超长回退文字", c.intercom.oversize_strategy == "text")
    check("概率语音默认不启用", c.random_voice.enabled is False)
    check("两段互不干扰", hasattr(c, "random_voice") and hasattr(c, "intercom"))
    check("总开关默认开启", c.plugin.enabled is True)
    # 宿主用 plugin.config_version 判断配置结构版本，缺失会拒绝加载插件，
    # 只保留配置管理。所以这个字段必须有有效值。
    check("config_version 存在（宿主强制要求）", bool(c.plugin.config_version),
          c.plugin.config_version)
    check("config_version 是三段式版本号",
          c.plugin.config_version.count(".") == 2, c.plugin.config_version)


def test_master_switch():
    """总开关必须真的拦住所有入口。

    `plugin.enabled` 是插件自己的字段，宿主不会读——宿主那层的 enabled
    属于组件注册状态。早先没有任何代码判断它，WebUI 里关掉开关后插件
    照常响应，开关形同虚设。
    """

    print("\n[4b] 总开关生效")

    plugin = load_plugin_module()
    P_cls = plugin.GPTSovitsVoicePlugin

    check("插件提供 _enabled 判定", hasattr(P_cls, "_enabled"))

    source = (P_cls.__module__ and __import__("inspect").getsource(P_cls)) or ""
    # 四个入口都要判断：工具、命令、入站钩子、出站钩子
    check("入口共 4 处调用 _enabled", source.count("self._enabled()") >= 4,
          f"实际 {source.count('self._enabled()')}")


def test_components():
    print("\n[5] 组件挂载")

    plugin = load_plugin_module()
    P_cls = plugin.GPTSovitsVoicePlugin
    ATTR = "__maibot_component_info__"

    hooks = {}
    apis = {}
    for _name, fn in inspect_members(P_cls):
        info = getattr(fn, ATTR, None)
        if info is None:
            continue
        kind = type(info).__name__
        if kind == "HookHandlerComponentInfo":
            hooks[info.name] = (info.hook, info.timeout_ms)
        elif kind == "APIComponentInfo":
            apis[info.name] = (info.version, info.public)

    check(
        "入站钩子订阅 after_process",
        hooks.get("gpt_sovits_intercom_mark", ("",))[0] == "chat.receive.after_process",
        str(hooks),
    )
    check(
        "出站钩子仍订阅 before_send",
        hooks.get("gpt_sovits_random_voice", ("",))[0] == "send_service.before_send",
        str(hooks),
    )
    check("入站钩子超时 3 秒", hooks.get("gpt_sovits_intercom_mark", ("", 0))[1] == 3000)
    check("原有 tts_voice 工具仍在", hasattr(P_cls, "handle_tts"))
    check("原有 /tts 命令仍在", hasattr(P_cls, "handle_tts_command"))

    for api_name in ("is_voice_active", "set_voice_active", "list_styles"):
        check(f"API {api_name} 存在且公开", apis.get(api_name, ("", False))[1] is True, str(apis))


def inspect_members(cls):
    """取出类的所有函数成员（含继承）。"""

    import inspect

    return inspect.getmembers(cls, predicate=inspect.isfunction)


def test_audio_cache():
    print("\n[6] 音频复用缓存（防重复合成）")

    plugin = load_plugin_module()
    p = plugin.GPTSovitsVoicePlugin()
    p._intercom_audio = {}

    import os
    import tempfile

    handle, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(handle)
    with open(tmp, "wb") as f:
        f.write(b"x")

    p._remember_intercom_audio("s1", "你好", tmp)

    check("相同文本命中缓存", p._recall_intercom_audio("s1", "你好") == tmp)
    check("不同文本不命中", p._recall_intercom_audio("s1", "别的话") is None)
    check("其他会话不命中", p._recall_intercom_audio("s2", "你好") is None)
    check("未知会话不命中", p._recall_intercom_audio("nope", "你好") is None)

    # 文件被清理后不能再复用
    os.remove(tmp)
    check("文件已删则不命中", p._recall_intercom_audio("s1", "你好") is None)

    # 过期不命中
    handle2, tmp2 = tempfile.mkstemp(suffix=".wav")
    os.close(handle2)
    p._remember_intercom_audio("s3", "文本", tmp2)
    text_cached, path_cached, at = p._intercom_audio["s3"]
    # 手动把时间戳往前拨 20 秒
    p._intercom_audio["s3"] = (text_cached, path_cached, at - 20.0)
    check("超过 15 秒不命中", p._recall_intercom_audio("s3", "文本") is None)
    os.remove(tmp2)


def test_single_synthesis_per_reply():
    print("\n[7] 一条回复拆成多段时的合成次数")

    plugin = load_plugin_module()
    p = plugin.GPTSovitsVoicePlugin()
    p._intercom_audio = {}

    import asyncio
    import os
    import tempfile

    handle, tmp = tempfile.mkstemp(suffix=".wav")
    os.close(handle)
    with open(tmp, "wb") as f:
        f.write(b"x")

    calls = {"n": 0}

    async def fake_synth(content, style, prefix):
        calls["n"] += 1
        return b"audio-bytes"

    p._synthesize_reply = fake_synth

    class FakeAudio:
        async def save_temp_file(self, data, ctx):
            return tmp

    p._audio = FakeAudio()

    # 注入一个可用的 ctx，避免日志调用抛异常中断流程
    class FakeLogger:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def debug(self, *a, **k): pass
        def error(self, *a, **k): pass

    class FakeCtx:
        logger = FakeLogger()

    p._set_context(FakeCtx()) if hasattr(p, "_set_context") else None
    # 直接覆盖 _ctx，绕过 SDK 的注入检查
    object.__setattr__(p, "_ctx", FakeCtx())

    class Cfg:
        max_reply_chars = 60
        oversize_strategy = "text"

    async def run():
        for _ in range(2):
            await p._convert_for_intercom(
                {"set_reply": False}, {"raw_message": []}, "同一段话", "s1", Cfg()
            )
        return calls["n"]

    n = asyncio.new_event_loop().run_until_complete(run())
    check("重复文本只合成一次", n == 1, f"实际合成 {n} 次")
    os.remove(tmp)


def test_orphan_sweep():
    print("\n[8] 遗留音频清理")

    # audio.py 用相对导入，必须按包方式加载
    spec = importlib.util.spec_from_file_location(
        "audio_under_test",
        os.path.join(P, "audio.py"),
        submodule_search_locations=[P],
    )
    audio_mod = importlib.util.module_from_spec(spec)
    sys.modules["audio_under_test"] = audio_mod
    spec.loader.exec_module(audio_mod)

    Sender = audio_mod.AudioSender

    import shutil
    import tempfile
    import time

    tmpdir = tempfile.mkdtemp()
    try:
        old = os.path.join(tmpdir, "tts_gpt_sovits_old.wav")
        with open(old, "wb") as f:
            f.write(b"x")
        os.utime(old, (time.time() - 3600, time.time() - 3600))

        fresh = os.path.join(tmpdir, "tts_gpt_sovits_fresh.wav")
        with open(fresh, "wb") as f:
            f.write(b"x")

        unrelated = os.path.join(tmpdir, "keep_me.wav")
        with open(unrelated, "wb") as f:
            f.write(b"x")

        removed = Sender._sweep_sync(tmpdir, 900.0)

        check("删除一个遗留文件", removed == 1, str(removed))
        check("超时文件被删", not os.path.exists(old))
        check("新文件保留", os.path.exists(fresh))
        check("非本插件文件保留", os.path.exists(unrelated))

        # 目录不存在时应安全返回 0
        check("目录不存在返回 0", Sender._sweep_sync("/nonexistent/path/xyz", 900.0) == 0)
        check("空目录路径返回 0", Sender._sweep_sync("", 900.0) == 0)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    test_window()
    test_message_parsing()
    test_group_detection()
    test_config()
    test_master_switch()
    test_components()
    test_audio_cache()
    test_single_synthesis_per_reply()
    test_orphan_sweep()
    print()
    print("=" * 60)
    print(f"语音对讲测试: {PASSED}/{PASSED + FAILED} 通过")
    if FAILED:
        print(f"失败 {FAILED} 项")
    print("=" * 60)
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
