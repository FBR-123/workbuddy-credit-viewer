# -*- coding: utf-8 -*-
"""端到端验证：--managed 的挂件在 WorkBuddy 消失后会不会自己结束进程。

用隔离副本（APP_DIR 指向临时目录）+ WB_WIDGET_FAKE_WB 后门控制"WorkBuddy 在不在"，
绝不碰真实 state.json / config.json。
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "wb_credit_widget.py")
# 跟随当前解释器：测试必须用带 tkinter 的系统 Python 跑，
# 所以同目录下的 pythonw.exe 就是我们要的。不写死任何人的家目录。
PYW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")

tmp = tempfile.mkdtemp(prefix="wb_life_")
app = os.path.join(tmp, "appdata")
os.makedirs(app, exist_ok=True)
copy = os.path.join(tmp, "wb_credit_widget.py")

src = open(SRC, encoding="utf-8").read()
# 隔离数据目录：换掉 APP_DIR 那一行
new, n = re.subn(r'^APP_DIR = .*$',
                 lambda mo: 'APP_DIR = r"%s"' % app, src, count=1, flags=re.M)
assert n == 1, "没找到 APP_DIR 行"
# 退出时留个标记，方便区分"自己退的"还是"被沙箱回收的"
new = new.replace("        self.root.destroy()",
                  "        open(os.path.join(APP_DIR, 'quit.marker'), 'w').write('bye')\n"
                  "        self.root.destroy()", 1)
open(copy, "w", encoding="utf-8").write(new)
print("隔离副本：%s" % copy)
print("隔离数据目录：%s" % app)

results = []


def spawn(fake, managed=True):
    cmd = [PYW, copy] + (["--managed"] if managed else [])
    env = dict(os.environ, WB_WIDGET_FAKE_WB=fake)
    return subprocess.Popen(cmd, env=env, cwd=tmp,
                            creationflags=0x00000008 | 0x08000000)


def alive(p):
    return p.poll() is None


print("\n=== A. WorkBuddy 不存在 + --managed -> 应该自己结束进程 ===")
p = spawn("0")
t0 = time.time()
try:
    p.wait(timeout=40)
    dt = time.time() - t0
    print("PASS 进程自行退出，耗时 %.1f 秒，退出码 %s" % (dt, p.returncode))
    results.append(("A1 自行退出", True))
    results.append(("A2 退出耗时在 5~25 秒之间（=宽限 8 秒 + 轮询）", 5 <= dt <= 25, "%.1fs" % dt))
except subprocess.TimeoutExpired:
    p.kill()
    print("FAIL 40 秒了还没退出")
    results.append(("A1 自行退出", False, "超时"))

marker = os.path.join(app, "quit.marker")
results.append(("A3 留下了 quit 标记（说明是走 quit() 正常退的，不是被强杀）",
                os.path.exists(marker)))
results.append(("A4 pid 文件已被清理", not os.path.exists(os.path.join(app, "widget.pid"))))
print("quit 标记：%s" % ("有" if os.path.exists(marker) else "无"))

print("\n=== B. WorkBuddy 存在 + --managed -> 应该一直活着 ===")
p2 = spawn("1")
time.sleep(14)
still = alive(p2)
print("%s 14 秒后仍在运行" % ("PASS" if still else "FAIL"))
results.append(("B1 WorkBuddy 在时不退出", still))
if still:
    p2.kill()
    try:
        p2.wait(timeout=5)
    except Exception:
        pass

print("\n=== C. WorkBuddy 不存在 + 非 --managed -> 不算托管，不该自己退 ===")
p3 = spawn("0", managed=False)
time.sleep(12)
alive3 = alive(p3)
print("%s 手动启动的挂件不会自己退出（%s）"
      % ("PASS" if alive3 else "FAIL", "仍在运行" if alive3 else "已退出"))
results.append(("C1 手动启动不受托管规则影响", alive3))
if alive3:
    p3.kill()
    try:
        p3.wait(timeout=5)
    except Exception:
        pass

print("\n" + "=" * 46)
bad = [r for r in results if not r[1]]
for name, ok, *rest in results:
    print("%s %s%s" % ("PASS" if ok else "FAIL", name, ("  <- " + rest[0]) if rest and not ok else ""))
print("通过 %d / %d" % (len(results) - len(bad), len(results)))
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if bad else 0)
