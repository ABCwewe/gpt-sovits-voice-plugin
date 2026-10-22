"""测试纯数字风格路由的完整链路。"""
import importlib.util
import os
import sys
import types

import os
P = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

results = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -> {detail}" if detail else ""))


# 构造最小依赖
pkg = types.ModuleType("nt")
pkg.__path__ = [P]
sys.modules["nt"] = pkg

sdk = types.ModuleType("maibot_sdk")


class PCB:
    def __init__(self, **kw):
        pass


def Field(default=None, default_factory=None, description="", **kw):
    return default_factory() if default_factory else default


sdk.PluginConfigBase = PCB
sdk.Field = Field
sys.modules["maibot_sdk"] = sdk

for name in ["utils", "config"]:
    spec = importlib.util.spec_from_file_location(f"nt.{name}", os.path.join(P, f"{name}.py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules[f"nt.{name}"] = m
    spec.loader.exec_module(m)

spec = importlib.util.spec_from_file_location("nt.style_router", os.path.join(P, "style_router.py"))
router = importlib.util.module_from_spec(spec)
sys.modules["nt.style_router"] = router
spec.loader.exec_module(router)

spec = importlib.util.spec_from_file_location("nt.gpt_sovits", os.path.join(P, "gpt_sovits.py"))
gs = importlib.util.spec_from_file_location("nt.gpt_sovits", os.path.join(P, "gpt_sovits.py"))
gpt = importlib.util.module_from_spec(gs)
sys.modules["nt.gpt_sovits"] = gpt
gs.loader.exec_module(gpt)

utils_mod = sys.modules["nt.utils"]
cfg_mod = sys.modules["nt.config"]

print("=" * 74)
print("一、配置字段存在性")
print("=" * 74)

auto = cfg_mod.AutoStyleConfig()
check("AutoStyleConfig 有 number_style 字段",
      hasattr(auto, "number_style"), str(getattr(auto, "number_style", "缺失")))
check("AutoStyleConfig 有 number_style_include_chinese 字段",
      hasattr(auto, "number_style_include_chinese"))
check("number_style 默认为空", not auto.number_style)
check("include_chinese 默认为 True", auto.number_style_include_chinese is True)

print()
print("=" * 74)
print("二、模拟 plugin.py 的路由决策")
print("=" * 74)


def simulate_route(
    text,
    available,
    number_style="",
    include_chinese=True,
    auto_enabled=True,
    fallback="默认",
):
    """复刻 plugin.py 的 _handle 中风格决策部分。"""
    style_name = fallback  # resolve_style 的结果

    number_style_used = False
    if number_style:
        if utils_mod.is_number_only(text, allow_chinese=include_chinese):
            if number_style in available:
                style_name = number_style
                number_style_used = True

    if not number_style_used and auto_enabled:
        picked, reason = router.route_style(text, available, [], fallback)
        if picked in available:
            style_name = picked

    return style_name


AVAIL = ["默认", "兴奋", "吐槽", "数字"]

print("\n--- 场景 1：配置了 number_style='数字' ---")
CASES = [
    ("12345", "数字", "纯数字"),
    ("99元", "数字", "金额"),
    ("2024年10月22日", "数字", "日期"),
    ("一百二十三", "数字", "中文数字"),
    ("好耶！太棒了！", "兴奋", "兴奋词（不应走数字）"),
    ("你在说什么啊", "吐槽", "吐槽词"),
    ("今天天气不错", "默认", "普通文本"),
    ("今天赚了99元", "默认", "含数字但非纯数字"),
]
for text, exp, desc in CASES:
    got = simulate_route(text, AVAIL, number_style="数字")
    check(f"{desc}「{text}」-> {exp}", got == exp, got)

print("\n--- 场景 2：未配置 number_style（原行为）---")
for text, exp, desc in [
    ("12345", "默认", "纯数字走默认"),
    ("好耶！太棒了！", "兴奋", "兴奋词仍正常"),
]:
    got = simulate_route(text, AVAIL, number_style="")
    check(f"{desc}「{text}」-> {exp}", got == exp, got)

print("\n--- 场景 3：number_style 指向不存在的风格 ---")
got = simulate_route("12345", AVAIL, number_style="不存在的风格")
check("不存在的数字风格时回退正常流程", got == "默认", got)

print("\n--- 场景 4：关闭中文数字识别 ---")
got = simulate_route("一百二十三", AVAIL, number_style="数字", include_chinese=False)
check("关闭中文数字后走默认", got == "默认", got)
got = simulate_route("12345", AVAIL, number_style="数字", include_chinese=False)
check("阿拉伯数字仍走数字风格", got == "数字", got)

print("\n--- 场景 5：自动语气关闭 ---")
got = simulate_route("好耶！太棒了！", AVAIL, auto_enabled=False)
check("关闭自动语气后走默认", got == "默认", got)
got = simulate_route("12345", AVAIL, number_style="数字", auto_enabled=False)
check("数字风格不受 auto_style.enabled 影响", got == "数字", got)

print()
print("=" * 74)
print("三、数字风格优先级")
print("=" * 74)

print("\n  验证：数字判断优先于自动语气规则")
# "2333" 可能命中兴奋规则（网络用语），但配了数字风格应走数字
got = simulate_route("2333", AVAIL, number_style="数字")
check("「2333」优先走数字风格而非兴奋", got == "数字", got)

# 不带数字风格时应走正常规则
got = simulate_route("2333", AVAIL, number_style="")
print(f"  （未配数字风格时「2333」-> {got}，由自动语气规则决定）")

print()
print("=" * 74)
passed = sum(1 for _, c, _ in results if c)
print(f"数字风格路由测试: {passed}/{len(results)} 通过")
print("=" * 74)
