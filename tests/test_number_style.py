"""测试纯数字风格路由。"""
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


spec = importlib.util.spec_from_file_location("u", os.path.join(P, "utils.py"))
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)

iso = u.is_number_only

print("=" * 74)
print("一、纯数字判断（阿拉伯数字）")
print("=" * 74)

ARABIC = [
    ("12345", True, "纯数字"),
    ("2333", True, "网络用语数字"),
    ("3.14", True, "小数"),
    ("-42", True, "负数"),
    ("1,234.56", True, "千分位+小数"),
    ("+86", True, "带加号"),
    ("007", True, "前导零"),
    ("1e10", False, "科学计数法（含字母）"),
]
for text, exp, desc in ARABIC:
    got = iso(text)
    check(f"{desc}「{text}」", got == exp, got)

print()
print("=" * 74)
print("二、带单位的数字")
print("=" * 74)

UNITS = [
    ("12345元", True, "金额"),
    ("99块5毛", True, "口语金额"),
    ("2024年10月22日", True, "日期"),
    ("12:30", True, "时间（冒号被忽略）"),
    ("3点15分", True, "口语时间"),
    ("50%", True, "百分比"),
    ("30°", True, "角度"),
    ("1号线", True, "线路"),
]
for text, exp, desc in UNITS:
    got = iso(text)
    check(f"{desc}「{text}」", got == exp, got)

print()
print("=" * 74)
print("三、中文数字")
print("=" * 74)

CHINESE = [
    ("一百二十三", True, "中文数字"),
    ("九十", True, "中文数字"),
    ("三千五百", True, "中文数字"),
    ("一万", True, "中文数字"),
    ("两", True, "中文数字（两）"),
    ("零", True, "中文数字（零）"),
]
for text, exp, desc in CHINESE:
    got = iso(text)
    check(f"{desc}「{text}」", got == exp, got)

# 关闭中文数字识别
for text, exp, desc in [("一百二十三", False, "中文数字（allow_chinese=False）")]:
    got = iso(text, allow_chinese=False)
    check(f"{desc}「{text}」", got == exp, got)

print()
print("=" * 74)
print("四、非数字文本（不应误判）")
print("=" * 74)

NOT_NUMBER = [
    ("今天123", "中文+数字"),
    ("第3个", "序数词"),
    ("你好", "纯中文"),
    ("abc", "纯英文"),
    ("iPhone 15", "英文+数字"),
    # "3号楼" 走数字风格合理，不再列为反例
    ("!", "纯符号"),
    ("...", "纯标点"),
    ("", "空串"),
    ("   ", "纯空白"),
    ("123abc", "数字+字母"),
    ("价格是99元", "中文+数字"),
]
for text, desc in NOT_NUMBER:
    got = iso(text)
    check(f"{desc}「{text}」应为 False", not got, got)

print()
print("=" * 74)
print("五、边界与容错")
print("=" * 74)

check("None 输入不崩溃", iso(None) is False)
check("超长数字", iso("9" * 100) is True)
check("全角数字不识别（预期）", iso("１２３") is False,
      "全角数字当前不支持")
check("混合中英文标点", iso("1,234，567") is True)

print()
print("=" * 74)
passed = sum(1 for _, c, _ in results if c)
print(f"纯数字判断测试: {passed}/{len(results)} 通过")
print("=" * 74)
