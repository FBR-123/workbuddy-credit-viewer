# -*- coding: utf-8 -*-
"""全流程实机测试：**真实窗口**消失后，挂件必须自己退出（2026-09-09 的 bug 回归）。

这次不用 `WB_WIDGET_FAKE_WB` 后门，而是走**真实的窗口检测代码**：
造一个"假客户端"进程，它的映像名通过 `WORKBUDDY_PROCESS_NAMES` 被识别成 WorkBuddy，
然后让它**关掉窗口但进程继续活着**——这正是 WorkBuddy 关掉后
（`codebuddy --prewarm` 池 / daemon / `--gc_retain_sec 600` 的 job 容器赖着不走）的真实形态。

断言的核心：窗口消失后，挂件必须在宽限期内退出，**哪怕那个进程还活着**。
修之前这里必然失败（进程还在 → 判定"客户端开着" → 挂件永不退出）。
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
# 跟随当前解释器（见上）
PY_DIR = os.path.dirname(sys.executable)
PY = os.path.join(PY_DIR, "python.exe")
PYW = os.path.join(PY_DIR, "pythonw.exe")

# 复制一份解释器并改名：让它在进程表里的映像名既不是 pythonw 也不是 workbuddy，
# 于是"挂件/监听器自己"不会被误当成 WorkBuddy，只有这个假客户端会被。
FAKE_EXE = os.path.join(PY_DIR, "wbtestclient.exe")
FAKE_NAME = "wbtestclient.exe"

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print("%s %s%s" % ("PASS" if cond else "FAIL", name,
                       ("  <- " + str(detail)) if detail and not cond else ""))


def read_first_pid(path):
    try:
        with open(path, encoding="utf-8") as f:
            return int(f.read().split()[0])
    except Exception:
        return None


def alive(pid):
    try:
        import ctypes
        from ctypes import wintypes as w
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = w.HANDLE
        k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        h = k.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        code = w.DWORD()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259
    except Exception:
        return False


def wait_for(fn, timeout, step=0.5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if fn():
            return time.time() - t0
        time.sleep(step)
    return None


tmp = tempfile.mkdtemp(prefix="wb_winfow_")
app = os.path.join(tmp, "appdata")
os.makedirs(app, exist_ok=True)
copy = os.path.join(tmp, "wb_credit_widget.py")
fclient = os.path.join(tmp, "fakeclient.py")

shown = os.path.join(tmp, "window_shown.txt")
go = os.path.join(tmp, "close_window.txt")
gone = os.path.join(tmp, "window_gone.txt")

# 隔离数据目录
src = open(SRC, encoding="utf-8").read()
src, n = re.subn(r'^APP_DIR = .*$', lambda mo: 'APP_DIR = r"%s"' % app,
                 src, count=1, flags=re.M)
assert n == 1
open(copy, "w", encoding="utf-8").write(src)

spec = importlib.util.spec_from_file_location("wbwinflow", copy)
m = importlib.util.module_from_spec(spec)
sys.modules["wbwinflow"] = m
spec.loader.exec_module(m)
m.APP_DIR = app
m.WIDGET_LOG = os.path.join(app, "widget.log")

# 假客户端：开一个真窗口 → 等测试发话 → 关掉窗口但进程继续活着
open(fclient, "w", encoding="utf-8").write('''# -*- coding: utf-8 -*-
import os, sys, time, tkinter as tk
shown, go, gone = sys.argv[1], sys.argv[2], sys.argv[3]
root = tk.Tk()
root.title("WBTestClient")
root.geometry("300x160+1200+700")
tk.Label(root, text="fake WorkBuddy client").pack(pady=40)
root.update()
open(shown, "w").write(str(os.getpid()))
deadline = time.time() + 120
while time.time() < deadline and not os.path.exists(go):
    root.update()
    time.sleep(0.1)
root.destroy()                      # 窗口没了……
open(gone, "w").write("gone")
time.sleep(300)                     # ……但进程还活着（= prewarm/daemon 残留）
''')

env = dict(os.environ, WORKBUDDY_PROCESS_NAMES=FAKE_NAME)
# 本测试进程自己也要设上：否则 m._wb_pids() 会把**真实**的 WorkBuddy 也算进来，
# 而真客户端此刻正开着窗口（我们就是跑在它里面），"窗口关掉"这条永远测不出来。
# WORKBUDDY_PROCESS_NAMES 是替换语义，设了就只认这些名字。
os.environ["WORKBUDDY_PROCESS_NAMES"] = FAKE_NAME
PID_W = os.path.join(app, "widget.pid")
PID_S = os.path.join(app, "watcher.pid")
LOG_W = os.path.join(app, "widget.log")

print("隔离目录：%s" % tmp)
print("假客户端映像名：%s\n" % FAKE_NAME)

client = watcher = None
try:
    if not os.path.exists(FAKE_EXE):
        shutil.copy2(PYW, FAKE_EXE)
    print("=== 1. 启动假客户端（带真窗口）===")
    client = subprocess.Popen([FAKE_EXE, fclient, shown, go, gone],
                              cwd=tmp, creationflags=0x00000008 | 0x08000000)
    t = wait_for(lambda: os.path.exists(shown), 30)
    check("假客户端窗口已弹出", t is not None, "等待超时")

    print("\n=== 2. 启动监听器（走真实窗口检测，不开后门）===")
    watcher = subprocess.Popen([PYW, copy, "--watch"], env=env, cwd=tmp,
                               creationflags=0x00000008 | 0x08000000)
    check("监听器写出 pid", wait_for(lambda: os.path.exists(PID_S), 20) is not None)
    t = wait_for(lambda: alive(read_first_pid(PID_W) or 0), 45)
    check("挂件被拉起（因为窗口可见）", t is not None, "45 秒内没看到挂件")
    wpid = read_first_pid(PID_W)

    print("\n=== 3. 检测函数此刻应判为「客户端开着」===")
    check("workbuddy_running() == True", m.workbuddy_running() is True,
          "got=%r" % (m.workbuddy_running(),))
    check("它确实在假客户端的 pid 集合里",
          client.pid in (m._wb_pids() or []),
          "pids=%s client.pid=%s" % (m._wb_pids(), client.pid))

    print("\n=== 4. 核心：关掉窗口但进程留着 → 挂件必须退出 ===")
    open(go, "w").write("go")
    check("假客户端窗口已销毁", wait_for(lambda: os.path.exists(gone), 30) is not None)
    check("假客户端进程仍然活着（= 残留进程形态）", alive(client.pid))
    check("此刻检测判为「客户端已关」", m.workbuddy_running() is False,
          "got=%r（这就是关不掉的原因）" % (m.workbuddy_running(),))

    t0 = time.time()
    t = wait_for(lambda: not alive(read_first_pid(PID_W) or 0), 40)
    dt = time.time() - t0
    check("挂件在窗口消失后自己退出了", t is not None, "40 秒内没退出")
    check("退出耗时在 4~25 秒之间（宽限 8 秒 + 2 秒轮询）",
          t is not None and 4 <= dt <= 25, "%.1fs" % dt)
    print("   挂件退出耗时 %.1fs" % dt)

    try:
        log = open(LOG_W, encoding="utf-8").read()
    except Exception:
        log = ""
    check("日志里有「因为窗口关闭而退出」的记录",
          "quitting: WorkBuddy window closed" in log,
          log[-200:] if log else "(没有 widget.log)")
    check("pid 文件已清理", not os.path.exists(PID_W))
    print("   widget.log 末尾：\n%s" % "\n".join(
        "      " + x for x in log.strip().splitlines()[-4:]))

    print("\n=== 5. 收尾 ===")
    subprocess.run([PY, copy, "--stop-all"], env=env, cwd=tmp,
                   capture_output=True, timeout=60)
    time.sleep(2)
    try:
        client.kill()
    except Exception:
        pass
    time.sleep(1.5)
    check("假客户端已收掉", not alive(client.pid))
finally:
    os.environ.pop("WORKBUDDY_PROCESS_NAMES", None)
    for pr in (watcher, client):
        try:
            if pr is not None:
                pr.kill()
        except Exception:
            pass
    try:
        if os.path.exists(FAKE_EXE):
            os.remove(FAKE_EXE)       # 别把测试用的假 exe 留在 Python 安装目录
    except Exception:
        pass
    shutil.rmtree(tmp, ignore_errors=True)

print("\n" + "=" * 50)
bad = [r for r in results if not r[1]]
print("通过 %d / %d" % (len(results) - len(bad), len(results)))
if bad:
    print("失败：" + ", ".join(r[0] for r in bad))
sys.exit(1 if bad else 0)