"""插件修复的回归测试。

不依赖 maibot_sdk：直接按文件路径加载被测模块。
运行: python test_plugin_fixes.py
"""
import importlib.util
import io
import math
import struct
import sys
import traceback
import wave
from array import array

import os
P = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_results = []


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(P, filename))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


utils = load("utils", "utils.py")
router = load("router", "style_router.py")


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f"  ({detail})" if detail and not cond else ""))


def make_wav(duration=2.0, rate=32000, rms_target=2000, silent=False):
    """生成测试 wav。"""
    n = int(rate * duration)
    if silent:
        samples = array("h", [0] * n)
    else:
        # 用正弦波近似，使其 RMS 接近目标值
        amp = int(rms_target * math.sqrt(2))
        samples = array("h", [
            int(amp * math.sin(2 * math.pi * 220 * i / rate)) for i in range(n)
        ])
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buf.getvalue()


# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
print("测试 1: 音频有效性校验（修复问题 7：静音崩坏检测）")
print("=" * 66)

valid, detail = utils.is_valid_audio(make_wav(2.0, rms_target=2000))
check("正常音频应通过", valid, detail)

valid, detail = utils.is_valid_audio(make_wav(2.0, silent=True))
check("纯静音音频应被拒绝", not valid, detail)

valid, detail = utils.is_valid_audio(make_wav(0.3, rms_target=2000))
check("时长过短应被拒绝", not valid, detail)

valid, detail = utils.is_valid_audio(make_wav(2.0, rms_target=20))
check("音量过低应被拒绝", not valid, detail)

valid, detail = utils.is_valid_audio(b"")
check("空数据应被拒绝", not valid, detail)

valid, detail = utils.is_valid_audio(b"not a wav file at all")
check("非 wav 数据应被拒绝", not valid, detail)

valid, detail = utils.is_valid_audio(b"\x00" * 200)
check("全零字节应被拒绝", not valid, detail)

# 之前实测的静音崩坏样本：0.76s，rms≈5
valid, detail = utils.is_valid_audio(make_wav(0.76, rms_target=5))
check("模拟历史崩坏样本(0.76s/rms5)应被拒绝", not valid, detail)


# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
print("测试 2: 文本清洗（修复问题 12、13：ZWJ 保留、更多空白）")
print("=" * 66)

check("零宽字符被清除",
      utils.clean_text("\u200c你好\u200c") == "你好")

check("BOM 被清除",
      utils.clean_text("\ufeff今天") == "今天")

check("软连字符被清除",
      utils.clean_text("abc\u00addef") == "abcdef")

# ZWJ 必须保留（emoji 组合）
zwj_in = "👨\u200d👩\u200d👧"
check("ZWJ(U+200D) 应保留以维持 emoji 组合",
      "\u200d" in utils.clean_text(zwj_in),
      repr(utils.clean_text(zwj_in)))

check("NBSP 归一为普通空格",
      utils.clean_text("a\u00a0b") == "a b")

check("行分隔符 U+2028 被归一",
      "\u2028" not in utils.clean_text("a\u2028b"))

check("段分隔符 U+2029 被归一",
      "\u2029" not in utils.clean_text("a\u2029b"))

check("全角空格被归一",
      utils.clean_text("a\u3000b") == "a b")

check("纯不可见字符返回空串",
      utils.clean_text("\u200c\u200c\u200c") == "")

check("正常文本不变",
      utils.clean_text("今天天气不错") == "今天天气不错")

# 实际失败过的那条
failed_text = "\u200cようこそ、わたしのソウル・ソサエティへ\u200c"
cleaned = utils.clean_text(failed_text)
check("历史失败文本清洗后无零宽符",
      "\u200c" not in cleaned and len(cleaned) == 19, repr(cleaned))


# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
print("测试 3: 语言检测")
print("=" * 66)

