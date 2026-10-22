"""语言检测完整测试：粤语、其他文字系统、fallback。"""
import importlib.util

import os
P = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

spec = importlib.util.spec_from_file_location("u", rf"{P}\utils.py")
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)

results = []


def check(name, cond, detail=""):
    results.append((name, cond, detail))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  -> {detail}" if detail else ""))


print("=" * 74)
print("一、粤语检测")
print("=" * 74)

YUE_CASES = [
    "唔该晒，多谢你",
    "佢哋话听日去饮茶",
    "呢个系乜嘢嚟㗎",
    "我唔知你讲咩",
    "咁都得？",
    "佢好叻",
    "冇问题",
    "你睇下呢个",
    "喺边度啊",
    "唔该借借",
]
for text in YUE_CASES:
    got = u.detect_language(text)
    check(f"「{text}」-> yue", got == "yue", got)

print()
print("=" * 74)
print("二、粤语误判检查（普通话不应判成粤语）")
print("=" * 74)

ZH_CASES = [
    "今天天气不错",
    "我在图书馆等你",
    "这个方案需要再讨论一下",
    "他说明天开会",
    "你好吗",
    "谢谢你",
    "他很好",
    "没有关系",
    "你吃饭了吗",
]
for text in ZH_CASES:
    got = u.detect_language(text)
    check(f"「{text}」-> zh", got == "zh", got)

print()
print("=" * 74)
print("三、其他文字系统")
print("=" * 74)

OTHER = [
    ("Привет, как дела?", "ru", "俄语"),
    ("สวัสดีครับ", "th", "泰语"),
    ("مرحبا بك", "ar", "阿拉伯语"),
    ("नमस्ते", "hi", "印地语"),
    ("Γεια σου", "el", "希腊语"),
]
for text, exp, desc in OTHER:
    got = u.detect_language(text)
    check(f"{desc}「{text[:15]}」-> {exp}", got == exp, got)

print()
print("=" * 74)
print("四、原有语言不回退")
print("=" * 74)

ORIG = [
    ("こんにちは、今日はいい天気ですね。", "ja"),
    ("今日はいい天気ですね", "ja"),
    ("私は学生です", "ja"),
    ("ラーメン", "ja"),
    ("今天天气不错", "zh"),
    ("Hello how are you", "en"),
    ("안녕하세요", "ko"),
]
for text, exp in ORIG:
    got = u.detect_language(text)
    check(f"「{text[:16]}」-> {exp}", got == exp, got)

print()
print("=" * 74)
print("五、fallback 参数")
print("=" * 74)

check("空文本返回 fallback(zh)", u.detect_language("") == "zh")
check("空文本返回 fallback(ja)", u.detect_language("", fallback="ja") == "ja")
check("纯数字返回 fallback(zh)", u.detect_language("12345") == "zh")
check("纯数字返回 fallback(ja)", u.detect_language("12345", fallback="ja") == "ja")
check("纯符号返回 fallback(ko)", u.detect_language("!!!", fallback="ko") == "ko")

print()
print("=" * 74)
print("六、语言名称映射")
print("=" * 74)

for code in ["zh", "ja", "en", "ko", "yue", "ru", "th"]:
    name = u.LANGUAGE_NAMES.get(code)
    check(f"{code} 有显示名", name is not None, str(name))

print()
print("=" * 74)
passed = sum(1 for _, c, _ in results if c)
print(f"语言检测测试: {passed}/{len(results)} 通过")
print("=" * 74)
