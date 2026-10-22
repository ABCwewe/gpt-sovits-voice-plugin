"""概率语音测试：内容过滤 + 随机决策 + 缓存一致性。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from random_voice import (
    DEFAULT_SKIP_PATTERNS,
    MAX_CACHE_SIZE,
    RandomVoiceDecider,
)

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


def test_suitability():
    print("\n【内容过滤】")
    d = RandomVoiceDecider()

    ok, _ = d.text_is_suitable("今天天气不错呀", 2, 80)
    check("普通短句适合", ok)

    ok, reason = d.text_is_suitable("", 2, 80)
    check("空文本不适合", not ok, reason)

    ok, reason = d.text_is_suitable("   ", 2, 80)
    check("纯空白不适合", not ok, reason)

    ok, reason = d.text_is_suitable("嗯", 2, 80)
    check("单字过短不适合", not ok, reason)

    ok, reason = d.text_is_suitable("好", 2, 80)
    check("单字'好'不适合", not ok, reason)

    long_text = "这是一段很长的文字" * 20
    ok, reason = d.text_is_suitable(long_text, 2, 80)
    check("超长文本不适合", not ok, reason)

    # 空白不占长度
    ok, _ = d.text_is_suitable("  你好  ", 2, 80)
    check("首尾空白不影响判定", ok)

    # 代码块
    ok, reason = d.text_is_suitable("看这段：\n```python\nprint(1)\n```", 2, 200)
    check("代码块不适合", not ok, reason)

    # 网址
    ok, reason = d.text_is_suitable("详见 https://example.com/x", 2, 200)
    check("网址不适合", not ok, reason)

    # 表格
    ok, reason = d.text_is_suitable("| 名称 | 值 |\n| --- | --- |", 2, 200)
    check("表格不适合", not ok, reason)

    # Markdown 列表
    ok, reason = d.text_is_suitable("- 第一点\n- 第二点", 2, 200)
    check("Markdown 列表不适合", not ok, reason)

    # 有序列表
    ok, reason = d.text_is_suitable("1. 第一\n2. 第二", 2, 200)
    check("有序列表不适合", not ok, reason)

    # HTML
    ok, reason = d.text_is_suitable("这是 <b>粗体</b>", 2, 200)
    check("HTML 标签不适合", not ok, reason)

    # Markdown 链接
    ok, reason = d.text_is_suitable("点击[这里](http://a.com)", 2, 200)
    check("Markdown 链接不适合", not ok, reason)

    # 正常含数字的句子应该通过
    ok, _ = d.text_is_suitable("今天气温25度", 2, 80)
    check("含数字的正常句子适合", ok)

    # 自定义规则覆盖内置
    ok, reason = d.text_is_suitable("普通句子", 2, 80, patterns=[])
    check("自定义空规则时全部通过", ok)

    ok, reason = d.text_is_suitable("包含ABC", 2, 80, patterns=[r"ABC"])
    check("自定义规则可命中", not ok, reason)


def test_decision():
    print("\n【随机决策】")
    d = RandomVoiceDecider()

    # 概率 0 永远不转
    for _ in range(20):
        enabled, _ = d.should_convert("s1", 0.0, 0.0)
        if enabled:
            break
    check("概率0从不转语音", not enabled)

    # 概率 1 永远转
    d.reset()
    for _ in range(20):
        enabled, _ = d.should_convert("s2", 1.0, 0.0)
        if not enabled:
            break
    check("概率1总是转语音", enabled)

    # 概率 0.5 应该两种结果都出现（统计检验）
    d.reset()
    hits = 0
    for i in range(400):
        enabled, _ = d.should_convert(f"t{i}", 0.5, 0.0)
        hits += 1 if enabled else 0
    ratio = hits / 400
    check(
        "概率0.5 比例接近一半",
        0.35 <= ratio <= 0.65,
        f"实际 {ratio:.2f}",
    )

    # 缓存一致性：同一 stream 在 TTL 内结果不变
    d.reset()
    first, cache1 = d.should_convert("same", 0.5, 60.0)
    check("首次不是缓存", not cache1)
    consist = True
    for _ in range(30):
        again, cached = d.should_convert("same", 0.5, 60.0)
        if again != first or not cached:
            consist = False
            break
    check("TTL内结果恒定且命中缓存", consist)

    # TTL 过期后可以重新掷骰
    d.reset()
    d.should_convert("exp", 0.5, 0.0)
    _, cached = d.should_convert("exp", 0.5, 0.0)
    check("TTL为0时不复用缓存", not cached)

    # 不同 stream 互不影响
    d.reset()
    d.should_convert("a", 1.0, 60.0)
    enabled_b, _ = d.should_convert("b", 0.0, 60.0)
    check("不同stream互不影响", not enabled_b)

    # 空 stream_id 退化为全局键
    d.reset()
    e1, _ = d.should_convert("", 1.0, 60.0)
    e2, cached = d.should_convert("", 1.0, 60.0)
    check("空stream用全局键", e1 and e2 and cached)


def test_prune():
    print("\n【缓存清理】")
    d = RandomVoiceDecider()
    # 填满超过阈值
    for i in range(MAX_CACHE_SIZE + 200):
        d.should_convert(f"k{i}", 0.5, 0.0)
    size = len(d._decisions)
    check(
        "缓存不会无限增长",
        size <= MAX_CACHE_SIZE + 1,
        f"实际 {size}",
    )

    # 重置
    d.should_convert("x", 1.0, 60.0)
    d.reset()
    check("reset 清空缓存", len(d._decisions) == 0)


def test_pattern_cache():
    print("\n【正则缓存】")
    d = RandomVoiceDecider()
    d.text_is_suitable("测试", 2, 80)
    n1 = len(d._pattern_cache)
    d.text_is_suitable("再测", 2, 80)
    n2 = len(d._pattern_cache)
    check("正则只编译一次", n1 == n2 == len(DEFAULT_SKIP_PATTERNS))

    # 无效正则不应崩溃
    ok, _ = d.text_is_suitable("正常文本", 2, 80, patterns=[r"[invalid("])
    check("无效正则被跳过而不崩溃", ok)


if __name__ == "__main__":
    test_suitability()
    test_decision()
    test_prune()
    test_pattern_cache()

    print("\n" + "=" * 60)
    print(f"概率语音测试: {PASSED}/{PASSED + FAILED} 通过")
    print("=" * 60)
    sys.exit(0 if FAILED == 0 else 1)