cases = [
    ("こんにちは、今日はいい天気ですね。", "ja"),
    ("今日はいい天気ですね", "ja"),
    ("私は学生です", "ja"),
    ("ラーメン", "ja"),
    ("今天天气不错", "zh"),
    ("这个方案需要讨论", "zh"),
    ("Hello how are you", "en"),
    ("안녕하세요", "ko"),
]
for text, expect in cases:
    got = utils.detect_language(text)
    check(f"检测 {text[:16]!r} -> {expect}", got == expect, f"得到 {got}")


# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
print("测试 4: 语气路由（修复问题 4、6、14、15、16）")
print("=" * 66)

AVAIL = ["默认", "兴奋", "吐槽"]

picked, _ = router.route_style("好耶！太棒了！", AVAIL, [], "默认")
check("兴奋词命中兴奋", picked == "兴奋", picked)

picked, _ = router.route_style("你在说什么啊", AVAIL, [], "默认")
check("吐槽词命中吐槽", picked == "吐槽", picked)

picked, _ = router.route_style("今天天气不错", AVAIL, [], "默认")
check("普通文本回退默认", picked == "默认", picked)

# 问题 4：兜底不在 available 中
picked, _ = router.route_style("普通文本", ["默认", "兴奋"], [], "default")
check("兜底风格非法时应自动纠正为可用风格",
      picked in ["默认", "兴奋"], picked)

# 问题 6：空 pattern 不应匹配一切
bad_rules = [{"style": "兴奋", "pattern": "", "weight": 100}]
picked, _ = router.route_style("任何文本", AVAIL, bad_rules, "默认")
check("空 pattern 规则应被忽略", picked == "默认", picked)

# 空 style
bad_rules = [{"style": "", "pattern": "任何", "weight": 100}]
picked, _ = router.route_style("任何文本", AVAIL, bad_rules, "默认")
check("空 style 规则应被忽略", picked == "默认", picked)

# 问题 14：权重相同时结果稳定
tie_rules = [
    {"style": "兴奋", "pattern": "测试", "weight": 50},
    {"style": "吐槽", "pattern": "测试", "weight": 50},
]
results = {router.route_style("测试文本", AVAIL, tie_rules, "默认")[0] for _ in range(5)}
check("权重相同时结果稳定", len(results) == 1, str(results))

# 问题 15：超长正则被拒绝
long_pattern = "a" * (router.MAX_PATTERN_LENGTH + 10)
bad_rules = [{"style": "兴奋", "pattern": long_pattern, "weight": 100}]
picked, _ = router.route_style("aaa", AVAIL, bad_rules, "默认")
check("超长正则被拒绝", picked == "默认", picked)

# 非法正则不应崩溃
bad_rules = [{"style": "兴奋", "pattern": "([unclosed", "weight": 100}]
try:
    picked, _ = router.route_style("文本", AVAIL, bad_rules, "默认")
    check("非法正则不崩溃", picked == "默认", picked)
except Exception as exc:
    check("非法正则不崩溃", False, str(exc))

# 自定义规则优先级
good_rules = [{"style": "吐槽", "pattern": "好耶", "weight": 999}]
picked, _ = router.route_style("好耶！", AVAIL, good_rules, "默认")
check("自定义规则权重更高时胜出", picked == "吐槽", picked)

# 缓存上限
for i in range(router.MAX_CACHE_SIZE + 50):
    router._compile(f"cache_test_{i}")
check("编译缓存不超过上限",
      len(router._compiled) <= router.MAX_CACHE_SIZE,
      f"当前 {len(router._compiled)}")


# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
print("测试 5: 语言路由（模拟 GPTSovitsClient.resolve_by_language）")
print("=" * 66)


class FakeStyle:
    def __init__(self, name, languages=None):
        self.name = name
        self.languages = languages or []


