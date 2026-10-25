# -*- coding: utf-8 -*-
"""「看窗口而不是看进程」的检测测试 —— 针对 2026-10-09 的真实 bug。

现象：关掉 WorkBuddy 窗口后，挂件不跟着关（用户报「没有随着 workbuddy 的关闭而关闭」）。

根因：WorkBuddy 关掉窗口后，有一批后台辅助进程会被**保留最多 10 分钟**：
    codebuddy --prewarm --prewarm-id wb-pool-...            预热池
    ...\\app.asar\\main\\daemon-app-server-entry.js --stdio
    ...\\app.asar\\main\\sidecar-entry.js --token ...
    ...\\windows-child-process-containment.cjs ... --gc_retain_sec 600
它们的映像名同样是 WorkBuddy.exe。旧实现只看"进程在不在"，
于是把这些残留误判成"客户端还开着"，挂件永远等不到 exit_with_workbuddy 生效。

本测试用**真实进程**验证修复后的判据：
  A. 现场事实：WorkBuddy 的主进程有可见窗口，而那些残留辅助进程**一个窗口都没有**
  B. 策略：把 pid 集合换成"活着但没有窗口"的进程 → 必须判为「已关闭」
     （旧实现在这里会返回 True，正好是 bug 的成因）
  C. 边界：pid 集合为空 / 窗口枚举抛异常 / 测试后门优先
"""
import ctypes
import ctypes.wintypes as w
import importlib.util
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(HERE, "wb_credit_widget.py")

spec = importlib.util.spec_from_file_location("wbwin", SRC)
m = importlib.util.module_from_spec(spec)
sys.modules["wbwin"] = m
spec.loader.exec_module(m)

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("PASS" if cond else "FAIL", name,
                       ("  <- " + str(detail)) if detail and not cond else ""))


# ------------------------------------------------------------ 独立枚举一遍窗口（当"标准答案"）
def visible_window_pids():
    """不用被测函数，自己枚举一遍：哪些 pid 拥有可见顶层窗口。"""
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
    u.GetWindowThreadProcessId.restype = w.DWORD
    u.IsWindowVisible.argtypes = [w.HWND]
    u.IsWindowVisible.restype = w.BOOL
    u.EnumWindows.restype = w.BOOL
    got = set()

    @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def _cb(hwnd, _):
        pid = w.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if u.IsWindowVisible(hwnd):
            got.add(int(pid.value))
        return True

    u.EnumWindows(_cb, 0)
    return got


pids = m._wb_pids() or []
print("WorkBuddy.exe 进程：%s" % (pids,))
print()

print("=== A. 现场事实：谁有可见窗口 ===")
if not pids:
    print("（当前 WorkBuddy 没在运行，跳过现场核对）")
else:
    truth = visible_window_pids()
    with_win = sorted(p for p in pids if p in truth)
    without = sorted(p for p in pids if p not in truth)
    print("  有可见窗口的 pid：%s" % with_win)
    print("  没有窗口的 pid  ：%s" % without)
    check("至少有一个 WorkBuddy 进程带可见窗口（客户端 UI）", len(with_win) >= 1)
    check("确实存在「进程活着但没有任何窗口」的残留进程（=bug 的成因）",
          len(without) >= 1, "没有这类进程，本测试就证明不了什么")
    check("被测函数与独立枚举结果一致（窗口对应的 pid 判定一致）",
          sorted(p for p in pids if m._wb_visible_window([p]) is not None) == with_win)

    print("\n=== A2. 只把「没有窗口的残留进程」交给检测 → 必须判为关闭 ===")
    check("残留进程集合判为「窗口已关」", m._wb_visible_window(without) is None)
    # 让 workbuddy_running() 走真实的窗口逻辑（绕开后门与 _wc 兜底）
    orig = m._wb_pids
    m._wb_pids = lambda: without
    try:
        got = m.workbuddy_running()
        check("workbuddy_running() 对「只剩残留进程」返回 False", got is False,
              "got=%r（这就是挂件关不掉的原因）" % (got,))
    finally:
        m._wb_pids = orig

print("\n=== B. 策略：进程活着但没有窗口 → 判为关闭 ===")
# 造一个「活着、没有窗口、但确实是个进程」的东西当残留进程的替身
ping = subprocess.Popen([r"C:\Windows\System32\ping.exe", "-n", "60", "127.0.0.1"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(0.6)
try:
    orig = m._wb_pids
    m._wb_pids = lambda: [ping.pid]
    try:
        check("无窗口进程 -> 判为关闭", m.workbuddy_running() is False,
              "got=%r" % (m.workbuddy_running(),))
        m._wb_pids = lambda: []
        check("没有进程 -> 判为关闭", m.workbuddy_running() is False,
              "got=%r" % (m.workbuddy_running(),))
        # 窗口枚举炸了也不能把判断搞崩，退回「进程在就算开着」是安全的保守行为
        m._wb_pids = lambda: [ping.pid]
        orig_vis = m._wb_visible_window
        m._wb_visible_window = lambda p: (_ for _ in ()).throw(OSError("boom"))
        try:
            got = m.workbuddy_running()
            check("窗口枚举失败时不崩、且保守退回进程判断", got is True, "got=%r" % (got,))
        finally:
            m._wb_visible_window = orig_vis
    finally:
        m._wb_pids = orig
finally:
    try:
        ping.kill()
    except Exception:
        pass

print("\n=== C. 测试后门仍然优先 ===")
os.environ["WB_WIDGET_FAKE_WB"] = "0"
try:
    check("FAKE_WB=0 -> False（哪怕 WorkBuddy 正开着）", m.workbuddy_running() is False)
    os.environ["WB_WIDGET_FAKE_WB"] = "1"
    check("FAKE_WB=1 -> True（哪怕进程都没有）", m.workbuddy_running() is True)
finally:
    os.environ.pop("WB_WIDGET_FAKE_WB", None)

print("\n=== D. 最小化不算关闭（README 里的承诺，必须真的验一次）===")
# 这一条如果错了，后果是「用户最小化 WorkBuddy，挂件就自己退出了」——
# 属于会让人以为程序坏了的那类 bug，而且只看代码看不出来。
try:
    import tkinter as tk
    root = tk.Tk()
    root.title("WBMinimizeProbe")
    root.geometry("260x140+320+320")
    root.update()
    try:
        check("窗口显示时判为可见",
              m._wb_visible_window([os.getpid()]) is not None)
        root.iconify()                       # 最小化到任务栏
        time.sleep(0.8)
        root.update()
        check("最小化后仍判为可见（IsWindowVisible 对最小化窗口为真）",
              m._wb_visible_window([os.getpid()]) is not None,
              "被判成已关闭了 —— 最小化会被误当成关窗")
    finally:
        root.destroy()
except Exception as e:
    check("最小化语义测试没跑起来", False, repr(e))

print("\n" + "=" * 50)
print("通过 %d / %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败：" + ", ".join(FAIL))
sys.exit(1 if FAIL else 0)