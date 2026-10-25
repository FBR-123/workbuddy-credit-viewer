# -*- coding: utf-8 -*-
"""挂件/监听器停机现场诊断。

要回答的问题：
  1. watcher.pid 里的 pid 到底是活的吗？它的映像名是什么？（怀疑 PID 复用导致误报"运行中"）
  2. watcher.log / widget.log 最后的记录是什么？
  3. 机器上次启动/登录是什么时候？（判断"登录自启有没有机会跑过"）
  4. 记账文件（state.json）最后被写的时间 = 挂件最后活着的时刻
"""
import ctypes
import ctypes.wintypes as w
import datetime
import json
import os
import subprocess
import sys

APP = os.path.join(os.environ["LOCALAPPDATA"], "WBCreditWidget")
k = ctypes.windll.kernel32

k.OpenProcess.restype = w.HANDLE
k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
k.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
k.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, ctypes.c_wchar_p,
                                         ctypes.POINTER(w.DWORD)]
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def proc_info(pid):
    """返回 (是否存活, 映像路径)。"""
    h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not h:
        return False, None
    try:
        code = w.DWORD()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        alive = bool(ok) and code.value == 259
        buf = ctypes.create_unicode_buffer(1024)
        n = w.DWORD(1024)
        img = None
        if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
            img = buf.value
        return alive, img
    finally:
        k.CloseHandle(h)


def read_pid(name):
    p = os.path.join(APP, name)
    try:
        # pid 文件现在是两行：第一行 pid，第二行创建时间（防 PID 复用）
        return int(open(p, encoding="utf-8").read().split()[0]), p
    except Exception as e:
        return None, "%s（读不到：%s）" % (p, e)


print("=" * 62)
print("1. pid 文件 vs 真实进程")
for name in ("watcher.pid", "widget.pid"):
    pid, path = read_pid(name)
    if pid is None:
        print("   %-12s 不存在或读不到" % name)
        continue
    alive, img = proc_info(pid)
    print("   %-12s 内容 pid=%d" % (name, pid))
    print("       该 pid 存活 = %s" % alive)
    print("       映像       = %s" % (img or "（无此进程）"))
    if alive and img:
        base = os.path.basename(img).lower()
        if base not in ("pythonw.exe", "python.exe"):
            print("       *** PID 复用！这个 pid 现在是别的程序，不是我们的进程 ***")
        else:
            print("       判定：确实是我们的进程")

print()
print("2. tasklist 里所有 python* 进程")
try:
    out = subprocess.run(["tasklist", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, timeout=60).stdout
    rows = [r for r in out.splitlines() if "python" in r.lower()]
    if not rows:
        print("   （一个都没有）")
    for r in rows:
        print("   " + r.strip())
except Exception as e:
    print("   tasklist 失败：%r" % e)

print()
print("3. 日志")
for f in ("watcher.log", "widget.log"):
    p = os.path.join(APP, f)
    if not os.path.exists(p):
        print("   %s：不存在" % f)
        continue
    st = os.stat(p)
    print("   %s（%d 字节，最后修改 %s）"
          % (f, st.st_size, datetime.datetime.fromtimestamp(st.st_mtime)))
    try:
        lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
        for ln in lines[-12:]:
            print("      " + ln)
    except Exception as e:
        print("      读取失败：%r" % e)

print()
print("4. 文件时间线")
for f in ("config.json", "state.json"):
    p = os.path.join(APP, f)
    if os.path.exists(p):
        st = os.stat(p)
        print("   %-12s 最后修改 %s" % (f, datetime.datetime.fromtimestamp(st.st_mtime)))

print()
print("5. 系统运行时长（判断有没有重启过）")
try:
    ms = k.GetTickCount64()
    up = datetime.timedelta(milliseconds=ms)
    boot = datetime.datetime.now() - up
    print("   已运行 %s" % up)
    print("   开机时刻 ≈ %s" % boot.strftime("%Y-%m-%d %H:%M:%S"))
except Exception as e:
    print("   取不到：%r" % e)

print()
print("6. 当前 WorkBuddy 进程数")
try:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq WorkBuddy.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, timeout=60).stdout
    n = len([r for r in out.splitlines() if "WorkBuddy.exe" in r])
    print("   %d 个" % n)
except Exception as e:
    print("   失败：%r" % e)
print("=" * 62)
