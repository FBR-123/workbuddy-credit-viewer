# -*- coding: utf-8 -*-
"""全链路实机测试：监听器与挂件都用**真实生产代码**，只把数据目录隔离到临时目录。

用文件型后门 WB_WIDGET_FAKE_WB=<file> 动态模拟「WorkBuddy 在/不在」，
跑完整一轮：目标出现→拉起→目标消失→优雅退出→目标回来→再拉起。

这样能一次性验证：
  A. 目标出现时监听器确实会拉起挂件（真实 Popen，真实 --managed）
  B. 目标消失后挂件走的是优雅退出（有 quit.marker 为证，不是被强杀）
  C. 监听器自己不会跟着死（它还要等下一轮）
  D. 整个循环**可以重复**（不是一次性的）
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(HERE, "wb_credit_widget.py")
# 跟随当前解释器：测试必须用带 tkinter 的系统 Python 跑，
# 所以同目录下的 pythonw.exe 就是我们要的。不写死任何人的家目录。
PYW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")

tmp = tempfile.mkdtemp(prefix="wb_pipe_")
app = os.path.join(tmp, "appdata")
os.makedirs(app, exist_ok=True)
copy = os.path.join(tmp, "wb_credit_widget.py")
fake = os.path.join(tmp, "fake_wb.txt")
marker = os.path.join(app, "quit.marker")

src = open(SRC, encoding="utf-8").read()
src, n = re.subn(r'^APP_DIR = .*$', lambda mo: 'APP_DIR = r"%s"' % app,
                 src, count=1, flags=re.M)
assert n == 1, "没找到 APP_DIR 行"
# 在优雅退出路径上留个印记，用来区分"自己退的"和"被强杀的"
src = src.replace("        self.root.destroy()",
                  "        open(os.path.join(APP_DIR, 'quit.marker'), 'w').write('bye')\n"
                  "        self.root.destroy()", 1)
assert "quit.marker" in src, "注入标记失败"
open(copy, "w", encoding="utf-8").write(src)

# 把模块也导进来，复用它的 _pid_alive
spec = importlib.util.spec_from_file_location("wbpipe", copy)
m = importlib.util.module_from_spec(spec)
sys.modules["wbpipe"] = m
spec.loader.exec_module(m)

PID_W = os.path.join(app, "widget.pid")
PID_S = os.path.join(app, "watcher.pid")

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print("%s %s%s" % ("PASS" if cond else "FAIL", name,
                       ("  <- " + str(detail)) if detail and not cond else ""))


def set_fake(v):
    with open(fake, "w", encoding="utf-8") as f:
        f.write(v)


def read_pid(p):
    # pid 文件现在是两行：第一行 pid，第二行进程创建时间（防 PID 复用）。
    # 老测试只取整份内容会 ValueError，这里改成只读第一行。
    try:
        with open(p, encoding="utf-8") as f:
            return int(f.read().split()[0])
    except Exception:
        return None


def live(p):
    pid = read_pid(p)
    return bool(pid and m._pid_alive(pid)), pid


def wait_for(fn, timeout, step=1.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if fn():
            return time.time() - t0
        time.sleep(step)
    return None


print("隔离目录：%s\n" % tmp)
set_fake("1")
env = dict(os.environ, WB_WIDGET_FAKE_WB=fake)

print("=== 启动监听器（真实进程，目标=在）===")
watcher = subprocess.Popen([PYW, copy, "--watch"], env=env, cwd=tmp,
                           creationflags=0x00000008 | 0x08000000)
t = wait_for(lambda: os.path.exists(PID_S), 20)
check("监听器写出了自己的 pid", t is not None, "等待超时")
ok, wpid = live(PID_S)
check("监听器进程存活", ok, "pid=%s" % wpid)
print("监听器 pid=%s（%.1fs）" % (wpid, t or -1))

print("\n=== A. 目标出现 -> 应该把挂件拉起来 ===")
t = wait_for(lambda: live(PID_W)[0], 45)
check("挂件被自动拉起", t is not None, "45 秒内没看到挂件进程")
ok, gpid = live(PID_W)
check("挂件进程存活", ok, "pid=%s" % gpid)
print("挂件 pid=%s（%.1fs）" % (gpid, t or -1))

print("\n=== A2. 挂件在跑的时候，监听器不该重复拉起 ===")
time.sleep(8)
ok2, gpid2 = live(PID_W)
check("挂件 pid 没被换掉（不重复拉起）", ok2 and gpid2 == gpid,
      "before=%s after=%s" % (gpid, gpid2))

print("\n=== B. 目标消失 -> 挂件应该自己优雅退出 ===")
set_fake("0")
t0 = time.time()
t = wait_for(lambda: not live(PID_W)[0], 45)
dt = time.time() - t0
check("挂件进程已结束", t is not None, "45 秒内没退出")
check("退出耗时在 5~30 秒之间（宽限 8 秒 + 轮询 + 兜底 15 秒）", t is not None and 5 <= dt <= 30,
      "%.1fs" % dt)
check("留下了 quit.marker（= 走的是优雅退出，不是被强杀）", os.path.exists(marker))
print("挂件退出耗时 %.1f 秒，quit.marker=%s" % (dt, "有" if os.path.exists(marker) else "无"))

print("\n=== C. 监听器自己不该跟着死 ===")
ok, wpid2 = live(PID_S)
check("监听器仍存活", ok, "pid=%s" % wpid2)

print("\n=== D. 目标回来 -> 应该能再拉起一轮（循环可重复）===")
set_fake("1")
try:
    os.remove(marker)
except Exception:
    pass
t0 = time.time()
t = wait_for(lambda: live(PID_W)[0], 70)
dt2 = time.time() - t0
check("挂件被第二次拉起", t is not None, "70 秒内没看到挂件")
if t:
    ok, gpid3 = live(PID_W)
    check("第二次的挂件是存活的新进程", ok and gpid3 != gpid,
          "first=%s second=%s" % (gpid, gpid3))
    print("第二轮挂件 pid=%s（%.1fs）" % (gpid3, dt2))

print("\n=== 收尾：停掉监听器，挂件应随之被清理 ===")
subprocess.run([PYW.replace("pythonw", "python"), copy, "--stop-all"],
               env=env, cwd=tmp, capture_output=True, timeout=60)
time.sleep(3)
ok_w, _ = live(PID_S)
ok_g, _ = live(PID_W)
check("监听器已停", not ok_w)
check("挂件已停", not ok_g)

print("\n" + "=" * 50)
bad = [r for r in results if not r[1]]
print("通过 %d / %d" % (len(results) - len(bad), len(results)))
if bad:
    print("失败：" + ", ".join(r[0] for r in bad))
for p in (watcher,):
    try:
        p.kill()
    except Exception:
        pass
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if bad else 0)
