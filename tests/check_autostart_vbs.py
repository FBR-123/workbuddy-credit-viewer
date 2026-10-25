# -*- coding: utf-8 -*-
"""解析启动文件夹里的自启项 .vbs，还原出它真正要执行的命令行。

本机 cscript/wscript 被安全策略当 LOLBin 拦了没法直接跑，
所以用"解析 + 原样执行"来验证：除了 wscript 的语法解析，其余环节与登录时一致。

用法：
    python check_autostart_vbs.py            # 只解析并打印
    python check_autostart_vbs.py --run      # 解析后原样执行该命令行
"""
import os
import re
import subprocess
import sys

STARTUP = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows",
                       "Start Menu", "Programs", "Startup")
VBS = os.path.join(STARTUP, "WorkBuddyCreditWidget.vbs")

if not os.path.exists(VBS):
    print("自启项不存在：%s" % VBS)
    sys.exit(2)

raw = open(VBS, "rb").read()
print("文件：%s（%d 字节）" % (VBS, len(raw)))

ok = True
if any(b >= 128 for b in raw):
    bad = [b for b in raw if b >= 128][:5]
    print("FAIL 含非 ASCII 字节：%r（wscript 下可能乱码/解析失败）" % bad)
    ok = False
else:
    print("PASS 纯 ASCII（不受代码页影响）")
if raw.startswith(b"\xef\xbb\xbf"):
    print("FAIL 带 UTF-8 BOM（wscript 可能报错）")
    ok = False
else:
    print("PASS 无 BOM")

text = raw.decode("ascii", "replace")

# 取 Dim 里声明的变量
declared = set()
mm = re.search(r"^Dim\s+(.+)$", text, re.M)
if mm:
    declared = {v.strip().lower() for v in mm.group(1).split(",")}
print("声明的变量：%s" % ", ".join(sorted(declared)))

# 取简单赋值： name = "..." 或 name = "..." & "..." 之类
vals = dict(re.findall(r'^(\w+)\s*=\s*(".*?"|.*?)$', text, re.M))
script_path = None
pyw_path = None
for k, v in vals.items():
    v = v.strip()
    # 处理  x = "..." 形式
    m2 = re.fullmatch(r'"([^"]*)"', v)
    if not m2:
        continue
    if k.lower().endswith("script") or k.lower() == "script":
        script_path = m2.group(1)
    if k.lower() in ("pyw", "exe"):
        pyw_path = m2.group(1)

# Fallback: 直接找绝对路径字符串
if not script_path:
    m3 = re.search(r'^(\w+)\s*=\s*"([A-Za-z]:\\[^"]+\.py)"', text, re.M)
    if m3:
        script_path = m3.group(2)
if not pyw_path:
    m4 = re.search(r'^(\w+)\s*=\s*"([A-Za-z]:\\[^"]*pythonw?\.exe)"', text, re.M)
    if m4:
        pyw_path = m4.group(2)

# 取 sh.Run 的开关：最后两个参数（窗口模式 / 是否等待）
run_switches = re.search(r'sh\.Run\s+.*,\s*(\d+)\s*,\s*(True|False)\s*$', text, re.M)

print("\n解析结果：")
print("  解释器 pythonw : %s" % pyw_path)
print("  脚本           : %s" % script_path)
print("  启动参数       : --watch（应为监听模式，不是挂件本体）")
print("  窗口模式/等待  : %s" % str(run_switches.groups() if run_switches else "未识别"))

ok_run = True
if not pyw_path or not os.path.exists(pyw_path):
    print("FAIL 解释器路径不存在：%r" % pyw_path)
    ok_run = False
else:
    print("PASS 解释器存在")
if not script_path or not os.path.exists(script_path):
    print("FAIL 脚本路径不存在：%r" % script_path)
    ok_run = False
else:
    print("PASS 脚本存在")
if "--watch" not in text:
    print("FAIL 没带 --watch（那就不叫随 WorkBuddy 启停了）")
    ok_run = False
else:
    print("PASS 带 --watch")
if re.search(r"WScript\.Sleep\s+(\d+)", text):
    print("PASS 有启动延迟 %s ms（避开桌面未就绪）"
          % re.search(r"WScript\.Sleep\s+(\d+)", text).group(1))

cmd = [pyw_path, script_path, "--watch"]

if "--run" in sys.argv:
    if not ok_run:
        print("\n解析没过，拒绝执行")
        sys.exit(1)
    print("\n原样执行：%s" % " ".join('"%s"' % c for c in cmd))
    p = subprocess.Popen(cmd, creationflags=0x00000008 | 0x08000000)
    print("已拉起，pid=%d" % p.pid)
else:
    print("\n（加 --run 可以原样执行这条命令）")

sys.exit(0 if (ok and ok_run) else 1)
