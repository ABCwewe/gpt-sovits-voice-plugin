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
    for name in ("config", "audio", "plugin"):
        spec = importlib.util.spec_from_file_location(f"gsv_test.{name}", ROOT / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"gsv_test.{name}"] = module
        spec.loader.exec_module(module)
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


if __name__ == "__main__":
    test_default_config()
    test_voice_message_has_no_binary()
    test_original_message_not_mutated()
    test_audio_sender_has_save_helper()
    test_plugin_no_longer_imports_base64()

    print("\n" + "=" * 60)
    print(f"音频载荷测试: {PASSED}/{PASSED + FAILED} 通过")
    print("=" * 60)
    sys.exit(0 if FAILED == 0 else 1)
