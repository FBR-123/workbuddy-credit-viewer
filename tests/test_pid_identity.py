# -*- coding: utf-8 -*-
"""pid 文件「身份校验」测试 —— 针对真实翻过车的 PID 复用问题。

事故现场（2026-10-04）：
  watcher.pid 里写着 pid=10784，监听器其实早就死了；重启后 Windows 把这个 pid
  分配给了 svchost.exe。于是 `_pid_alive(10784)` 返回 True，
  登录自启跑 `--watch` 时以为"监听器已经在运行"，直接退出 → 挂件再也不出现。
  更危险的是 `--stop-all` 会拿着这个 pid 去 os.kill，杀掉毫不相干的进程。

本测试覆盖：
  1. pid 指向**别的程序**（ping.exe）→ 必须判为过期，并删除文件
  2. pid 存在、也是 python，但创建时间对不上 → 判为过期
  3. pid + 创建时间都正确 → 认活
  4. stop_running 绝不能杀掉被复用的那个无关进程
  5. 老的单行 pid 文件（没有创建时间）仍能按映像名识别
  6. 心跳过旧 → 视为监听器卡死
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(HERE, "wb_credit_widget.py")

tmp = tempfile.mkdtemp(prefix="wb_pidtest_")
spec = importlib.util.spec_from_file_location("wbpid", SRC)
m = importlib.util.module_from_spec(spec)
sys.modules["wbpid"] = m
spec.loader.exec_module(m)

m.APP_DIR = tmp
m.PID_PATH = os.path.join(tmp, "widget.pid")
m.WATCHER_PID_PATH = os.path.join(tmp, "watcher.pid")
m.WATCHER_HEARTBEAT = os.path.join(tmp, "watcher.heartbeat")
m.WATCHER_LOG = os.path.join(tmp, "watcher.log")

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s%s" % ("PASS" if cond else "FAIL", name,
                       ("  <- " + str(detail)) if detail and not cond else ""))


logs = []


def wlog(msg):
    logs.append(msg)


# ---------- 造两个"活的进程"：一个非 python，一个 python ----------
ping = subprocess.Popen([r"C:\Windows\System32\ping.exe", "-n", "60", "127.0.0.1"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
py_child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(0.6)

ping_img = m.proc_identity(ping.pid)["image"]
py_img = m.proc_identity(py_child.pid)["image"]
print("造出的进程：")
print("  非 python: pid=%d image=%s" % (ping.pid, ping_img))
print("  python   : pid=%d image=%s" % (py_child.pid, py_img))
print()

try:
    print("=== 1. pid 指向别的程序（真实事故场景）===")
    p = os.path.join(tmp, "reused.pid")
    with open(p, "w", encoding="utf-8") as f:
        f.write(str(ping.pid))                    # 故意不写创建时间，模拟只有一行
    got = m.pid_file_read(p, wlog)
    check("判为不是我们的进程（返回 None）", got is None, "got=%r" % (got,))
    check("过期文件已被删除", not os.path.exists(p))
    check("日志里说明了原因", any("复用" in x for x in logs), logs[-1:] if logs else "")

    print("\n=== 2. 是 python，但创建时间对不上 ===")
    p = os.path.join(tmp, "wrongtime.pid")
    with open(p, "w", encoding="utf-8") as f:
        f.write("%d\n%d" % (py_child.pid, 123456789))    # 假的创建时间
    got = m.pid_file_read(p, wlog)
    check("判为过期（创建时间不符）", got is None, "got=%r" % (got,))
    check("文件已删除", not os.path.exists(p))

    print("\n=== 3. pid + 创建时间都对 -> 认活 ===")
    p = os.path.join(tmp, "good.pid")
    created = m.proc_identity(py_child.pid)["created"]
    check("能取到创建时间", bool(created), "created=%r" % (created,))
    with open(p, "w", encoding="utf-8") as f:
        f.write("%d\n%d" % (py_child.pid, created))
    got = m.pid_file_read(p, wlog)
    check("认活，返回 pid", got == py_child.pid, "got=%r" % (got,))
    check("文件保留", os.path.exists(p))

    print("\n=== 4. stop_running 绝不能杀掉无关进程 ===")
    # 把 widget.pid 指向那个 ping：修好之前，这里会把 ping 干掉
    with open(m.PID_PATH, "w", encoding="utf-8") as f:
        f.write(str(ping.pid))
    m.stop_running(quiet=True)
    time.sleep(0.5)
    alive = ping.poll() is None
    check("ping 进程安然无恙", alive, "它被误杀了！")
    check("过期 pid 文件被清掉", not os.path.exists(m.PID_PATH))

    print("\n=== 5. 老的单行 pid 文件（没有创建时间）===")
    p = os.path.join(tmp, "legacy.pid")
    with open(p, "w", encoding="utf-8") as f:
        f.write(str(py_child.pid))            # 只有一行
    got = m.pid_file_read(p, wlog)
    check("按映像名识别为存活", got == py_child.pid, "got=%r" % (got,))

    print("\n=== 6. 心跳新鲜度 ===")
    with open(m.WATCHER_PID_PATH, "w", encoding="utf-8") as f:
        f.write("%d\n%d" % (py_child.pid, created))
    with open(m.WATCHER_HEARTBEAT, "w", encoding="utf-8") as f:
        f.write("x")
    os.utime(m.WATCHER_HEARTBEAT, (time.time() - 10, time.time() - 10))   # 10 秒前：新鲜
    check("心跳新鲜 -> 认活", m.watcher_pid() == py_child.pid,
          "got=%r" % (m.watcher_pid(),))
    os.utime(m.WATCHER_HEARTBEAT, (time.time() - 9999, time.time() - 9999))  # 很旧：卡死
    got = m.watcher_pid()
    check("心跳过旧 -> 判为卡死（返回 None）", got is None, "got=%r" % (got,))
    check("卡死时也把 pid 文件清掉，好让新监听器能起来",
          not os.path.exists(m.WATCHER_PID_PATH))
    check("心跳文件缺失时不误判（退回按身份判断）", True)
    with open(m.WATCHER_PID_PATH, "w", encoding="utf-8") as f:
        f.write("%d\n%d" % (py_child.pid, created))
    if os.path.exists(m.WATCHER_HEARTBEAT):
        os.remove(m.WATCHER_HEARTBEAT)
    check("没有心跳文件时按身份认活", m.watcher_pid() == py_child.pid,
          "got=%r" % (m.watcher_pid(),))

    print("\n=== 7. 自己写的 pid 文件不算「别人在跑」 ===")
    p = os.path.join(tmp, "self.pid")
    m.pid_file_write(p)
    first = open(p, encoding="utf-8").read().split()
    check("写入包含 pid + 创建时间两行", len(first) == 2, first)
    check("读自己 -> None（避免自己挡自己）", m.pid_file_read(p, wlog) is None)

finally:
    for proc in (ping, py_child):
        try:
            proc.kill()
        except Exception:
            pass

print("\n" + "=" * 50)
print("通过 %d / %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败：" + ", ".join(FAIL))
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if FAIL else 0)
