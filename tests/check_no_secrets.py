# -*- coding: utf-8 -*-
"""上传 / 提交前体检：别把凭证和个人信息带进公开仓库。

    python tests\\check_no_secrets.py

分两级：
  * **FAIL（退出码 1）** —— 疑似凭证、完整手机号、邮箱。必须改掉才能推。
  * **WARN** —— 硬编码的绝对路径。启动器里出现是正常的（README 已说明要改），
              但主程序和测试里出现就值得看一眼。

为什么需要这个：本项目要读本机登录态，开发过程中很容易顺手把一条
带 token 的 curl、一段调试打印、一张带真实账号的截图留在目录里。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv", "venv"}
TEXT_EXT = (".py", ".md", ".vbs", ".bat", ".txt", ".json", ".yml", ".yaml", "")

# ---- FAIL 级：出现即必须处理 ----
FATAL = {
    "疑似 Bearer token": re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{20,}"),
    "疑似赋值型密钥": re.compile(
        r"(?:access_token|authorization|cookie|secret|password|api[_-]?key)"
        r"\s*[=:]\s*[\"'][A-Za-z0-9_\-\.]{16,}[\"']", re.I),
    "完整手机号": re.compile(r"(?<![\d*])1[3-9]\d{9}(?![\d*])"),
    "邮箱地址": re.compile(r"[\w\.\-]+@(?:qq|163|126|gmail|outlook|foxmail)\.\w+", re.I),
    "JWT": re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
}

# ---- WARN 级：看一眼就行 ----
WARN = {
    "硬编码用户路径": re.compile(r"C:\\+Users\\+(?!<)[A-Za-z0-9_\-\.]+"),
    "硬编码盘符路径": re.compile(r"(?<![A-Za-z])[DE]:\\+[A-Za-z0-9_\u4e00-\u9fa5\\\-\.]{4,}"),
}

# 允许出现的位置（启动器/文档里说明"要改成你自己的"）
WARN_ALLOW = {"README.md", "README.en.md", "详细使用手册.md", "发布文案.md"}


def iter_files():
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in sorted(filenames):
            if not fn.lower().endswith(TEXT_EXT):
                continue
            if fn in ("LICENSE",):
                continue
            yield os.path.join(dirpath, fn)


def main():
    fatal_hits, warn_hits = [], []

    for path in iter_files():
        rel = os.path.relpath(path, ROOT)
        try:
            text = open(path, encoding="utf-8", errors="replace").read()
        except Exception:
            continue
        for label, pat in FATAL.items():
            for m in pat.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                fatal_hits.append((rel, line, label, m.group(0)[:60]))
        for label, pat in WARN.items():
            if os.path.basename(rel) in WARN_ALLOW:
                continue
            for m in pat.finditer(text):
                line = text.count("\n", 0, m.start()) + 1
                warn_hits.append((rel, line, label, m.group(0)[:60]))

    print("扫描目录：%s\n" % ROOT)

    if fatal_hits:
        print("!! 必须处理（%d 处）" % len(fatal_hits))
        for rel, line, label, txt in fatal_hits:
            print("   %s:%d  [%s]  %s" % (rel, line, label, txt))
        print()
    else:
        print("OK  没有发现凭证 / 完整手机号 / 邮箱\n")

    if warn_hits:
        print(".. 建议看一眼（%d 处，硬编码绝对路径）" % len(warn_hits))
        seen = set()
        for rel, line, label, txt in warn_hits:
            key = (rel, label)
            if key in seen:
                continue
            seen.add(key)
            print("   %-34s [%s]  e.g. %s:%d" % (rel, label, rel, line))
        print()

    if fatal_hits:
        print("结论：有 %d 处必须改掉才能推。" % len(fatal_hits))
        return 1
    print("结论：可以推。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
