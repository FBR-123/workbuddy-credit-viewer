# -*- coding: utf-8 -*-
"""冒烟测试：右键菜单能否正常构建（新增的「随 WorkBuddy 自动启动」子菜单）。

菜单是在右键时才构建的，NameError 只会在那一刻炸出来——所以要主动点一次。
用隔离 APP_DIR，不碰真实配置。
"""
import importlib.util
import os
import re
import shutil
import sys
import tempfile
import tkinter as tk
import types

SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "wb_credit_widget.py")

tmp = tempfile.mkdtemp(prefix="wb_menu_")
app = os.path.join(tmp, "appdata")
os.makedirs(app, exist_ok=True)
copy = os.path.join(tmp, "wb_credit_widget.py")

src = open(SRC, encoding="utf-8").read()
src, n = re.subn(r'^APP_DIR = .*$', lambda mo: 'APP_DIR = r"%s"' % app,
                 src, count=1, flags=re.M)
assert n == 1
open(copy, "w", encoding="utf-8").write(src)

spec = importlib.util.spec_from_file_location("wbmenu", copy)
m = importlib.util.module_from_spec(spec)
sys.modules["wbmenu"] = m
spec.loader.exec_module(m)

fails = []


def check(name, cond, detail=""):
    print("%s %s%s" % ("PASS" if cond else "FAIL", name,
                       ("  <- " + detail) if detail and not cond else ""))
    if not cond:
        fails.append(name)


print("=== 构建 Widget 并点一次右键 ===")
try:
    w = m.Widget()
except Exception as e:
    print("FAIL 构造 Widget 失败：%r" % e)
    raise SystemExit(1)

# tk_popup 会真的弹菜单并阻塞，替换掉它，只验证菜单能构建出来
popped = {}
import tkinter as _tk
_orig_popup = _tk.Menu.tk_popup


def fake_popup(self, x, y, entry=""):
    popped["n"] = popped.get("n", 0) + 1
    # 递归收集所有菜单项，确认新入口真的挂上去了
    popped.setdefault("labels", [])

    def walk(menu):
        for i in range(menu.index("end") + 1 if menu.index("end") is not None else 0):
            try:
                if menu.type(i) == "cascade":
                    popped["labels"].append(menu.entrycget(i, "label"))
                    walk(menu.nametowidget(menu.entrycget(i, "menu")))
                else:
                    popped["labels"].append(menu.entrycget(i, "label"))
            except Exception:
                pass

    walk(self)


_tk.Menu.tk_popup = fake_popup
try:
    ev = types.SimpleNamespace(x_root=100, y_root=100)
    try:
        w._menu(ev)
        check("右键菜单构建成功（无异常）", True)
    except Exception as e:
        check("右键菜单构建成功（无异常）", False, repr(e))
finally:
    _tk.Menu.tk_popup = _orig_popup

labels = popped.get("labels", [])
print("菜单项共 %d 条" % len(labels))
check("有「随 WorkBuddy 自动启动」子菜单",
      any("随 WorkBuddy 自动启动" in str(l) for l in labels),
      str(labels))
check("有「启用/移除」动作项",
      any(("启用「随 WorkBuddy 自动启动」" in str(l)
           or "移除「随 WorkBuddy 自动启动」" in str(l)) for l in labels))
check("有「查看监听状态」", any("查看监听状态" in str(l) for l in labels))
check("原有关键项还在（立即刷新/退出挂件）",
      any("立即刷新" in str(l) for l in labels) and any("退出挂件" in str(l) for l in labels))

print("\n=== 直接调用菜单动作方法（不应抛异常）===")
for fn, name in ((w._watcher_status, "_watcher_status"),):
    try:
        # 把系统对话框换掉，别真弹出来阻塞
        orig_show = m.show_msg
        m.show_msg = lambda *a, **k: None
        orig_info = m.Widget._info
        m.Widget._info = lambda self, text: None
        fn()
        check("%s 调用成功" % name, True)
    except Exception as e:
        check("%s 调用成功" % name, False, repr(e))
    finally:
        m.Widget._info = orig_info
        m.show_msg = orig_show

try:
    w.quit()
except Exception as e:
    check("quit 正常", False, repr(e))

print("\n" + "=" * 46)
print("失败 %d 项" % len(fails))
shutil.rmtree(tmp, ignore_errors=True)
sys.exit(1 if fails else 0)
