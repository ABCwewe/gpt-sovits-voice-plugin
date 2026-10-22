"""底模版本选择与语言白名单测试。

重点覆盖两处容易出错的地方：
1. GPT-SoVITS 根目录常带版本名（如 ``GPT-SoVITS-v2pro-xxxx``），
   不能让它污染从权重路径推断版本的结果。
2. 配置里手填的版本必须大小写不敏感，且优先于路径推断。
"""

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
print("一、版本名归一化（大小写不敏感）")
print("=" * 74)

CASES = [
    ("auto", "auto"),
    ("AUTO", "auto"),
    (" Auto ", "auto"),
    ("v2ProPlus", "v2ProPlus"),
    ("v2proplus", "v2ProPlus"),
    ("V2PROPLUS", "v2ProPlus"),
    ("v2Pro", "v2Pro"),
    ("v2pro", "v2Pro"),
    ("V1", "v1"),
    ("v5turbo", "v5turbo"),
    ("v5Turbo", "v5turbo"),
    ("", ""),
    (None, ""),
    ("不存在的版本", ""),
]
for raw, want in CASES:
    got = u.normalize_version(raw)
    check(f"normalize_version({raw!r}) -> {want!r}", got == want, got)

print()
print("=" * 74)
print("二、从权重路径推断（父目录 / 文件名）")
print("=" * 74)

PATH_CASES = [
    # 正常情况：父目录带版本
    ("SoVITS_weights_v2ProPlus/azuma3_e8_s368.pth", "v2ProPlus"),
    ("SoVITS_weights_v2Pro/azuma_e10.pth", "v2Pro"),
    ("SoVITS_weights_v1/azuma_e10.pth", "v1"),
    ("SoVITS_weights_v3/azuma_e10.pth", "v3"),
    ("SoVITS_weights_v4/azuma_e10.pth", "v4"),
    ("SoVITS_weights_v5turbo/azuma_e10.pth", "v5turbo"),
    ("SoVITS_weights_v5dev/azuma_e10.pth", "v5dev"),
    ("SoVITS_weights_v2/azuma_e10.pth", "v2"),
    # Windows 反斜杠
    (r"SoVITS_weights_v2ProPlus\azuma3_e8_s368.pth", "v2ProPlus"),
    # 绝对路径：根目录的 v2pro 不能污染 v1 权重
    (
        r"D:\GPT-SoVITS-v2pro-20261006nvidia50x0\SoVITS_weights_v1\azuma_e10.pth",
        "v1",
    ),
    # 父目录无版本，靠文件名
    ("weights/azuma_v2ProPlus_e10.pth", "v2ProPlus"),
    # 长 token 优先，避免 v2ProPlus 被当成 v2Pro
    ("SoVITS_weights_v2ProPlus/azuma.pth", "v2ProPlus"),
    # 完全看不出版本
    ("weights/my_model.pth", ""),
    ("", ""),
]
for path, want in PATH_CASES:
    got = u.version_from_weights(path)
    check(f"{path!r} -> {want!r}", got == want, got)

print()
print("=" * 74)
print("三、resolve_version 优先级：显式 > SoVITS > GPT")
print("=" * 74)

# 显式配置优先于路径
got = u.resolve_version("v1", sovits_weights="SoVITS_weights_v2ProPlus/x.pth")
check("显式 v1 覆盖路径的 v2ProPlus", got == "v1", got)

# auto 时走路径推断
got = u.resolve_version("auto", sovits_weights="SoVITS_weights_v2ProPlus/x.pth")
check("auto -> 按路径推断 v2ProPlus", got == "v2ProPlus", got)

# 空配置也走推断
got = u.resolve_version("", sovits_weights="SoVITS_weights_v3/x.pth")
check("空配置 -> 按路径推断 v3", got == "v3", got)

# SoVITS 推断不出时用 GPT
got = u.resolve_version(
    "auto",
    sovits_weights="weights/sovits.pth",
    gpt_weights="GPT_weights_v4/gpt.ckpt",
)
check("SoVITS 无版本 -> 用 GPT 的 v4", got == "v4", got)

