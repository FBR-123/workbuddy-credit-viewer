# -*- coding: utf-8 -*-
"""监听器（watch_loop）决策逻辑单元测试。

思路：把模块当库导入，重定向 APP_DIR/PID/WATCHER_PID 到临时目录，
再把 workbuddy_running / launch_widget / kill_widget / widget_pid 换成探针，
最后用 max_ticks 跑有限帧，断言"该拉时拉、该杀时杀、不该动时不动"。
"""
import importlib.util
import os
import shutil
import sys
import tempfile
import time

import os as _os
SRC = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                    "wb_credit_widget.py")

tmp = tempfile.mkdtemp(prefix="wb_watch_test_")
spec = importlib.util.spec_from_file_location("wbmod", SRC)
m = importlib.util.module_from_spec(spec)
sys.modules["wbmod"] = m
spec.loader.exec_module(m)

# ---- 重定向到临时目录，别碰真实状态 ----
m.APP_DIR = tmp
m.PID_PATH = os.path.join(tmp, "widget.pid")
m.WATCHER_PID_PATH = os.path.join(tmp, "watcher.pid")
m.WATCHER_LOG = os.path.join(tmp, "watcher.log")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("PASS" if cond else "FAIL", name, ("  <- " + detail) if detail and not cond else ""))


class FakeChild:
    def __init__(self, alive=True):
        self._alive = alive
        self.pid = 4321
        self.killed = False

    def poll(self):
        return None if self._alive else 0

    def kill(self):
        self._alive = False
        self.killed = True


class Harness:
    """每跑一个场景都重建探针状态。"""

    def __init__(self, running_sequence):
        self.seq = list(running_sequence)
        self.i = 0
        self.launches = 0
        self.kills = 0            # 按 pid 文件去杀别人的次数（新版应为 0）
        self.child = None

    def running(self):
        if self.i < len(self.seq):
            v = self.seq[self.i]
        else:
            v = self.seq[-1] if self.seq else None
        self.i += 1
        return v

    def launch(self):
        self.launches += 1
        self.child = FakeChild(True)
        return self.child

    def kill(self):
        """旧实现会按 pid 文件杀挂件——新版不该再走这条路。"""
        self.kills += 1
        self.child = None
        return True

    @property
    def child_killed(self):
        return bool(self.child and self.child.killed) or self._child_killed

    def run(self, ticks=12, poll=0.01, grace=0.05, cooldown=0.05):
        h = self
        self._child_killed = False
        old = (m.workbuddy_running, m.launch_widget, m.widget_pid)
        m.workbuddy_running = h.running
        m.launch_widget = h.launch
        # 挂件"在不在跑"跟着我们伪造的 child 走
        m.widget_pid = lambda: (1234 if h.child is not None else None)
        try:
            m.watch_loop(poll=poll, kill_grace=grace,
                         relaunch_cooldown=cooldown, max_ticks=ticks)
        finally:
            self._child_killed = bool(h.child and h.child.killed)
            (m.workbuddy_running, m.launch_widget, m.widget_pid) = old


print("=== 场景 1：WorkBuddy 一直在跑，挂件没起来 -> 应该拉起，且不反复拉 ===")
h = Harness([True] * 12)
h.run(ticks=12, cooldown=100.0)          # cooldown 很大 => 只允许拉一次
check("拉起一次", h.launches == 1, "launches=%d" % h.launches)
check("没杀过", h.kills == 0 and not h.child_killed)

print("\n=== 场景 2：WorkBuddy 没跑，一直没跑 -> 不应该拉起任何东西 ===")
h = Harness([False] * 12)
h.run(ticks=12)
check("没拉起", h.launches == 0, "launches=%d" % h.launches)
check("没东西可杀", h.kills == 0 and h.child is None, "kills=%d" % h.kills)

print("\n=== 场景 3：WorkBuddy 跑着 -> 拉起 -> WorkBuddy 退出 -> 结束自己拉起的挂件 ===")
h = Harness([True] * 3 + [False] * 12)
h.run(ticks=15, cooldown=100.0, grace=0.05)
check("拉起一次", h.launches == 1, "launches=%d" % h.launches)
check("退出后杀掉自己拉起的子进程", h.child_killed)
check("没有按 pid 文件去杀别人（只收拾自己的）", h.kills == 0, "kills=%d" % h.kills)
check("杀掉后不再拉起", h.launches == 1, "launches=%d" % h.launches)

print("\n=== 场景 4：WorkBuddy 闪断（退出后又马上回来）-> 不该杀 ===")
h = Harness([True, False, True, True, True, True, True, True])
h.run(ticks=8, cooldown=100.0, grace=5.0)   # grace 5 秒，闪断够短
check("没杀掉", h.kills == 0 and not h.child_killed)
check("没重复拉起", h.launches == 1, "launches=%d" % h.launches)

print("\n=== 场景 5：检测不到（None）-> 什么都不做 ===")
h = Harness([None] * 10)
h.run(ticks=10)
check("没拉起", h.launches == 0, "launches=%d" % h.launches)
check("没杀", h.kills == 0 and not h.child_killed)

print("\n=== 场景 6：挂件已在运行（pid 文件有效）-> 不该重复拉起 ===")
old_wp = m.widget_pid
m.widget_pid = lambda: 999999            # 假装已有挂件
try:
    h2 = Harness([True] * 10)
    old = (m.workbuddy_running, m.launch_widget)
    m.workbuddy_running, m.launch_widget = h2.running, h2.launch
    try:
        m.watch_loop(poll=0.01, kill_grace=0.05, relaunch_cooldown=0.05, max_ticks=10)
    finally:
        (m.workbuddy_running, m.launch_widget) = old
finally:
    m.widget_pid = old_wp
check("没重复拉起", h2.launches == 0, "launches=%d" % h2.launches)

print("\n=== 场景 7：start_watcher_now 幂等（已有监听器时不重复起）===")
with open(m.WATCHER_PID_PATH, "w") as f:
    f.write(str(os.getpid()))
m.watcher_pid = lambda: 4242
try:
    r = m.start_watcher_now()
    check("已有监听器则直接返回其 pid", r == 4242, "r=%r" % (r,))
finally:
    try:
        os.remove(m.WATCHER_PID_PATH)
    except Exception:
        pass

print("\n=== 场景 8：自启项安装/移除（纯 ASCII 写盘 + 原子替换）===")
m.STARTUP_DIR = os.path.join(tmp, "Startup")
m.STARTUP_VBS = os.path.join(m.STARTUP_DIR, "WorkBuddyCreditWidget.vbs")
p = m.install_autostart()
raw = open(p, "rb").read()
check("文件已生成", os.path.exists(p))
check("内容是纯 ASCII", all(b < 128 for b in raw), "发现有非 ASCII 字节")
check("含 --watch", b"--watch" in raw)
check("是 ASCII 而不是 UTF-8 BOM", not raw.startswith(b"\xef\xbb\xbf"))
check("检测到已安装", m.autostart_installed() is True)
check("移除成功", m.remove_autostart() is True and not m.autostart_installed())

print("\n=== 场景 9：重复安装不会把文件写坏（原子替换）===")
m.install_autostart()
a = open(p, "rb").read()
m.install_autostart()
b = open(p, "rb").read()
check("两次安装内容一致", a == b and len(b) > 0)

print("\n" + "=" * 46)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项：" + ", ".join(FAIL))
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if FAIL else 0)
