# -*- coding: utf-8 -*-
"""VBScript / 批处理启动器的静态校验（因为本机跑不了 .vbs）。

    python tests\\check_vbs_syntax.py

为什么需要它：本机安全策略把脚本宿主（WScript/CScript）当 LOLBin 拦了，
`launch.vbs` 这类启动器**没法在这里真跑一遍**。而 VBS 又是那种"错一个字符就
整个不工作、还基本不给提示"的语言，`Option Explicit` 下漏个 Dim 就是运行时
错误 500 —— 用户在登录时只会看到"没反应"。

所以退而求其次，把能静态查的全查掉：
  1. 纯 ASCII / 无 BOM             —— 代码页问题（中文 Windows 下最经典的坑）
  2. 块结构配平                    —— If/End If、Function/End Function、For/Next、Select Case
  3. Option Explicit 下变量必须声明 —— 抓拼写错误
  4. 引用的 .vbs / .py / .bat 存在 —— 抓改名/删文件后没同步
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BUILTIN = {"wscript", "err", "true", "false", "nothing", "empty", "null",
           "vbcrlf", "vblf", "vbtab", "vbnullstring", "now", "date", "time"}

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                       ("  <- " + str(detail)) if detail and not ok else ""))


def strip_comment(line):
    """去掉行尾注释（跳过字符串里的单引号，处理 "" 转义）。"""
    out, in_str, i = [], False, 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            in_str = not in_str
            out.append(ch)
        elif ch == "'" and not in_str:
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def logical_lines(text):
    """把以 _ 续行的物理行拼成一条。"""
    buf = ""
    for raw in text.splitlines():
        s = raw.rstrip()
        if s.endswith("_"):
            buf += s[:-1] + " "
            continue
        buf += s
        if buf.strip():
            yield buf.strip()
        buf = ""
    if buf.strip():
        yield buf.strip()


def analyse(path):
    print("\n=== %s ===" % os.path.relpath(path, ROOT))
    raw = open(path, "rb").read()

    check("纯 ASCII（不受代码页影响）", all(b < 128 for b in raw),
          "含非 ASCII 字节，中文 Windows 下会乱码")
    check("无 UTF-8 BOM", not raw.startswith(b"\xef\xbb\xbf"))
    check("用 CRLF 或 LF 均可，但不含裸 CR", b"\r" not in raw.replace(b"\r\n", b""))

    text = raw.decode("ascii", "replace")
    lines = [strip_comment(l) for l in logical_lines(text)]

    # ---- 块结构 ----
    stack = []
    for ln in lines:
        low = ln.lower()
        if re.match(r"^if\b", low):
            # 只有以 Then 结尾的才是块式 If；`If x Then y` 是一行式
            if low.endswith("then"):
                stack.append(("if", ln))
        elif re.match(r"^elseif\b", low) or low == "else" or low.startswith("else "):
            if not stack or stack[-1][0] != "if":
                check("Else/ElseIf 处于 If 块内", False, ln)
                return
        elif re.match(r"^end\s+if\b", low):
            if not stack or stack[-1][0] != "if":
                check("End If 配平", False, "多余/错位：" + ln)
                return
            stack.pop()
        elif re.match(r"^(function|sub)\b", low):
            stack.append((low.split()[0], ln))
        elif re.match(r"^end\s+(function|sub)\b", low):
            kind = low.split()[1]
            if not stack or stack[-1][0] != kind:
                check("End %s 配平" % kind.capitalize(), False, ln)
                return
            stack.pop()
        elif re.match(r"^for\b", low):
            stack.append(("for", ln))
        elif re.match(r"^next\b", low):
            if not stack or stack[-1][0] != "for":
                check("Next 配平", False, ln)
                return
            stack.pop()
        elif re.match(r"^do\b", low):
            stack.append(("do", ln))
        elif re.match(r"^loop\b", low):
            if not stack or stack[-1][0] != "do":
                check("Loop 配平", False, ln)
                return
            stack.pop()
        elif re.match(r"^select\s+case\b", low):
            stack.append(("select", ln))
        elif re.match(r"^end\s+select\b", low):
            if not stack or stack[-1][0] != "select":
                check("End Select 配平", False, ln)
                return
            stack.pop()
    check("块结构全部配平", not stack,
          "未闭合：" + ", ".join("%s(%s)" % (k, v[:40]) for k, v in stack))

    # ---- Option Explicit ----
    explicit = any(l.strip().lower() == "option explicit" for l in lines)
    check("声明了 Option Explicit", explicit)

    if explicit:
        declared = set()
        for ln in lines:
            low = ln.lower()
            m = re.match(r"^dim\s+(.+)$", low)
            if m:
                declared |= {x.strip() for x in m.group(1).split(",") if x.strip()}
                continue
            m = re.match(r"^const\s+([a-z_]\w*)", low)
            if m:
                declared.add(m.group(1))
                continue
            m = re.match(r"^(?:function|sub)\s+([a-z_]\w*)\s*\(([^)]*)\)", low)
            if m:
                # 函数名本身也算"已声明"：VBS 里 `Fn = value` 是给返回值赋值
                declared.add(m.group(1))
                for p in m.group(2).split(","):
                    p = p.strip()
                    if p:
                        declared.add(p.split()[0])
                continue
            m = re.match(r"^for\s+each\s+([a-z_]\w*)", low)
            if m:
                declared.add(m.group(1))

        undeclared = []
        for ln in lines:
            m = re.match(r"^(?:set\s+)?([A-Za-z_]\w*)\s*=", ln)
            if not m:
                continue
            name = m.group(1)
            if name.lower() in BUILTIN or "." in name:
                continue
            if name.lower() not in declared and name not in declared:
                undeclared.append(name)
        check("所有被赋值的变量都已声明", not undeclared,
              "未声明：" + ", ".join(sorted(set(undeclared))))

    # ---- 引用的文件存在 ----
    missing = []
    for m in re.finditer(r'"([^"]*\.(?:vbs|py|bat))"', text, re.I):
        ref = m.group(1)
        if "\\" in ref or "/" in ref:
            continue                       # 运行时拼出来的路径，静态查不了
        if not os.path.exists(os.path.join(ROOT, ref)):
            missing.append(ref)
    check("引用的脚本文件都存在", not missing, missing)

    # ---- 架构约束：.vbs 里不许有逻辑 ----
    # 本机跑不了脚本宿主，所以 .vbs 的正确性只能靠"足够简单"来保证。
    # 所有判断/循环/函数都必须下沉到可执行的 .bat 里。
    logic = re.compile(r"^\s*(if|else|elseif|for|do|loop|while|select\s+case|"
                       r"function|sub|end\s+if|end\s+select|next)\b", re.I)
    offenders = [ln for ln in lines if logic.match(ln)]
    check(".vbs 里没有控制流（逻辑都在对应的 .bat 里）", not offenders,
          offenders[:3])
    check("引用了同名/对应的 .bat", ".bat" in text.lower())


def main():
    vbs = sorted(f for f in os.listdir(ROOT) if f.lower().endswith(".vbs"))
    if not vbs:
        print("没找到 .vbs 文件")
        return 1
    print("仓库：%s" % ROOT)
    for f in vbs:
        analyse(os.path.join(ROOT, f))

    print("\n" + "=" * 50)
    bad = [r for r in RESULTS if not r[1]]
    print("通过 %d / %d" % (len(RESULTS) - len(bad), len(RESULTS)))
    if bad:
        print("失败：")
        for name, _, detail in bad:
            print("  - %s%s" % (name, ("  <- " + str(detail)) if detail else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