# SoVITS 优先于 GPT
got = u.resolve_version(
    "auto",
    sovits_weights="SoVITS_weights_v1/s.pth",
    gpt_weights="GPT_weights_v4/g.ckpt",
)
check("SoVITS 优先于 GPT", got == "v1", got)

# 都推断不出 -> 空
got = u.resolve_version("auto", "a.pth", "b.ckpt")
check("都推断不出 -> 空字符串", got == "", got)

print()
print("=" * 74)
print("四、各版本语言白名单")
print("=" * 74)

LANG_CASES = [
    # v1 只有中英日，不支持粤语
    ("v1", {"zh", "en", "ja"}),
    # v2 及以上都支持粤语
    ("v2", {"zh", "en", "ja", "yue"}),
    ("v2Pro", {"zh", "en", "ja", "yue"}),
    ("v2ProPlus", {"zh", "en", "ja", "yue"}),
    ("v3", {"zh", "en", "ja", "yue"}),
    ("v4", {"zh", "en", "ja", "yue"}),
    ("v5dev", {"zh", "en", "ja", "yue"}),
    ("v5turbo", {"zh", "en", "ja", "yue"}),
]
for ver, want in LANG_CASES:
    got = u.supported_languages(ver)
    check(f"{ver} 支持 {sorted(want)}", got == want, sorted(got))

# 未知版本 -> 安全集合
got = u.supported_languages("")
check("未知版本 -> 安全集合", got == u.DEFAULT_SUPPORTED_LANGUAGES, sorted(got))
got = u.supported_languages("v9不存在")
check("非法版本名 -> 安全集合", got == u.DEFAULT_SUPPORTED_LANGUAGES, sorted(got))

# 大小写不敏感
got = u.supported_languages("V2PROPLUS")
check("V2PROPLUS 大写也能识别", got == {"zh", "en", "ja", "yue"}, sorted(got))

print()
print("=" * 74)
print("五、is_language_supported 逐语言核对")
print("=" * 74)

# (语言, 版本, 是否支持)
SUPPORT_CASES = [
    ("zh", "v2ProPlus", True),
    ("en", "v2ProPlus", True),
    ("ja", "v2ProPlus", True),
    ("yue", "v2ProPlus", True),
    # 韩语在所有版本上都不可用（缺 eunjeon 会抛内部异常）
    ("ko", "v2ProPlus", False),
    ("ko", "v2", False),
    ("ko", "v3", False),
    ("ko", "v4", False),
    ("ko", "v5turbo", False),
    ("ko", "v1", False),
    # 粤语只在 v2 及以上可用
    ("yue", "v1", False),
    ("yue", "v3", True),
    ("ru", "v2ProPlus", False),   # 所有版本都不支持
    ("ru", "v4", False),
    ("fr", "v2ProPlus", False),
    ("de", "v3", False),
    ("th", "v4", False),
    ("", "v2ProPlus", False),
]
for lang, ver, want in SUPPORT_CASES:
    got = u.is_language_supported(lang, ver)
    check(f"is_language_supported({lang!r}, {ver!r}) -> {want}", got == want, got)

print()
print("=" * 74)
print("六、本机实际权重路径解析")
print("=" * 74)

REAL = "SoVITS_weights_v2ProPlus/azuma3_e8_s368.pth"
got = u.version_from_weights(REAL)
check(f"{REAL} -> v2ProPlus", got == "v2ProPlus", got)
got = u.is_language_supported("ko", u.version_from_weights(REAL))
check("当前模型不允许韩语", got is False, got)

print()
print("=" * 74)
total = len(results)
passed = sum(1 for _, ok, _ in results if ok)
print(f"结果：{passed}/{total} 通过")
if passed != total:
    print("失败项：")
    for name, ok, detail in results:
        if not ok:
            print(f"  - {name}  -> {detail}")
print("=" * 74)
