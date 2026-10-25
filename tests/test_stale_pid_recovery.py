# -*- coding: utf-8 -*-
"""复现并回归「PID 被复用导致监听器拒绝启动」这个真实事故。

事故经过（2026-10-04）：硬关机/重启时监听器来不及清理 watcher.pid，
留下的那个 pid 被 svchost.exe 复用。登录时启动项跑 --watch，
`_pid_alive(10784)` 返回 True → 监听器以为"前一个还在跑"→ 静默退出
→ 挂件永远不出现。用户看到的现象就是"这次怎么没出现窗口"。

本测试跑**真实生产代码 + 真实进程**，只把数据目录隔离到临时目录，
按三种"脏 pid 文件"分别验证：现在都必须能正常启动。
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

results = []


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print("%s %s%s" % ("PASS" if cond else "FAIL", name,
                       ("  <- " + str(detail)) if detail and not cond else ""))


def make_env(fake):
    tmp = tempfile.mkdtemp(prefix="wb_stale_")
    app = os.path.join(tmp, "appdata")
    os.makedirs(app, exist_ok=True)
    copy = os.path.join(tmp, "wb_credit_widget.py")
    src = open(SRC, encoding="utf-8").read()
    src, n = re.subn(r'^APP_DIR = .*$', lambda mo: 'APP_DIR = r"%s"' % app,
                     src, count=1, flags=re.M)
    assert n == 1
    open(copy, "w", encoding="utf-8").write(src)
    return tmp, app, copy


def read_first_pid(path):
    try:
        with open(path, encoding="utf-8") as f:
            return int(f.read().split()[0])
    except Exception:
        return None


def alive(pid):
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259
    except Exception:
        return False


def wait_for(fn, timeout, step=1.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if fn():
            return time.time() - t0
        time.sleep(step)
    return None


def scenario(title, pidfile_text, expect_watcher_starts, decoy=None):
    """跑一轮：写好脏 pid 文件 → 启动真实 --watch → 看它有没有正常起来。"""
    print("\n=== %s ===" % title)
    tmp, app, copy = make_env(fake=None)
    fake = os.path.join(tmp, "fake_wb.txt")
    open(fake, "w", encoding="utf-8").write("1")
    env = dict(os.environ, WB_WIDGET_FAKE_WB=fake)

    pid_s = os.path.join(app, "watcher.pid")
    pid_w = os.path.join(app, "widget.pid")
    # 脏文件要趁启动之前先放好，模拟"重启后残留"
    formatted = pidfile_text % {"pid": decoy.pid if decoy else 0}
    with open(pid_s, "w", encoding="utf-8") as f:
        f.write(formatted)
    stale_pid = read_first_pid(pid_s)
    print("  预置 watcher.pid = %r（stale pid=%s）"
          % (formatted.replace("\n", "\\n"), stale_pid))

    w = subprocess.Popen([PYW, copy, "--watch"], env=env, cwd=tmp,
                         creationflags=0x00000008 | 0x08000000)
    try:
        # 监听器必须能把 watcher.pid 换成它自己的（新 pid），说明它真的跑起来了。
        # 注意：可能被删掉后重写，所以只要"不再是那个脏 pid"就算它动了。
        t = wait_for(lambda: (read_first_pid(pid_s) or 0) not in (0, stale_pid), 25)
        started = t is not None and alive(read_first_pid(pid_s) or 0)
        if expect_watcher_starts:
            check("监听器成功启动（没被脏 pid 文件挡住）", started,
                  "watcher.pid 没被更新/进程不活")
        else:
            check("监听器按预期拒绝启动", not started)
        print("  启动耗时 %s，watcher.pid 现在 = %s"
              % ("%.1fs" % t if t else "超时", read_first_pid(pid_s)))

        if started:
            t2 = wait_for(lambda: alive(read_first_pid(pid_w) or 0), 45)
            check("挂件被拉起（窗口会真的出现）", t2 is not None,
                  "45 秒内没看到挂件进程")

        # 收尾
        subprocess.run([PYW.replace("pythonw", "python"), copy, "--stop-all"],
                       env=env, cwd=tmp, capture_output=True, timeout=60)
    finally:
        try:
            w.kill()
        except Exception:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


# 造一个"别的程序"，用来占用那个被复用的 pid
ping = subprocess.Popen([r"C:\Windows\System32\ping.exe", "-n", "120", "127.0.0.1"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(0.6)
print("用作「被复用的 pid」的无关进程：pid=%d（ping.exe）" % ping.pid)
print("它现在活着？ %s" % alive(ping.pid))

try:
    # ① 真实事故：单行 pid，指向一个活着的**非 python** 进程
    scenario("① 脏文件单行 pid，指向被复用的无关进程（=本次事故原样）",
             "%(pid)d", True, decoy=ping)

    # ② 两行 pid，进程存在但创建时间不符（同名不同进程）
    scenario("② 两行 pid，创建时间对不上",
             "%(pid)d\n123456789", True, decoy=ping)

    # ③ 单行 pid，进程已经不存在了（最普通的陈旧文件）
    scenario("③ pid 早已不存在", "999999", True, decoy=None)
finally:
    try:
        ping.kill()
    except Exception:
        pass

print("\n" + "=" * 50)
bad = [r for r in results if not r[1]]
print("通过 %d / %d" % (len(results) - len(bad), len(results)))
if bad:
    print("失败：" + ", ".join(r[0] for r in bad))
sys.exit(1 if bad else 0)