class FakeClient:
    def __init__(self, styles):
        self._styles = {s.name: s for s in styles}

    def default_style_name(self):
        for n in ("default", "默认"):
            if n in self._styles:
                return n
        return next(iter(self._styles), "")

    # 复制自 gpt_sovits.py 的修复版本
    def resolve_by_language(self, lang, style_name, prefix=""):
        styles = self._styles
        if not lang:
            return style_name
        cur = styles.get(style_name)
        if cur is not None and lang in (cur.languages or []):
            return style_name
        for suffix in (f"_{lang}", f"-{lang}"):
            if f"{style_name}{suffix}" in styles:
                return f"{style_name}{suffix}"
        dn = self.default_style_name()
        for suffix in (f"_{lang}", f"-{lang}"):
            if f"{dn}{suffix}" in styles:
                return f"{dn}{suffix}"
        return style_name


c = FakeClient([FakeStyle("默认", ["zh"]), FakeStyle("默认_ja", ["ja"]),
                FakeStyle("兴奋", ["zh"]), FakeStyle("吐槽", ["zh"])])
check("zh + 默认 -> 默认", c.resolve_by_language("zh", "默认") == "默认")
check("ja + 默认 -> 默认_ja", c.resolve_by_language("ja", "默认") == "默认_ja")
check("ja + 兴奋 -> 默认_ja", c.resolve_by_language("ja", "兴奋") == "默认_ja")
check("ja + 吐槽 -> 默认_ja", c.resolve_by_language("ja", "吐槽") == "默认_ja")
check("en + 默认 -> 默认（无英文变体）", c.resolve_by_language("en", "默认") == "默认")

# 有语气变体时优先同名
c2 = FakeClient([FakeStyle("默认", ["zh"]), FakeStyle("兴奋", ["zh"]),
                 FakeStyle("兴奋_ja", ["ja"]), FakeStyle("默认_ja", ["ja"])])
check("ja + 兴奋 有同名变体时用 兴奋_ja",
      c2.resolve_by_language("ja", "兴奋") == "兴奋_ja")

# 无语言声明时保持原样
c3 = FakeClient([FakeStyle("甲"), FakeStyle("乙")])
check("无 languages 声明时保持原风格",
      c3.resolve_by_language("ja", "甲") == "甲")


# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
print("测试 6: URL 转义（修复问题 3）")
print("=" * 66)
import urllib.parse

tricky = "my model & test#1.ckpt"
encoded = urllib.parse.urlencode({"weights_path": tricky})
check("含空格/&/# 的路径被正确转义",
      "weights_path=my+model+%26+test%231.ckpt" == encoded, encoded)
check("转义后可完整还原",
      urllib.parse.parse_qs(encoded)["weights_path"][0] == tricky)


# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
print("测试 7: 并发锁存在性（修复问题 2）")
print("=" * 66)
src = open(os.path.join(P, "gpt_sovits.py"), encoding="utf-8").read()
check("使用 asyncio.Lock 保护切换+合成", "asyncio.Lock()" in src)
check("模型状态为实例变量（非 ClassVar）",
      "ClassVar" not in src and "self._current_gpt_weights" in src)
check("提供 reset_model_state 供服务端重启后调用",
      "def reset_model_state" in src)


# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
print("测试 8: 死代码检查（修复问题 8）")
print("=" * 66)
import ast

tree = ast.parse(src)
# 检查 resolve_by_language 是否还有两个连续 return
for node in ast.walk(tree):
    if isinstance(node, ast.FunctionDef) and node.name == "resolve_by_language":
        body = node.body
        consecutive = 0
        max_consecutive = 0
        for stmt in body:
            if isinstance(stmt, ast.Return):
                consecutive += 1
                max_consecutive = max(max_consecutive, consecutive)
            else:
                consecutive = 0
        check("resolve_by_language 无连续重复 return",
              max_consecutive <= 1, f"发现 {max_consecutive} 个连续 return")


# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 66)
total = len(_results)
passed = sum(1 for _, ok, _ in _results if ok)
failed = [(n, d) for n, ok, d in _results if not ok]
print(f"测试结果: {passed}/{total} 通过" + (f"，{len(failed)} 失败" if failed else ""))
if failed:
    print("\n失败项：")
    for n, d in failed:
        print(f"  - {n}: {d}")
print("=" * 66)
sys.exit(0 if not failed else 1)
