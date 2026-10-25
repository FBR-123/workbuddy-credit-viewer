# -*- coding: utf-8 -*-
"""
WorkBuddy 积分挂件 (Credit Widget)
---------------------------------
常驻桌面底部的悬浮小挂件：实时显示积分余额、今日消耗、近 3/5 天即将过期的积分与倒计时。
只用 Python 标准库（tkinter + urllib）。

数据来源：本机 WorkBuddy 登录态快照（只读），绝不输出或落盘任何 token。

运行（三种方式，按需选）：
    pythonw wb_credit_widget.py              # 直接显示挂件（手动启动）
    pythonw wb_credit_widget.py --watch      # 监听模式：WorkBuddy 一启动就拉起挂件，
                                             #            WorkBuddy 一退出就结束挂件（无界面）
    python  wb_credit_widget.py --stop       # 只停挂件
    python  wb_credit_widget.py --stop-all   # 停挂件 + 停监听器
"""
import argparse
import base64
import datetime
import glob
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

# tkinter 只在真的要开窗口时才需要。命令行子命令（--stop / --dump / --autostart status）
# 不该因为"这台机器的 python 没装 tkinter"就整个崩掉 —— 那恰恰是最需要救场的时候。
try:
    import tkinter as tk
    from tkinter import font as tkfont
except Exception:                       # 精简版 Python 常见：没有 tkinter
    tk = None
    tkfont = None

# ---------------------------------------------------------------- 路径 / 常量
__version__ = "1.0.0"


def _find_checkin_skill_dir():
    """找签到技能里的 workbuddy_checkin.py（只用来解 token 的加密信封）。

    不写死任何人的家目录：先看环境变量，再在 ~/.workbuddy/skills/*/scripts 里找。

    **只认名字里带 checkin 的技能目录**：这个模块是 import 进来执行的，
    要是把"任意技能目录里有个同名文件"都当候选，等于给本机所有第三方技能
    一个在挂件启动时执行代码的机会。装到别处就设 WB_CHECKIN_SKILL_DIR。

    找不到返回 None —— 挂件照常工作，只是解不了新版本的加密信封。
    """
    env = os.environ.get("WB_CHECKIN_SKILL_DIR")
    if env and os.path.exists(os.path.join(env, "workbuddy_checkin.py")):
        return env
    base = os.path.join(os.path.expanduser("~"), ".workbuddy", "skills")
    try:
        for name in sorted(os.listdir(base)):
            if "checkin" not in name.lower():
                continue
            cand = os.path.join(base, name, "scripts")
            if os.path.exists(os.path.join(cand, "workbuddy_checkin.py")):
                return cand
    except Exception:
        pass
    return None


SKILL_DIR = _find_checkin_skill_dir()
AUTH_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""),
                        "CodeBuddyExtension", "Data", "Public", "auth")
APP_DIR = os.path.join(os.environ.get("LOCALAPPDATA", "."), "WBCreditWidget")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
STATE_PATH = os.path.join(APP_DIR, "state.json")
PID_PATH = os.path.join(APP_DIR, "widget.pid")
WATCHER_PID_PATH = os.path.join(APP_DIR, "watcher.pid")
WATCHER_HEARTBEAT = os.path.join(APP_DIR, "watcher.heartbeat")
WATCHER_LOG = os.path.join(APP_DIR, "watcher.log")
WIDGET_LOG = os.path.join(APP_DIR, "widget.log")
HEARTBEAT_STALE = 180.0          # 心跳超过这么久没刷新 = 监听器卡死/已死

# 由 --watch 拉起时置 True：WorkBuddy 退出后挂件自己结束进程，把桌面还原干净
MANAGED = False
# 开机自启项（启动的是「监听器」，不是挂件窗口）
STARTUP_DIR = os.path.join(os.environ.get("APPDATA", ""),
                           "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
STARTUP_VBS = os.path.join(STARTUP_DIR, "WorkBuddyCreditWidget.vbs")

TRANSPARENT = "#FF00FF"          # 圆角透明色的 key color

C_BG = "#FFFFFF"
C_BORDER = "#DCE3EA"
C_TEXT = "#0F2B3C"
C_SUB = "#7A8A99"
C_ACCENT = "#0B7285"
C_WARN = "#C77700"
C_DANGER = "#D93025"
C_OK = "#1E8E5A"

DEFAULT_CONFIG = {
    "poll_seconds": 15,           # 高频余额轮询（全部账号、每个只 1 个请求）；0 = 关闭
    "refresh_seconds": 60,        # 完整刷新当前账号（含批次/到期）
    "full_seconds": 600,          # 全账号盘点间隔
    "rotate_seconds": 10,         # 胶囊轮播显示各账号的间隔；0 = 只显示当前在线账号
    "view": "current",            # current | all
    "topmost": True,
    "rounded": True,
    "follow_workbuddy": True,     # 只在 WorkBuddy 客户端运行时显示
    "exit_with_workbuddy": True,  # 被监听器拉起时，WorkBuddy 退出后挂件进程自行结束
    "exit_grace_seconds": 8,      # WorkBuddy 退出后等这么久再结束（避免切换账号时的瞬时抖动）
    "startup_toast": True,        # 显示时弹一次多账号盘点面板
    "toast_seconds": 20,
    "alert_days": 3,              # 到期提醒阈值（天）；0 = 关闭提醒
    "alert_seconds": 45,          # 到期提醒面板自动隐藏秒数
    "runout_days": 7,             # 耗尽预测提醒阈值（天）；0 = 关闭提醒
    "runout_window_days": 7,      # 计算日均消耗的统计窗口（天）
    "runout_min_balance": 100,    # 单账号余额低于此值时列入低余额提醒（0 = 关闭）
    "x": None,
    "y": None,
    "right": None,
}

_wc = None
if SKILL_DIR:
    try:
        sys.path.insert(0, SKILL_DIR)
        import workbuddy_checkin as _wc  # noqa: E402
    except Exception:
        _wc = None


# ---------------------------------------------------------------- 小工具
def mask(s):
    s = str(s or "")
    if len(s) == 11 and s.isdigit():
        return s[:3] + "****" + s[-4:]
    if len(s) > 12:
        return s[:8] + "..."
    return s


def num(v):
    try:
        return float(v)
    except Exception:
        return 0.0


def fmt(v):
    v = num(v)
    if abs(v - round(v)) < 0.005:
        return "{:,}".format(int(round(v)))
    return "{:,.1f}".format(v)


def fmt_used(v):
    """消耗值：有消耗才带负号，0 就老实显示 0（避免出现「-0」）。"""
    v = num(v)
    if v <= 0:
        return "0"
    return "-" + fmt(v)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            base = dict(default)
            base.update(d)
            return base
    except Exception:
        pass
    return dict(default)


def save_json(path, data):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


def jwt_payload(token):
    try:
        p = token.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p))
    except Exception:
        return {}


def resolve_token(raw):
    if isinstance(raw, str) and raw:
        return raw
    if isinstance(raw, dict) and _wc is not None:
        try:
            return _wc.decrypt_access_token_field(raw)
        except Exception:
            return None
    return None


def post(url, token, body, timeout=20):
    import urllib.error
    import urllib.request
    data = json.dumps(body if body is not None else {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", "Bearer %s" % token)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "WorkBuddy-Credit-Widget/1.0")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8", "replace"))
        except Exception:
            return e.code, {}
    except Exception as e:
        return -1, {"err": type(e).__name__}


# ---------------------------------------------------------------- 账号扫描
def scan_accounts():
    cands = []
    for f in glob.glob(os.path.join(AUTH_DIR, "*.info")):
        try:
            cands.append((f, os.path.getmtime(f)))
        except OSError:
            cands.append((f, 0))
    cands.sort(key=lambda x: x[1])
    out = {}
    for f, mt in cands:
        base = os.path.basename(f)
        is_cur = base == "workbuddy-desktop.info"
        try:
            with open(f, "r", encoding="utf-8-sig") as fh:
                data = json.load(fh)
        except Exception:
            continue
        acct = data.get("account") or {}
        auth = data.get("auth") or {}
        raw = auth.get("accessToken")
        pay = jwt_payload(raw if isinstance(raw, str) else "")
        uid = acct.get("uid") or pay.get("sub") or base
        exp = pay.get("exp")
        fresh = {
            "uid": uid,
            "label": mask(acct.get("nickname") or acct.get("phoneNumber")
                          or pay.get("preferred_username")),
            "phone": mask(acct.get("phoneNumber")),
            "domain": auth.get("domain") or "www.workbuddy.cn",
            "token_exp_days": (datetime.datetime.fromtimestamp(exp)
                               - datetime.datetime.now()).days if exp else None,
            "mtime": mt,
            "_token": resolve_token(raw),
        }
        if uid in out:
            out[uid]["snapshots"] += 1
            out[uid]["is_current"] = out[uid]["is_current"] or is_cur
            if fresh["_token"]:
                out[uid].update(fresh)
            else:
                for k in ("label", "phone", "domain", "token_exp_days"):
                    if not out[uid].get(k) and fresh.get(k):
                        out[uid][k] = fresh[k]
        else:
            fresh["snapshots"] = 1
            fresh["is_current"] = is_cur
            out[uid] = fresh
    return sorted(out.values(), key=lambda x: (not x["is_current"], x["label"] or ""))


# ---------------------------------------------------------------- 取数
def fetch_balance_only(acc):
    """高频轻量档：只拉资源包汇总（每账号 1 个请求），用于刷新余额与今日消耗。

    批次 / 到期信息沿用上一次完整刷新的结果（这些不会每分钟变）。
    """
    token = acc.get("_token")
    if not token:
        return None
    base = "https://%s" % acc["domain"]
    try:
        _st, summ = post(base + "/billing/meter/get-user-resource-summary", token, {})
    except Exception:
        return None
    pk = ((summ or {}).get("data") or {}).get("Packages") or []
    if not pk:
        return None
    return {
        "total_remain": round(sum(num(p.get("CycleRemainCapacity")) for p in pk), 2),
        "packages": [{"name": p.get("PackageCode"),
                      "total": p.get("CycleTotalCapacity"),
                      "remain": p.get("CycleRemainCapacity"),
                      "used": p.get("CycleUsedCapacity")} for p in pk],
        "balanced_at": time.time(),
    }


def fetch_account(acc, today):
    res = {
        "uid": acc["uid"], "label": acc["label"], "phone": acc["phone"],
        "domain": acc["domain"], "token_exp_days": acc["token_exp_days"],
        "is_current": acc["is_current"],
        "ok": False, "total_remain": 0.0, "packages": [],
        "soon3": 0.0, "soon5": 0.0, "batches": [], "soon": [],
        "active_batch_count": 0, "next_end": None, "next_remain": None,
        "error": None,
    }
    token = acc.get("_token")
    if not token:
        res["error"] = "无法取得可用 token（登录态可能已失效）"
        return res
    base = "https://%s" % acc["domain"]
    st, summ = post(base + "/billing/meter/get-user-resource-summary", token, {})
    pk = ((summ.get("data") or {}).get("Packages") or [])
    if not pk:
        res["error"] = "无资源包数据（HTTP %s / code %s）" % (st, (summ or {}).get("code"))
        res["ok"] = (st == 200)
        return res
    codes = [p.get("PackageCode") for p in pk]
    accs = []
    for path, body in [
        ("/billing/meter/get-user-resource-free-packages",
         {"PackageCodes": codes, "PageNumber": 1, "PageSize": 200}),
        ("/billing/meter/get-user-resource-paid-packages",
         {"PackageCodes": codes, "PageNumber": 1, "PageSize": 200, "NeedRenewInfo": True}),
    ]:
        _s, r = post(base + path, token, body)
        accs += ((r.get("data") or {}).get("Accounts") or [])
    batches = []
    for a in accs:
        rem = num(a.get("CycleCapacityRemainPrecise"))
        if rem <= 0:
            continue
        end = a.get("CycleEndTime")
        try:
            d = datetime.datetime.strptime(str(end).strip(), "%Y-%m-%d %H:%M:%S")
            days = (d.date() - today).days
        except Exception:
            d, days = None, None
        batches.append({
            "remain": round(rem, 2), "package": a.get("PackageName"),
            "end": end, "days_left": days,
            "ts": d.timestamp() if d else None,
        })
    batches.sort(key=lambda x: (x["ts"] if x["ts"] else 9e18))
    soon3 = sum(b["remain"] for b in batches
                if b["days_left"] is not None and 0 <= b["days_left"] <= 3)
    soon5 = sum(b["remain"] for b in batches
                if b["days_left"] is not None and 0 <= b["days_left"] <= 5)
    b3 = [b for b in batches
          if b["days_left"] is not None and 0 <= b["days_left"] <= 3]
    b5 = [b for b in batches
          if b["days_left"] is not None and 0 <= b["days_left"] <= 5]
    res.update({
        "ok": True,
        "total_remain": round(sum(num(p.get("CycleRemainCapacity")) for p in pk), 2),
        "packages": [{"name": p.get("PackageCode"),
                      "total": p.get("CycleTotalCapacity"),
                      "remain": p.get("CycleRemainCapacity"),
                      "used": p.get("CycleUsedCapacity")} for p in pk],
        "batches": batches,
        "active_batch_count": len(batches),
        "soon": [b for b in batches
                 if b["days_left"] is not None and 0 <= b["days_left"] <= 5],
        "soon3": round(soon3, 2),
        "soon5": round(soon5, 2),
        "next3_ts": b3[0]["ts"] if b3 else None,     # 倒计时要与 soon3 同源，否则会出现
        "next3_end": b3[0]["end"] if b3 else None,   # 「3日到期 0」后面还跟着一个倒计时
        "next5_ts": b5[0]["ts"] if b5 else None,
        "next_end": batches[0]["end"] if batches else None,
        "next_remain": batches[0]["remain"] if batches else None,
        "next_ts": batches[0]["ts"] if batches else None,
    })
    return res


# ---------------------------------------------------------------- 状态:今日消耗 / 提醒去重
def load_state():
    """读状态文件；跨天时重置今日基线，但保留提醒去重记录与余额采样。"""
    st = load_json(STATE_PATH, {"date": "", "baseline": {}, "alerts": {}, "burn": {}})
    today = datetime.date.today().isoformat()
    if st.get("date") != today:
        st["date"] = today
        st["baseline"] = {}
    st.setdefault("baseline", {})
    st.setdefault("alerts", {})
    st.setdefault("burn", {})
    return st


def daily_baseline(totals):
    """按账号记录当天首次看到的余额，用于算今日消耗。"""
    st = load_state()
    base = st["baseline"]
    for uid, t in totals.items():
        if uid not in base:
            base[uid] = t
    save_json(STATE_PATH, st)
    return base


def alert_already_sent(key):
    return key in load_state().get("alerts", {})


def mark_alert_sent(key):
    st = load_state()
    alerts = st.setdefault("alerts", {})
    alerts[key] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if len(alerts) > 300:                      # 只保留最近 300 条
        for k in list(alerts)[:-300]:
            alerts.pop(k, None)
    save_json(STATE_PATH, st)


# ---------------------------------------------------------------- 余额采样 / 耗尽预测
def record_balance(total, ok_count):
    """把「今天最后一次读到的总余额」写进当日采样；同日覆盖，保留最近 45 天。

    每天同时记录当天的首采（first_ts / first_total），用于估算当天消耗速度。
    返回最新的 burn 字典。
    """
    st = load_state()
    burn = st.setdefault("burn", {})
    if not ok_count:
        return burn
    now = time.time()
    today = datetime.date.today().isoformat()
    total = round(float(total or 0), 2)
    cur = burn.get(today) if isinstance(burn.get(today), dict) else None
    if cur is None:
        burn[today] = {"first_ts": now, "first_total": total, "total": total, "ts": now}
    else:
        cur["total"] = total
        cur["ts"] = now
        cur.setdefault("first_ts", now)
        cur.setdefault("first_total", total)
    if len(burn) > 45:
        for k in sorted(burn)[:-45]:
            burn.pop(k, None)
    save_json(STATE_PATH, st)
    return burn


def burn_stats(burn, current_total, window_days=7, now=None):
    """按采样估算日均消耗与「还能用多久」。

    返回 ok=True 时含 rate（分/天）、days_left（天）、runout_ts、samples、span_days；
    ok=False 时含 reason 说明为什么算不出来。
    """
    now = float(now or time.time())
    remain = float(current_total or 0)
    info = {"ok": False, "samples": 0, "remain": round(remain, 2)}
    pts = []
    for _d, v in (burn or {}).items():
        if not isinstance(v, dict):
            continue
        try:
            if v.get("ts"):
                pts.append((float(v["ts"]), float(v.get("total") or 0)))
        except (TypeError, ValueError):
            pass
        try:
            if v.get("first_ts"):
                pts.append((float(v["first_ts"]), float(v.get("first_total") or 0)))
        except (TypeError, ValueError):
            pass
    if current_total is not None:
        pts.append((now, remain))
    pts = [p for p in pts if p[0] > 0]
    pts.sort()
    cut = now - float(window_days or 7) * 86400
    pts = [p for p in pts if p[0] >= cut]
    info["samples"] = len(pts)
    if len(pts) < 2:
        info["reason"] = "采样不足（挂件要跑一段时间才能估算）"
        return info
    t0, v0 = pts[0]
    t1, v1 = pts[-1]
    span = (t1 - t0) / 86400.0
    if span < 0.05:
        info["reason"] = "采样跨度太短，继续观察中"
        return info
    spent = round(v0 - v1, 2)
    info["span_days"] = round(span, 2)
    info["spent"] = spent
    if spent <= 0:
        info["ok"] = True
        info["rate"] = 0.0
        info["days_left"] = None
        info["reason"] = "近 %s 天余额没有下降" % info["span_days"]
        return info
    rate = spent / span
    info["ok"] = True
    info["rate"] = round(rate, 2)
    info["today_only"] = span < 1.0
    if rate < 0.5:                              # 每天不到半分，视为几乎不消耗
        info["days_left"] = None
        info["reason"] = "消耗极低，暂时估不出耗尽时间"
        return info
    days_left = remain / rate
    info["days_left"] = days_left
    info["runout_ts"] = now + days_left * 86400
    return info


def span_text(seconds):
    """把秒数说成「3天4时 / 18时 / 42分」。"""
    if seconds is None:
        return "-"
    if seconds <= 0:
        return "已耗尽"
    d = int(seconds // 86400)
    h = int((seconds % 86400) // 3600)
    m = int((seconds % 3600) // 60)
    if d > 0:
        return "%d天%d时" % (d, h)
    if h > 0:
        return "%d时%d分" % (h, m)
    return "%d分" % m


# ---------------------------------------------------------------- WorkBuddy 检测：看窗口，不看进程
def _wb_pids():
    """枚举所有 WorkBuddy.exe 的 pid；枚举不了（异常/非 Windows）返回 None。"""
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        TH32CS_SNAPPROCESS = 0x2
        INVALID = ctypes.c_size_t(-1).value

        class PE32W(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t),
                        ("th32ModuleID", wintypes.DWORD),
                        ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", wintypes.LONG),
                        ("dwFlags", wintypes.DWORD),
                        ("szExeFile", wintypes.WCHAR * 260)]

        k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k.Process32FirstW.restype = wintypes.BOOL
        k.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PE32W)]
        k.Process32NextW.restype = wintypes.BOOL
        k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PE32W)]
        names = {"workbuddy.exe"}
        extra = os.environ.get("WORKBUDDY_PROCESS_NAMES", "")
        if extra:
            # **替换**而不是追加：自动化测试要能把真实客户端排除在外，
            # 只认自己造出来的那个假客户端（追加的话真客户端永远匹配得上，
            # 根本没法在真客户端正在运行的机器上测"窗口关掉"这条路）。
            names = {n.strip().lower() for n in extra.split(",") if n.strip()}
        snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == INVALID:
            return None
        pids = []
        try:
            e = PE32W()
            e.dwSize = ctypes.sizeof(PE32W)
            ok = k.Process32FirstW(snap, ctypes.byref(e))
            while ok:
                if e.szExeFile and e.szExeFile.lower() in names:
                    pids.append(int(e.th32ProcessID))
                ok = k.Process32NextW(snap, ctypes.byref(e))
        finally:
            k.CloseHandle(snap)
        return pids
    except Exception:
        return None


def _wb_visible_window(pids):
    """返回第一个属于这些 pid 的**可见**顶层窗口句柄，没有就返回 None。

    判定要点：
      * 用 `IsWindowVisible` —— **最小化到任务栏的窗口仍然算可见**
        （Electron 最小化不会隐藏窗口），所以"最小化"不会被误判成"已关闭"；
      * 隐藏到托盘时窗口不可见 → 判为"客户端窗口已关"，这正是用户要的语义；
      * 顺带排除 IME / MSCTFIME 之类的隐形辅助窗口（它们本来就不可见）。
    """
    if not pids or not sys.platform.startswith("win"):
        return None
    import ctypes
    from ctypes import wintypes
    u = ctypes.WinDLL("user32", use_last_error=True)
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                           ctypes.POINTER(wintypes.DWORD)]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.IsWindowVisible.restype = wintypes.BOOL

    want = set(int(p) for p in pids)
    found = []

    # 回调类型单独命名，并且把 EnumWindows 的 argtypes 也声明上：
    # 不声明的话 64 位下函数指针可能被按 c_int 截断（本项目自己踩过这个坑）。
    ENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @ENUMPROC
    def _cb(hwnd, _):
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in want and u.IsWindowVisible(hwnd):
            found.append(hwnd)
            return False                      # 找到一个就够了，提前收工
        return True

    u.EnumWindows.argtypes = [ENUMPROC, wintypes.LPARAM]
    u.EnumWindows.restype = wintypes.BOOL
    u.EnumWindows(_cb, 0)
    return found[0] if found else None


def workbuddy_running():
    """WorkBuddy 客户端**窗口**是否开着。True/False；无法检测时返回 None。

    判据是"有没有可见的顶层窗口"，**不是**"进程在不在"——
    原因见 _wb_visible_window 的注释（关掉窗口后后台辅助进程还会赖着不走）。

    环境变量 WB_WIDGET_FAKE_WB 是**只给自动化测试用**的后门，不设它时行为完全不变：
      * "1" / "0"   → 直接返回该结果
      * 一个文件路径 → 每次读文件里的 "1"/"0"（可以在运行中途翻牌，用于测试启停切换）
    """
    fake = os.environ.get("WB_WIDGET_FAKE_WB")
    if fake == "1":
        return True
    if fake == "0":
        return False
    if fake:
        try:
            with open(fake, "r", encoding="utf-8") as f:
                v = f.read().strip()
            if v in ("0", "1"):
                return v == "1"
        except Exception:
            pass              # 文件读不到就退回真实检测，绝不因此误判

    # Windows：先按「有没有可见的客户端窗口」判断——这是唯一能区分
    # 「客户端开着」和「只剩后台残留进程」的信号。
    pids = _wb_pids()
    if pids is not None:
        try:
            return _wb_visible_window(pids) is not None
        except Exception:
            pass              # 窗口枚举失败，退回下面的进程判断
    if _wc is not None:
        try:
            return bool(_wc._list_workbuddy_pids())
        except Exception:
            pass
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        TH32CS_SNAPPROCESS = 0x2
        INVALID = ctypes.c_size_t(-1).value

        class PE32W(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD),
                        ("th32DefaultHeapID", ctypes.c_size_t),
                        ("th32ModuleID", wintypes.DWORD),
                        ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD),
                        ("pcPriClassBase", wintypes.LONG),
                        ("dwFlags", wintypes.DWORD),
                        ("szExeFile", wintypes.WCHAR * 260)]

        k.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        k.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        k.Process32FirstW.restype = wintypes.BOOL
        k.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PE32W)]
        k.Process32NextW.restype = wintypes.BOOL
        k.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PE32W)]
        snap = k.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == INVALID:
            return None
        names = {"workbuddy.exe"}
        extra = os.environ.get("WORKBUDDY_PROCESS_NAMES", "")
        if extra:
            names |= {n.strip().lower() for n in extra.split(",") if n.strip()}
        try:
            e = PE32W()
            e.dwSize = ctypes.sizeof(PE32W)
            ok = k.Process32FirstW(snap, ctypes.byref(e))
            while ok:
                if e.szExeFile and e.szExeFile.lower() in names:
                    return True
                ok = k.Process32NextW(snap, ctypes.byref(e))
        finally:
            k.CloseHandle(snap)
        return False
    except Exception:
        return None


def collect(session_start_totals, fast_only_uids=None, prev=None, balance_all=False):
    """取数并汇总。

    fast_only_uids: 只完整刷新这些账号，其余复用 prev 结果。
    balance_all:    高频轻量档 —— 全部账号只刷余额（每号 1 个请求），批次沿用 prev。
    """
    from datetime import datetime as _dt
    today = datetime.date.today()
    accounts = scan_accounts()
    prev_map = {a["uid"]: a for a in (prev or {}).get("accounts", [])}
    results = []
    for acc in accounts:
        old = prev_map.get(acc["uid"])
        if balance_all and old and old.get("ok"):
            bal = fetch_balance_only(acc)
            if bal:
                merged = dict(old)
                merged.update(bal)
                results.append(merged)
            else:
                results.append(old)             # 拿不到就先用旧值，等下一轮完整刷新
            continue
        if fast_only_uids is not None and acc["uid"] not in fast_only_uids:
            if old and old.get("ok"):
                results.append(old)
                continue
        results.append(fetch_account(acc, today))

    daily_total = round(sum(r.get("total_remain") or 0 for r in results), 2)
    totals = {r["uid"]: (r.get("total_remain") or 0) for r in results if r.get("ok")}
    base = daily_baseline(totals)
    for r in results:                       # 每个账号的今日消耗（用于多账号轮播显示）
        if r.get("ok"):
            t = float(r.get("total_remain") or 0)
            r["used_today"] = round(max(0.0, base.get(r["uid"], t) - t), 2)
        else:
            r["used_today"] = None
    used_all = round(sum(max(0.0, base.get(u, t) - t) for u, t in totals.items()), 2)
    sess_all = round(sum(max(0.0, session_start_totals.get(u, t) - t)
                         for u, t in totals.items()), 2)

    soon_all = []
    for r in results:
        for b in r.get("soon", []):
            soon_all.append({"account": r["label"], "phone": r["phone"],
                             "remain": b["remain"], "end": b["end"],
                             "days_left": b["days_left"], "package": b["package"],
                             "ts": b["ts"]})
    soon_all.sort(key=lambda x: x["ts"] if x.get("ts") else 9e18)

    # 最近到期（<=5天）用于倒计时
    nxt = None
    nxt3 = None
    for r in results:
        for b in r.get("soon", []):
            if b.get("ts") and (nxt is None or b["ts"] < nxt["ts"]):
                nxt = {"end": b["end"], "remain": b["remain"],
                       "account": r["label"], "ts": b["ts"],
                       "package": b["package"]}
        t3 = r.get("next3_ts")
        if t3 and (nxt3 is None or t3 < nxt3["ts"]):
            nxt3 = {"end": r.get("next3_end"), "account": r["label"], "ts": t3}

    cur = next((r for r in results if r.get("is_current")), results[0] if results else None)
    mask_uids = set(fast_only_uids or [])
    if fast_only_uids is not None:
        totals_cur = {u: t for u, t in totals.items() if u in mask_uids}
        used_cur = round(sum(max(0.0, base.get(u, t) - t) for u, t in totals_cur.items()), 2)
        sess_cur = round(sum(max(0.0, session_start_totals.get(u, t) - t)
                             for u, t in totals_cur.items()), 2)
    else:
        used_cur, sess_cur = used_all, sess_all

    return {
        "ts": _dt.now().strftime("%Y-%m-%d %H:%M:%S"),
        "account_count": len(results),
        "ok_count": sum(1 for r in results if r.get("ok")),
        "total_remain_all": daily_total,
        "used_today_all": used_all,
        "used_session_all": sess_all,
        "used_today_cur": used_cur,
        "used_session_cur": sess_cur,
        "soon3_all": round(sum(r.get("soon3") or 0 for r in results), 2),
        "soon5_all": round(sum(r.get("soon5") or 0 for r in results), 2),
        "current": cur,
        "next_expiry": nxt,
        "next3_ts": nxt3["ts"] if nxt3 else None,
        "next3_end": nxt3["end"] if nxt3 else None,
        "next3_account": nxt3["account"] if nxt3 else None,
        "soon_all": soon_all,
        "accounts": results,
    }


# ---------------------------------------------------------------- 倒计时
def countdown_text(ts):
    if not ts:
        return ""
    left = ts - time.time()
    if left <= 0:
        return "已到期"
    d = int(left // 86400)
    h = int((left % 86400) // 3600)
    m = int((left % 3600) // 60)
    if d > 0:
        return "%d天%d时" % (d, h)
    if h > 0:
        return "%d时%d分" % (h, m)
    return "%d分" % m


# ---------------------------------------------------------------- UI
class Widget:
    def __init__(self):
        self.cfg = load_json(CONFIG_PATH, DEFAULT_CONFIG)
        self.q = queue.Queue()
        self.stop_flag = threading.Event()
        self.snap = None
        self.session_start = {}
        self._session_ready = False
        self.blink = False
        self.busy = False
        self._pulse_until = time.time() + 3.5
        self._toast_done = False
        self._toast = None
        self._alert_win = None
        self._preview = None
        self._alert_sent_this_run = set()
        self._burn = load_state().get("burn", {}) or {}
        self._burn_stats = None
        self._burn_saved = 0.0
        self._last_vals = None
        self._deltas = {}
        self._delta_until = 0.0
        self._rot = 0                       # 轮播到第几个账号
        try:
            _rot_s = int(self.cfg.get("rotate_seconds", 10) or 10)
        except (TypeError, ValueError):
            _rot_s = 10
        self._rot_next = time.time() + max(3, _rot_s)
        self._prev_used_map = {}
        self._ka_next = time.time() + 60     # 下次检查"监听器还活着吗"
        self._visible = True
        self._wb_gone_since = None
        self._follow = bool(self.cfg.get("follow_workbuddy", True))
        self._last_error = None               # 最近一次取数失败（保留旧数据时用）

        self.root = tk.Tk()
        self.root.title("WorkBuddy 积分挂件")
        self.root.overrideredirect(True)
        # 注意：Windows 上 -alpha 与 -transparentcolor 同时使用会导致整个窗口不可见，
        # 这里只用 -transparentcolor 做圆角，不使用 -alpha。
        self.use_rounded = bool(self.cfg.get("rounded", True))
        bg = TRANSPARENT if self.use_rounded else C_BG
        self.root.configure(bg=bg)
        if self.use_rounded:
            try:
                self.root.attributes("-transparentcolor", TRANSPARENT)
            except Exception:
                self.use_rounded = False
                bg = C_BG
                self.root.configure(bg=bg)
        self._set_topmost(self.cfg.get("topmost", True))

        self.W, self.H = 470, 34
        self.canvas = tk.Canvas(self.root, width=self.W, height=self.H,
                                bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack()

        fam = "Microsoft YaHei UI"
        if fam not in tkfont.families():
            fam = "Microsoft YaHei"
        self.f_small = tkfont.Font(family=fam, size=8)
        self.f_main = tkfont.Font(family=fam, size=9)
        self.f_bold = tkfont.Font(family=fam, size=9, weight="bold")

        self._place_window()
        self._bind_events()
        self._draw()

        if os.environ.get("WB_WIDGET_DEBUG"):
            try:
                os.makedirs(APP_DIR, exist_ok=True)
                with open(os.path.join(APP_DIR, "debug.txt"), "a", encoding="utf-8") as f:
                    f.write("screen=%sx%s scaling=%s geom=%s W=%s x=%s y=%s\n"
                            % (self.root.winfo_screenwidth(),
                               self.root.winfo_screenheight(),
                               self.root.tk.call("tk", "scaling"),
                               self.root.geometry(), self.W,
                               self.root.winfo_x(), self.root.winfo_y()))
            except Exception:
                pass

        threading.Thread(target=self._worker, daemon=True).start()
        self.root.after(400, self._pump)
        self.root.after(1000, self._tick)
        # 跟随 WorkBuddy：客户端没开就先藏起来
        if self._follow and workbuddy_running() is False:
            self._hide_widget(quiet=True)
        self.root.after(1500, self._watch_workbuddy)

    # ---------------- 窗口
    def _place_window(self):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = self.cfg.get("x")
        y = self.cfg.get("y")
        right = self.cfg.get("right")
        if not isinstance(x, int) or not isinstance(y, int) \
                or not (0 <= x <= sw - 60) or not (0 <= y <= sh - 30):
            x = y = right = None
        if not isinstance(right, int) or not (60 <= right <= sw - 4):
            right = (x + self.W) if isinstance(x, int) else None
        if x is None or y is None:
            x = sw - self.W - 24
            y = sh - self.H - 96
            right = x + self.W
        x = max(0, min(int(x), sw - 80))
        y = max(0, min(int(y), sh - 40))
        self.root.geometry("%dx%d+%d+%d" % (self.W, self.H, x, y))
        self.root.update_idletasks()
        self.pos = [x, y]
        self._anchor_right = right if isinstance(right, int) else (x + self.W)

    def _set_topmost(self, on):
        try:
            self.root.attributes("-topmost", bool(on))
        except Exception:
            pass

    def _bind_events(self):
        c = self.canvas
        c.bind("<Button-1>", self._drag_start)
        c.bind("<B1-Motion>", self._drag_move)
        c.bind("<ButtonRelease-1>", self._drag_end)
        c.bind("<Button-3>", self._menu)

    def _drag_start(self, e):
        self._dx, self._dy = e.x, e.y
        self._origin = (self.pos[0], self.pos[1])
        self._moved = False

    def _drag_move(self, e):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, min(self.pos[0] + e.x - self._dx, sw - 60))
        y = max(0, min(self.pos[1] + e.y - self._dy, sh - 24))
        self.root.geometry("+%d+%d" % (x, y))
        self.pos = [x, y]
        self._anchor_right = x + self.W
        if abs(x - self._origin[0]) > 4 or abs(y - self._origin[1]) > 4:
            self._moved = True

    def _drag_end(self, e):
        if getattr(self, "_moved", False):
            self.cfg["x"] = self.pos[0]
            self.cfg["y"] = self.pos[1]
            self.cfg["right"] = self.pos[0] + self.W
            save_json(CONFIG_PATH, self.cfg)
        else:
            self._show_preview()          # 单击 = 弹出预览面板

    def _reset_position(self):
        """复位到屏幕右下角默认位置。"""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.pos = [sw - self.W - 24, sh - self.H - 96]
        self._anchor_right = self.pos[0] + self.W
        self.cfg["x"] = self.pos[0]
        self.cfg["y"] = self.pos[1]
        self.cfg["right"] = self.pos[0] + self.W
        save_json(CONFIG_PATH, self.cfg)
        self.root.geometry("%dx%d+%d+%d"
                           % (self.W, self.H, self.pos[0], self.pos[1]))
        self._set_topmost(True)
        self.cfg["topmost"] = True
        save_json(CONFIG_PATH, self.cfg)

    # ---------------- 右键菜单
    def _menu(self, e):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="立即刷新", command=self._force_refresh)
        m.add_command(label="复位到右下角", command=self._reset_position)
        m.add_separator()
        v = tk.StringVar(value=self.cfg.get("view", "current"))
        m.add_radiobutton(label="视图：当前账号", value="current", variable=v,
                          command=lambda: self._set_view("current"))
        m.add_radiobutton(label="视图：全账号合计", value="all", variable=v,
                          command=lambda: self._set_view("all"))
        iv = tk.IntVar(value=int(self.cfg.get("refresh_seconds", 60)))
        sub = tk.Menu(m, tearoff=0)
        for lab, sec in (("30 秒", 30), ("1 分钟", 60), ("5 分钟", 300)):
            sub.add_radiobutton(label=lab, value=sec, variable=iv,
                                command=lambda s=sec: self._set_interval(s))
        m.add_cascade(label="刷新间隔（当前账号）", menu=sub)
        pv = tk.IntVar(value=int(self.cfg.get("poll_seconds", 15) or 0))
        psub = tk.Menu(m, tearoff=0)
        for lab, sec in (("5 秒（最灵敏）", 5), ("15 秒（默认）", 15), ("30 秒", 30),
                         ("1 分钟", 60), ("关闭", 0)):
            psub.add_radiobutton(label=lab, value=sec, variable=pv,
                                 command=lambda s=sec: self._set_poll(s))
        m.add_cascade(label="余额轮询（全部账号）", menu=psub)
        rv2 = tk.IntVar(value=int(self.cfg.get("rotate_seconds", 10) or 0))
        rsub2 = tk.Menu(m, tearoff=0)
        for lab, sec in (("关闭（只看当前在线账号）", 0), ("5 秒", 5), ("10 秒（默认）", 10),
                         ("20 秒", 20), ("30 秒", 30)):
            rsub2.add_radiobutton(label=lab, value=sec, variable=rv2,
                                  command=lambda x=sec: self._set_rotate(x))
        m.add_cascade(label="多账号轮播", menu=rsub2)
        av = tk.IntVar(value=int(self.cfg.get("alert_days", 3) or 0))
        asub = tk.Menu(m, tearoff=0)
        for lab, d in (("1 天内", 1), ("3 天内（默认）", 3), ("5 天内", 5), ("关闭提醒", 0)):
            asub.add_radiobutton(label=lab, value=d, variable=av,
                                 command=lambda x=d: self._set_alert_days(x))
        m.add_cascade(label="到期提醒阈值", menu=asub)
        m.add_command(label=("到期提醒：测试弹一次" ),
                      command=self._test_alert)
        rv = tk.IntVar(value=int(self.cfg.get("runout_days", 7) or 0))
        rsub = tk.Menu(m, tearoff=0)
        for lab, d in (("3 天内", 3), ("7 天内（默认）", 7), ("14 天内", 14), ("关闭提醒", 0)):
            rsub.add_radiobutton(label=lab, value=d, variable=rv,
                                 command=lambda x=d: self._set_runout_days(x))
        m.add_cascade(label="耗尽提醒阈值", menu=rsub)
        m.add_command(label="耗尽提醒：测试弹一次", command=self._test_runout_alert)
        m.add_command(label="打开预览面板（单击同效）", command=self._show_preview)
        m.add_command(label="查看明细（纯文本窗口）", command=self._detail)
        m.add_command(label=("取消置顶" if self.cfg.get("topmost", True) else "始终置顶"),
                      command=self._toggle_top)
        m.add_command(label=("关闭圆角" if self.use_rounded else "开启圆角"),
                      command=self._toggle_rounded)
        m.add_command(label=("关闭「跟随 WorkBuddy 启停」" if self._follow
                             else "开启「跟随 WorkBuddy 启停」"),
                      command=self._toggle_follow)
        wsub = tk.Menu(m, tearoff=0)
        wsub.add_command(label=("移除「随 WorkBuddy 自动启动」" if autostart_installed()
                                else "启用「随 WorkBuddy 自动启动」"),
                         command=self._toggle_autostart)
        wsub.add_command(label="查看监听状态", command=self._watcher_status)
        m.add_cascade(label="随 WorkBuddy 自动启动", menu=wsub)
        m.add_command(label=("关闭启动盘点提示" if self.cfg.get("startup_toast", True)
                             else "开启启动盘点提示"),
                      command=self._toggle_toast_cfg)
        m.add_command(label="立即看多账号盘点", command=lambda: self._show_toast(self.snap or {}))
        m.add_separator()
        m.add_command(label="退出挂件", command=self.quit)
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def _set_view(self, v):
        self.cfg["view"] = v
        save_json(CONFIG_PATH, self.cfg)
        self._draw()

    def _set_interval(self, s):
        self.cfg["refresh_seconds"] = s
        save_json(CONFIG_PATH, self.cfg)
        self._force_refresh()

    def _set_poll(self, s):
        self.cfg["poll_seconds"] = s
        save_json(CONFIG_PATH, self.cfg)
        self._force_refresh()

    def _set_rotate(self, s):
        self.cfg["rotate_seconds"] = s
        save_json(CONFIG_PATH, self.cfg)
        self._rot = 0
        self._rot_next = time.time() + max(3, int(s or 10))
        self._draw()

    def _set_alert_days(self, d):
        self.cfg["alert_days"] = d
        save_json(CONFIG_PATH, self.cfg)
        if d:
            self._alert_sent_this_run.clear()
        self._draw()

    def _test_alert(self):
        """手动弹一次到期提醒，便于确认样式。"""
        s = self.snap or {}
        items = [b for b in (s.get("soon_all") or []) if b.get("ts")]
        if not items:
            self._show_expiry_alert(
                [{"account": "（示例）138****8888", "end": "2026-09-30 23:59:59",
                  "remain": 500.0, "ts": time.time() + 2 * 86400 + 10 * 3600}],
                "3 天内（示例数据，暂无真实临期批次）")
            return
        self._show_expiry_alert(items, "%d 天内" % int(self.cfg.get("alert_days", 3) or 3))

    def _set_runout_days(self, d):
        self.cfg["runout_days"] = d
        save_json(CONFIG_PATH, self.cfg)
        if d:
            self._alert_sent_this_run.clear()
        self._draw()

    def _test_runout_alert(self):
        """手动弹一次耗尽提醒，便于确认样式。"""
        st = self._burn_stats or {}
        low = self._low_balance_accounts(self.snap or {})
        if not st.get("ok") or st.get("days_left") is None:
            demo = {"ok": True, "rate": 620.0, "days_left": 5.3, "span_days": 3.0, "samples": 4,
                    "remain": 3286.0, "runout_ts": time.time() + 5.3 * 86400,
                    "today_only": False}
            self._show_runout_alert(demo, demo["days_left"], low)
            return
        self._show_runout_alert(st, st.get("days_left"), low)

    def _toggle_top(self):
        self.cfg["topmost"] = not self.cfg.get("topmost", True)
        self._set_topmost(self.cfg["topmost"])
        save_json(CONFIG_PATH, self.cfg)

    def _toggle_toast_cfg(self):
        self.cfg["startup_toast"] = not self.cfg.get("startup_toast", True)
        save_json(CONFIG_PATH, self.cfg)

    def _toggle_rounded(self):
        self.cfg["rounded"] = not self.use_rounded
        save_json(CONFIG_PATH, self.cfg)
        self.use_rounded = bool(self.cfg["rounded"])
        bg = TRANSPARENT if self.use_rounded else C_BG
        try:
            self.root.configure(bg=bg)
            self.canvas.configure(bg=bg)
            self.root.attributes("-transparentcolor",
                                  TRANSPARENT if self.use_rounded else "")
        except Exception:
            pass
        self._draw()

    def _force_refresh(self):
        self.busy = True
        self._draw()
        threading.Thread(target=self._fetch_all, daemon=True).start()

    # ---------------- 数据线程
    def _focus_uids(self):
        """完整刷新档要刷哪些账号：当前在线账号；视图为「全账号合计」时刷全部。"""
        ids = set()
        cur = (self.snap or {}).get("current") or {}
        if cur.get("uid"):
            ids.add(cur["uid"])
        if self.cfg.get("view") == "all":
            ids = {a["uid"] for a in (self.snap or {}).get("accounts", [])}
        return ids

    def _worker(self):
        """三层刷新：15s 只刷余额（全部账号）/ 60s 完整刷当前账号 / 600s 全账号盘点。"""
        first = True
        last_full = last_refresh = last_poll = 0.0
        while not self.stop_flag.is_set():
            try:
                now = time.time()
                poll_s = int(self.cfg.get("poll_seconds", 15) or 0)
                full_due = (now - last_full) >= float(self.cfg.get("full_seconds", 600))
                ref_due = (now - last_refresh) >= float(self.cfg.get("refresh_seconds", 60))
                poll_due = poll_s > 0 and (now - last_poll) >= poll_s
                snap = None
                if first or full_due:
                    snap = collect(self.session_start)
                    last_full = last_refresh = last_poll = now
                    if not self._session_ready and snap.get("accounts"):
                        self.session_start = {a["uid"]: (a.get("total_remain") or 0)
                                              for a in snap["accounts"] if a.get("ok")}
                        self._session_ready = True
                elif ref_due:
                    ids = self._focus_uids()
                    if ids:
                        snap = collect(self.session_start, fast_only_uids=ids, prev=self.snap)
                    else:
                        snap = collect(self.session_start, prev=self.snap, balance_all=True)
                    last_refresh = last_poll = now
                elif poll_due:
                    snap = collect(self.session_start, prev=self.snap, balance_all=True)
                    last_poll = now
                if snap is not None:
                    self.q.put(snap)
                first = False
            except Exception as ex:
                self.q.put({"error": "%s: %s" % (type(ex).__name__, ex)})
            for _ in range(10):                 # 1 秒粒度，便于及时响应退出 / 手动刷新
                if self.stop_flag.is_set():
                    return
                time.sleep(0.1)
                if getattr(self, "_wake", False):
                    self._wake = False
                    break

    def _fetch_all(self):
        try:
            snap = collect(self.session_start)
            if not self._session_ready:
                self.session_start = {a["uid"]: (a.get("total_remain") or 0)
                                      for a in snap.get("accounts", []) if a.get("ok")}
                self._session_ready = True
            self.q.put(snap)
        except Exception as ex:
            self.q.put({"error": "%s: %s" % (type(ex).__name__, ex)})

    def _pump(self):
        got = False
        try:
            while True:
                item = self.q.get_nowait()
                self.busy = False
                got = True
                if (isinstance(item, dict) and item.get("error")
                        and (self.snap or {}).get("accounts")):
                    # 已经有过好数据时，一次取数失败不该把界面冲成"积分取数失败"：
                    # 那会让胶囊丢数字、轮播丢账号，连退出时的当日余额落盘都拿不到。
                    # 保留上一轮，只记下错误（右下角的"N 秒前"会继续变老，就是信号）。
                    self._last_error = item.get("error")
                    self.snap = dict(self.snap)
                    self.snap["stale_seconds"] = time.time()
                else:
                    self._last_error = None
                    self.snap = item
        except queue.Empty:
            pass
        if got:
            # 面板/提醒出问题也不能让重绘循环断掉
            try:
                self._note_change(self.snap or {})
            except Exception as ex:
                self._log("delta: %s: %s" % (type(ex).__name__, ex))
            try:
                self._maybe_toast()
            except Exception as ex:
                self._log("toast: %s: %s" % (type(ex).__name__, ex))
            try:
                self._update_burn(self.snap or {})
            except Exception as ex:
                self._log("burn: %s: %s" % (type(ex).__name__, ex))
            try:
                self._check_expiry_alerts(self.snap or {})
            except Exception as ex:
                self._log("alert: %s: %s" % (type(ex).__name__, ex))
            try:
                self._check_runout_alert(self.snap or {})
            except Exception as ex:
                self._log("runout: %s: %s" % (type(ex).__name__, ex))
        try:
            self._rotate()
        except Exception as ex:
            self._log("rotate: %s: %s" % (type(ex).__name__, ex))
        self._draw()
        self.root.after(400 if time.time() < self._pulse_until else 800, self._pump)

    # ---------------- 多账号轮播（把其余账号也同步显示出来）
    def _ok_accounts(self):
        return [a for a in ((self.snap or {}).get("accounts") or []) if a.get("ok")]

    def _rotate_seconds(self):
        try:
            return int(self.cfg.get("rotate_seconds", 10) or 0)
        except (TypeError, ValueError):
            return 10

    def _rotate_on(self):
        return self._rotate_seconds() > 0 and len(self._ok_accounts()) > 1

    def _rot_account(self):
        """轮播开启时返回当轮要显示的账号，否则返回当前在线账号。"""
        cur = (self.snap or {}).get("current") or {}
        if not self._rotate_on():
            return cur
        ok = self._ok_accounts()
        return ok[self._rot % len(ok)] if ok else cur

    def _rotate(self):
        """按间隔推进轮播指针（在重绘循环里调用）。"""
        if not self._rotate_on():
            self._rot = 0
            return
        now = time.time()
        if now >= self._rot_next:
            ok = self._ok_accounts()
            self._rot = (self._rot + 1) % max(1, len(ok))
            self._rot_next = now + max(3, self._rotate_seconds())

    def _note_change(self, s):
        """对比上一轮数值，记下「刚变化了多少」，用于在胶囊上闪一下。"""
        cur = s.get("current") or {}
        vals = {"cur": cur.get("total_remain"),
                "all": s.get("total_remain_all"),
                "used": s.get("used_today_all")}
        per = {a["uid"]: a.get("used_today") for a in (s.get("accounts") or [])
               if a.get("ok") and a.get("uid")}
        prev = self._last_vals
        deltas = {}
        if prev:
            for k, v in vals.items():
                p = prev.get(k)
                if v is None or p is None:
                    continue
                try:
                    diff = round(float(v) - float(p), 2)
                except (TypeError, ValueError):
                    continue
                if abs(diff) >= 0.01:
                    deltas[k] = diff
        # 轮播时「今日」显示的是当轮账号，变化提示也要用同一个口径
        if self._rotate_on():
            uid = (self._rot_account() or {}).get("uid")
            v = per.get(uid)
            p = self._prev_used_map.get(uid)
            deltas.pop("used", None)
            if uid and v is not None and p is not None and abs(float(v) - float(p)) >= 0.01:
                deltas["used"] = round(float(v) - float(p), 2)
        self._prev_used_map = per
        self._last_vals = vals
        if deltas:
            self._deltas = deltas
            self._delta_until = time.time() + 6.0      # 闪 6 秒，够看清「刚消耗了多少」

    def _tick(self):
        self.blink = not self.blink
        self._draw()
        self.root.after(30000, self._tick)

    # ---------------- 绘制
    def _rrect(self, x1, y1, x2, y2, r, fill, outline, width=1):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
               x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        item = self.canvas.create_polygon(pts, smooth=True, splinesteps=24,
                                          fill=fill, outline=outline, width=width)
        self._capsule = item
        return item

    def _draw(self):
        c = self.canvas
        c.delete("all")
        H, W = self.H, self.W
        bc, bw = self._capsule_style()
        self._rrect(0.5, 0.5, W - 0.5, H - 0.5, H / 2.0, C_BG, bc, bw)

        s = self.snap
        err = (s or {}).get("error")
        ok_all = s and s.get("ok_count", 0) == s.get("account_count", 0)
        any_ok = s and s.get("ok_count", 0) > 0

        dot = C_OK
        if err or not any_ok:
            dot = C_DANGER
        elif not ok_all:
            dot = C_WARN
        c.create_oval(14, H / 2 - 3.5, 21, H / 2 + 3.5, fill=dot, outline="")

        x = 30
        if err:
            c.create_text(x, H / 2, anchor="w", text="积分取数失败", fill=C_DANGER,
                          font=self.f_bold)
            x += self.f_bold.measure("积分取数失败") + 10
            short = str(err)[:46]
            c.create_text(x, H / 2, anchor="w", text=short, fill=C_SUB,
                          font=self.f_small)
            self._autosize(x + self.f_small.measure(short))
            return
        if not s:
            c.create_text(x, H / 2, anchor="w", text="正在读取积分…", fill=C_SUB,
                          font=self.f_main)
            self._autosize(x + self.f_main.measure("正在读取积分…"))
            return

        view = self.cfg.get("view", "current")
        rotating = False
        if view == "all":
            label = "全账号 %d个" % s.get("account_count", 0)
            remain = s.get("total_remain_all", 0)
            used_t = s.get("used_today_all", 0)
            used_s = s.get("used_session_all", 0)
            soon3 = s.get("soon3_all", 0)
            soon5 = s.get("soon5_all", 0)
            cd_ts = s.get("next3_ts")
        else:
            cur = self._rot_account()
            rotating = self._rotate_on()
            label = cur.get("label") or "当前账号"
            if rotating:
                label = "⟳ " + label
            remain = cur.get("total_remain", 0)
            # 轮播时「今日」跟着当轮账号走，非轮播显示全账号合计（消耗可能在别的号上）
            used_t = cur.get("used_today") if rotating else None
            if used_t is None:
                used_t = s.get("used_today_all", 0)
            used_s = s.get("used_session_cur", 0)
            soon3 = cur.get("soon3", 0)
            soon5 = cur.get("soon5", 0)
            # 倒计时要和上面这行「3日到期」是同一个账号、同一批额度
            cd_ts = cur.get("next3_ts") if rotating else s.get("next3_ts")

        x = self._cell(x, "", label, C_TEXT, self.f_bold, 150)
        if view == "current" and (s.get("account_count") or 0) > 1:
            if self._rotate_on():
                ok = self._ok_accounts()
                tag = "%d/%d" % (self._rot % len(ok) + 1, len(ok))
            else:
                tag = "共%d号" % s["account_count"]
            c.create_text(x + 3, H / 2, anchor="w", text=tag, fill=C_SUB,
                          font=self.f_small)
            x += self.f_small.measure(tag) + 6
        x = self._sep(x)
        x = self._cell(x, "余额", fmt(remain), C_ACCENT, self.f_bold, 108)
        x = self._sep(x)
        x = self._cell(x, "今日", fmt_used(used_t), C_WARN, self.f_main, 116)
        # 最近一次刷新涨了多少消耗，红色闪 3 秒（任意账号的消耗都会算进「今日」）
        if time.time() < self._delta_until and self._deltas.get("used"):
            txt = "+%s" % fmt(self._deltas["used"])
            c.create_text(x, H / 2, anchor="w", text=txt, fill=C_DANGER, font=self.f_bold)
        x += self._delta_width()
        x = self._sep(x)
        if soon3 > 0 and self._alert_on():
            # 有 3 天内到期的额度：⚠ 闪烁（占位固定，避免抖动）+ 整段标红
            gw = self.f_bold.measure("⚠") + 2
            if int(time.time() * 1.4) % 2 == 0:
                c.create_text(x, H / 2, anchor="w", text="⚠", fill=C_DANGER,
                              font=self.f_bold)
            x += gw
        x = self._cell(x, "3日到期", fmt(soon3), C_DANGER if soon3 > 0 else C_SUB,
                       self.f_bold, 128)
        # 只有「3日到期」本身不为 0 才跟倒计时，否则会出现「3日到期 0 剩3天3时」这种自相矛盾的读法
        cd = countdown_text(cd_ts) if soon3 > 0 else ""
        if cd:
            c.create_text(x, H / 2, anchor="w", text="剩" + cd,
                          fill=C_DANGER, font=self.f_small)
            x += self.f_small.measure("剩" + cd) + 4
        x = self._sep(x)
        x = self._cell(x, "5日", fmt(soon5), C_WARN if soon5 > 0 else C_SUB,
                       self.f_small, 74)
        rd, _bs = self._runout_now()
        if rd is not None:
            # 按当前消耗速度快用完了：≈ 耗尽约X天（红色）
            sym = "≈"
            c.create_text(x, H / 2, anchor="w", text=sym, fill=C_DANGER, font=self.f_bold)
            x += self.f_bold.measure(sym) + 1
            txt = "耗尽约%s" % span_text(rd * 86400)
            c.create_text(x, H / 2, anchor="w", text=txt, fill=C_DANGER, font=self.f_bold)
            x += self.f_bold.measure(txt) + 2
        self._autosize(x)

    def _autosize(self, end_x):
        """按内容宽度自适应窗口，保持右边缘位置不变。"""
        # 右下角还有「已同步 / 12s前」这类状态文字，右侧要给它留位置，否则会叠字
        need = int(end_x + self._footer_width() + 22)
        need = max(180, min(need, self.root.winfo_screenwidth() - 40))
        if abs(need - self.W) > 3:
            self.W = need
            x = max(0, self._anchor_right - self.W)
            self.pos[0] = x
            try:
                self.canvas.config(width=self.W)
                self.root.geometry("%dx%d+%d+%d" % (self.W, self.H, x, self.pos[1]))
            except Exception:
                pass
            c = self.canvas
            if getattr(self, "_capsule", None) is not None:
                c.delete(self._capsule)
            bc, bw = self._capsule_style()
            self._capsule = self._rrect(0.5, 0.5, self.W - 0.5, self.H - 0.5,
                                        self.H / 2.0, C_BG, bc, bw)
            c.tag_lower(self._capsule)
        self._footer(self.W - 20, self.H / 2)

    def _alert_on(self):
        return int(self.cfg.get("alert_days", 3) or 0) > 0

    def _capsule_style(self):
        """启动后 3.5 秒内闪烁边框；有临期积分时用淡红边框常亮。"""
        if time.time() < getattr(self, "_pulse_until", 0):
            on = int((self._pulse_until - time.time()) * 4) % 2 == 0
            return (C_ACCENT if on else C_DANGER), 2
        if self._alert_on() and ((self.snap or {}).get("soon3_all") or 0) > 0:
            return "#EFB3AB", 2
        if self._runout_on() and self._runout_now()[0] is not None:
            return "#EFB3AB", 2
        return C_BORDER, 1

    def _delta_width(self):
        """给「+8,888」这种瞬时变化提示预留的固定宽度（避免窗口忽宽忽窄）。"""
        return self.f_bold.measure("+8,888") + 4

    def _footer_width(self):
        """footer 可能出现的文字里最宽的那个，用于给右侧预留空间。"""
        f = self.f_small
        return max(f.measure(t) for t in ("同步中…", "已同步", "88s前"))

    def _footer(self, x, y):
        """右下角显示同步状态：同步中… / 已同步 / Ns前 —— 让人一眼看出在同步。"""
        if self.busy:
            txt, col = "同步中…", C_ACCENT
        else:
            age = None
            ts = (self.snap or {}).get("ts")
            if ts:
                try:
                    age = max(0, int(time.time() - datetime.datetime.strptime(
                        ts, "%Y-%m-%d %H:%M:%S").timestamp()))
                except Exception:
                    age = None
            if age is None:
                txt, col = ("●" if self.blink else "○"), C_SUB
            elif age < 5:
                txt, col = "已同步", C_OK
            else:
                txt, col = "%ds前" % age, C_SUB
        self.canvas.create_text(x, y, anchor="e", text=txt, fill=col, font=self.f_small)

    def _cell(self, x, label, value, color, font, width):
        c = self.canvas
        c.create_text(x, self.H / 2, anchor="w", text=label, fill=C_SUB,
                      font=self.f_small)
        lw = self.f_small.measure(label)
        c.create_text(x + lw + 5, self.H / 2, anchor="w", text=str(value),
                      fill=color, font=font)
        vw = font.measure(str(value))
        return x + lw + 5 + vw + 4

    def _sep(self, x):
        x += 6
        self.canvas.create_text(x, self.H / 2, anchor="w", text="│", fill="#E3E8EE",
                                font=self.f_small)
        return x + 11

    # ---------------- 跟随 WorkBuddy 启停
    def _watch_workbuddy(self):
        try:
            if self._follow:
                running = workbuddy_running()
                if running is True:
                    if self._wb_gone_since is not None:
                        # 只在"曾经判定为关闭过"时记一笔，正常运行时不刷屏
                        widget_log("WorkBuddy window back after %.1fs"
                                   % (time.time() - self._wb_gone_since))
                    self._wb_gone_since = None
                    if not self._visible:
                        self._show_widget()
                elif running is False:
                    if self._wb_gone_since is None:
                        self._wb_gone_since = time.time()
                        widget_log("WorkBuddy window gone -> grace %.0fs (managed=%s, exit_with=%s)"
                                   % (float(self.cfg.get("exit_grace_seconds", 8) or 8),
                                      MANAGED, self.cfg.get("exit_with_workbuddy", True)))
                    elif self._visible and time.time() - self._wb_gone_since > 20:
                        self._hide_widget()
                    # 被监听器拉起时：WorkBuddy 退出就把挂件也结束掉，不留后台进程
                    if (MANAGED and self.cfg.get("exit_with_workbuddy", True)
                            and time.time() - self._wb_gone_since
                            > float(self.cfg.get("exit_grace_seconds", 8) or 8)):
                        widget_log("quitting: WorkBuddy window closed for %.1fs"
                                   % (time.time() - self._wb_gone_since))
                        self.quit()
                        return
            self._keepalive()
        except Exception as e:
            widget_log("_watch_workbuddy error: %r" % (e,))
        self.root.after(2000, self._watch_workbuddy)

    def _keepalive(self):
        """互相保活：挂了监听器就没人能再把挂件拉起来，所以由挂件定期补位。

        只在用户确实装了自启项时才做——用户主动移除自启项（不想要后台程序）
        的时候必须尊重，不能偷偷把监听器再起一个。
        """
        if time.time() < self._ka_next:
            return
        self._ka_next = time.time() + 60
        try:
            if autostart_installed() and not watcher_pid():
                pid = start_watcher_now()
                watcher_log("watcher was missing -> widget revived it (pid=%s)" % pid)
        except Exception as e:
            watcher_log("keepalive failed: %r" % e)

    def _show_widget(self):
        self._visible = True
        try:
            self.root.deiconify()
            self._set_topmost(self.cfg.get("topmost", True))
            self._pulse_until = time.time() + 3.5
        except Exception:
            pass
        if self.cfg.get("startup_toast", True):
            self._toast_done = False          # 重新弹一次盘点面板
        self._draw()

    def _hide_widget(self, quiet=False):
        self._visible = False
        self._close_panel(self._toast)
        self._toast = None
        self._close_panel(self._alert_win)
        self._alert_win = None
        self._close_panel(self._preview)
        self._preview = None
        try:
            self.root.withdraw()
        except Exception:
            pass
        if not quiet:
            self._toast_done = False

    def _toggle_follow(self):
        self.cfg["follow_workbuddy"] = not self.cfg.get("follow_workbuddy", True)
        self._follow = bool(self.cfg["follow_workbuddy"])
        save_json(CONFIG_PATH, self.cfg)
        if not self._follow and not self._visible:
            self._show_widget()

    # ---------------- 随 WorkBuddy 自动启动（启动的是监听器，不是这个窗口）
    def _toggle_autostart(self):
        if autostart_installed():
            remove_autostart()
            self._info("已移除「随 WorkBuddy 自动启动」。\n\n"
                       "登录后不会再有任何后台监听；\n"
                       "想用挂件请手动双击 start_widget.vbs。")
        else:
            try:
                p = install_autostart()
            except Exception as e:
                self._info("安装自启项失败：%s" % e)
                return
            if not watcher_pid():
                try:
                    subprocess.Popen([pythonw_path(), os.path.abspath(__file__), "--watch"],
                                     cwd=os.path.dirname(os.path.abspath(__file__)),
                                     creationflags=0x00000008 | 0x08000000)
                except Exception:
                    pass
            self._info("已启用「随 WorkBuddy 自动启动」。\n\n"
                       "启动项：%s\n\n"
                       "说明：放在启动项里的是一个「无界面监听器」——\n"
                       "· 开机时它只是安静等待，不显示任何窗口；\n"
                       "· WorkBuddy 一启动 → 挂件出现；\n"
                       "· WorkBuddy 一退出 → 挂件进程自动结束。\n\n"
                       "挂件窗口本身不再开机启动。想彻底不要任何开机项，\n"
                       "在这个菜单里再点一次「移除」即可。" % p)

    def _watcher_status(self):
        wb = {True: "运行中", False: "未运行"}.get(workbuddy_running(), "无法检测")
        w = watcher_pid()
        g = widget_pid()
        self._info("WorkBuddy 客户端：%s\n"
                   "监听器：%s\n"
                   "挂件进程：%s\n"
                   "开机自启项：%s\n"
                   "跟随 WorkBuddy 显隐：%s\n"
                   "WorkBuddy 退出后挂件自动结束：%s"
                   % (wb,
                      ("运行中 pid=%d" % w) if w else "未运行",
                      ("运行中 pid=%d" % g) if g else "未运行",
                      "已安装" if autostart_installed() else "未安装",
                      "开" if self._follow else "关",
                      "开" if self.cfg.get("exit_with_workbuddy", True) else "关"))

    def _info(self, text):
        """这些是低频的开关操作，用系统对话框说清楚就好（挂在挂件自己的 root 上）。"""
        try:
            from tkinter import messagebox
            messagebox.showinfo("WorkBuddy 积分挂件", text, parent=self.root)
        except Exception:
            show_msg(text)

    # ---------------- 启动盘点提示（多账号概览）
    def _maybe_toast(self):
        if self._toast_done or not self.cfg.get("startup_toast", True) or not self._visible:
            return
        s = self.snap or {}
        if not s.get("ok_count"):
            return
        self._toast_done = True
        self._show_toast(s)

    def _show_toast(self, s):
        fam = self.f_main.actual("family")
        self._close_panel(self._alert_win)      # 面板都停在挂件正上方，避免互相叠住
        self._alert_win = None
        soon3 = [b for b in (s.get("soon_all") or [])
                 if (b.get("days_left") is not None and b["days_left"] <= 3)]
        blocks = [
            ("title", "WorkBuddy 积分盘点 · %d 个账号" % s.get("account_count", 0)),
            ("sub", "总余额 %s　今日消耗 %s　近 3 日到期 %s（%d 笔）"
                    % (fmt(s.get("total_remain_all", 0)),
                       fmt_used(s.get("used_today_all", 0)),
                       fmt(s.get("soon3_all", 0)), len(soon3))),
            ("hr", None),
        ]
        for a in (s.get("accounts") or []):
            if not a.get("ok"):
                blocks.append(("fail", "%s　取数失败：%s" % (a.get("label"), a.get("error"))))
                continue
            head = a.get("label") or "-"
            if a.get("is_current"):
                head += "（当前在线）"
            right = "余额 %s" % fmt(a.get("total_remain", 0))
            if a.get("used_today"):
                right += "　今日 -%s" % fmt(a.get("used_today", 0))
            if (a.get("soon3") or 0) > 0:
                right += "　3日内到期 %s" % fmt(a.get("soon3", 0))
            if a.get("next_end"):
                right += "　最近 %s" % str(a.get("next_end"))[:16]
            blocks.append(("row", (head, right, (a.get("soon3") or 0) > 0)))
        blocks.append(("hr", None))
        risk = [a for a in (s.get("accounts") or [])
                if a.get("token_exp_days") is not None and a.get("token_exp_days") < 7]
        if risk:
            blocks.append(("fail", "⚠ %s 需重新登录续期"
                           % "、".join(a.get("label") or "-" for a in risk)))
        blocks.append(("foot", "点击关闭 · %d 秒后自动隐藏 · 右键挂件可切换「全账号合计」视图"
                       % int(self.cfg.get("toast_seconds", 20))))
        self._toast = self._panel(blocks, accent=C_BORDER,
                                  seconds=int(self.cfg.get("toast_seconds", 20)))

    # ---------------- 单击预览面板
    def _burn_line(self):
        """一行文字：日均消耗 + 预计还能用多久。"""
        st = self._burn_stats or {}
        if not st.get("ok"):
            return st.get("reason") or "采样不足，继续观察中"
        rate = st.get("rate") or 0
        if st.get("days_left") is None:
            return "日均消耗 %s 分　%s" % (fmt(rate), st.get("reason") or "余额未见下降")
        txt = "日均消耗 %s 分　预计 %s 后耗尽" % (fmt(rate), span_text(st["days_left"] * 86400))
        if st.get("runout_ts"):
            txt += "（约 %s）" % datetime.datetime.fromtimestamp(
                st["runout_ts"]).strftime("%m-%d %H:%M")
        if st.get("today_only"):
            txt += "　仅按今日推算"
        return txt

    def _show_preview(self):
        """单击挂件弹出：账号总览 + 近 5 天逐笔到期（样式与预览图一致，点一下关闭）。"""
        s = self.snap or {}
        if not s.get("ok_count"):
            self._close_panel(self._preview)
            self._preview = None
            return
        fam = self.f_main.actual("family")
        accounts = s.get("accounts") or []
        soon = s.get("soon_all") or []
        soon3 = [b for b in soon if (b.get("days_left") or 99) <= 3]
        soon5 = [b for b in soon if (b.get("days_left") or 99) <= 5]
        blocks = [
            ("title", "WorkBuddy 积分预览 · %s" % s.get("ts")),
            ("sub", "总余额 %s　今日消耗 %s　3 日到期 %s（%d 笔）　5 日合计 %s"
                    % (fmt(s.get("total_remain_all", 0)),
                       fmt_used(s.get("used_today_all", 0)),
                       fmt(s.get("soon3_all", 0)), len(soon3), fmt(s.get("soon5_all", 0)))),
            ("sect", "消耗与预测 · 近 %d 天采样 %d 个"
                     % (int(self.cfg.get("runout_window_days", 7) or 7),
                        (self._burn_stats or {}).get("samples", 0))),
            ("sub", self._burn_line()),
            ("sect", "账号 · %d 个（红色行 = 该账号有 3 天内到期额度）" % s.get("account_count", 0)),
        ]
        low_limit = float(self.cfg.get("runout_min_balance", 100) or 0)
        for a in accounts:
            if not a.get("ok"):
                blocks.append(("fail", "%s　取数失败：%s" % (a.get("label"), a.get("error"))))
                continue
            head = (a.get("label") or "-") + ("（当前在线）" if a.get("is_current") else "")
            right = "余额 %s" % fmt(a.get("total_remain", 0))
            if a.get("used_today"):
                right += "　今日 -%s" % fmt(a.get("used_today", 0))
            if (a.get("soon3") or 0) > 0:
                right += "　3日内 %s" % fmt(a.get("soon3", 0))
            right += "　最近 %s" % (str(a.get("next_end"))[5:16] if a.get("next_end") else "-")
            hot = (a.get("soon3") or 0) > 0
            if low_limit > 0 and float(a.get("total_remain") or 0) < low_limit:
                right += "　⚠低余额"
                hot = True
            blocks.append(("row", (head, right, hot)))
        total5 = round(sum(b.get("remain") or 0 for b in soon5), 2)
        blocks.append(("sect", "近 5 天到期 · %d 笔、合计 %s 分" % (len(soon5), fmt(total5))))
        if not soon5:
            blocks.append(("sub", "（近 5 天没有会清零的额度）"))
        for b in soon5[:14]:
            blocks.append(("row", (str(b.get("end"))[5:16],
                                   "%s　%s 分　剩 %s"
                                   % (b.get("account") or "-", fmt(b.get("remain", 0)),
                                      countdown_text(b.get("ts"))),
                                   (b.get("days_left") or 9) <= 3)))
        risk = [a for a in accounts
                if a.get("token_exp_days") is not None and a.get("token_exp_days") < 7]
        if risk:
            blocks.append(("fail", "⚠ %s 需重新登录续期"
                           % "、".join(a.get("label") or "-" for a in risk)))
        blocks.append(("foot", "点击面板任意位置关闭 · 右键挂件可看纯文本明细 / 切换视图"))
        self._close_panel(self._alert_win)      # 用户主动看预览时，让自动提醒面板让位
        self._alert_win = None
        self._close_panel(self._preview)
        self._preview = self._panel(blocks, accent=C_BORDER, seconds=0)

    # ---------------- 到期提醒
    def _check_expiry_alerts(self, s):
        """临近到期（默认 ≤3 天）时标红并弹提醒；同一批批次+同一阈值只提醒一次。"""
        days_limit = self.cfg.get("alert_days", 3)
        if not days_limit or not self._visible:
            return
        items = [b for b in (s.get("soon_all") or [])
                 if b.get("days_left") is not None
                 and 0 <= b["days_left"] <= days_limit and b.get("ts")]
        if not items:
            return
        left_h = (min(b["ts"] for b in items) - time.time()) / 3600.0
        if left_h <= 6:
            level, level_txt = "6h", "6 小时内"
        elif left_h <= 24:
            level, level_txt = "1d", "24 小时内"
        else:
            level, level_txt = "3d", "3 天内"
        key = "%s::%s" % (level, "|".join(sorted("%s@%s" % (b.get("account"), b.get("end"))
                                                 for b in items)))
        if key in self._alert_sent_this_run or alert_already_sent(key):
            return
        self._alert_sent_this_run.add(key)
        mark_alert_sent(key)
        self._show_expiry_alert(items, level_txt)

    def _show_expiry_alert(self, items, level_txt):
        items = sorted(items, key=lambda b: b["ts"])[:12]
        total = round(sum(b.get("remain") or 0 for b in items), 2)
        blocks = [
            ("alert", "⚠ 积分即将到期提醒 · %s清零" % level_txt),
            ("sub", "共 %d 笔、合计 %s 分即将过期，用不完就作废" % (len(items), fmt(total))),
            ("hr", None),
        ]
        for b in items:
            head = str(b.get("end") or "")[5:16]
            right = "%s　%s 分　剩 %s" % (b.get("account") or "-",
                                        fmt(b.get("remain", 0)),
                                        countdown_text(b.get("ts")))
            blocks.append(("row", (head, right, True)))
        blocks.append(("hr", None))
        blocks.append(("foot", "优先跑批量任务用掉这批额度；点击关闭 · %d 秒后自动隐藏"
                       % int(self.cfg.get("alert_seconds", 45))))
        self._close_panel(self._toast)          # 与启动盘点面板互斥，避免叠在一起
        self._toast = None
        self._close_panel(self._alert_win)
        self._alert_win = self._panel(blocks, accent="#E9A79F",
                                      seconds=int(self.cfg.get("alert_seconds", 45)))

    # ---------------- 耗尽预测（按消耗速度算还能用多久）
    def _update_burn(self, s):
        """每次拿到新数据就更新采样，并重算日均消耗 / 还能用多久。"""
        ok = s.get("ok_count") or 0
        if not ok:
            return
        total = float(s.get("total_remain_all") or 0)
        now = time.time()
        today = datetime.date.today().isoformat()
        cur = self._burn.get(today)
        if not isinstance(cur, dict):
            cur = {"first_ts": now, "first_total": total, "total": total, "ts": now}
            self._burn[today] = cur
        else:
            if not cur.get("first_ts"):
                cur["first_ts"] = now
                cur["first_total"] = total
            cur["total"] = total
            cur["ts"] = now
        if now - float(self._burn_saved or 0) > 120:      # 落盘节流，别每 400ms 写文件
            try:
                self._burn = record_balance(total, ok)
                self._burn_saved = now
            except Exception as ex:
                self._log("burn-save: %s: %s" % (type(ex).__name__, ex))
        self._burn_stats = burn_stats(self._burn, total,
                                      int(self.cfg.get("runout_window_days", 7) or 7), now)

    def _flush_burn(self):
        """退出前把当天的最后余额落盘。"""
        try:
            s = self.snap or {}
            if s.get("ok_count"):
                self._burn = record_balance(s.get("total_remain_all") or 0, s.get("ok_count"))
                self._burn_saved = time.time()
        except Exception:
            pass

    def _runout_days(self):
        try:
            return int(self.cfg.get("runout_days", 7) or 0)
        except (TypeError, ValueError):
            return 7

    def _runout_on(self):
        return self._runout_days() > 0

    def _runout_now(self):
        """返回 (days_left 或 None, stats)；days_left 只在低于阈值时返回。

        只有当天数据（today_only）时外推会明显高估消耗，容易误报，
        因此这种情况只报「几乎确定要耗尽」（≤1 天），其余仅在预览面板里展示。
        """
        st = self._burn_stats or {}
        if not self._runout_on() or not st.get("ok"):
            return None, st
        d = st.get("days_left")
        if d is None:
            return None, st
        if st.get("today_only") and d > 1.0:
            return None, st
        if d > self._runout_days():
            return None, st
        return d, st

    def _low_balance_accounts(self, s):
        limit = float(self.cfg.get("runout_min_balance", 100) or 0)
        if limit <= 0:
            return []
        out = [a for a in (s.get("accounts") or [])
               if a.get("ok") and float(a.get("total_remain") or 0) < limit]
        out.sort(key=lambda a: float(a.get("total_remain") or 0))
        return out

    def _check_runout_alert(self, s):
        """积分按当前速度快用完 / 某账号余额见底时弹提醒。

        去重：同一等级（1d/3d/Nd/低余额）**每天最多提醒一次**，
        跨等级（7天 → 3天 → 1天）会再提醒；低余额名单变化也算新的一批。
        """
        if not self._visible:
            return
        days, st = self._runout_now()
        low = self._low_balance_accounts(s)
        if days is None and not low:
            return
        if days is None:
            level = "low"
        elif days <= 1:
            level = "1d"
        elif days <= 3:
            level = "3d"
        else:
            level = "%dd" % self._runout_days()
        key = "runout::%s::%s" % (level, datetime.date.today().isoformat())
        if low:
            key += "::" + ",".join(sorted(a.get("label") or "-" for a in low))
        if key in self._alert_sent_this_run or alert_already_sent(key):
            return
        self._alert_sent_this_run.add(key)
        mark_alert_sent(key)
        self._show_runout_alert(st, days, low)

    def _show_runout_alert(self, st, days=None, low=None):
        s = self.snap or {}
        low = low or []
        if days is None:
            head = "⚠ 积分低余额提醒"
        else:
            head = "⚠ 积分即将耗尽提醒 · 约 %s 后用完" % span_text(days * 86400)
        blocks = [("alert", head)]
        if days is not None:
            if st.get("today_only"):
                src = "仅按今日 %s 推算，仅供参考" % span_text((st.get("span_days") or 0) * 86400)
            else:
                src = "近 %s 天采样 %d 个" % (fmt(st.get("span_days", 0)), st.get("samples", 0))
            blocks.append(("sub", "日均消耗 %s 分（%s）　当前总余额 %s 分"
                           % (fmt(st.get("rate", 0)), src, fmt(st.get("remain", 0)))))
            if st.get("runout_ts"):
                blocks.append(("sub", "按当前速度预计 %s 前后用完"
                               % datetime.datetime.fromtimestamp(st["runout_ts"]).strftime("%m-%d %H:%M")))
        blocks.append(("hr", None))
        if low:
            blocks.append(("sect", "低余额账号 · 余额 < %s 分（%d 个）"
                           % (fmt(self.cfg.get("runout_min_balance", 100)), len(low))))
            for a in low[:8]:
                blocks.append(("row", (a.get("label") or "-",
                                       "余额 %s 分" % fmt(a.get("total_remain", 0)), True)))
        elif days is not None:
            blocks.append(("sub", "各账号余额都还够用，注意上面这个整体趋势就好"))
        soon3 = float(s.get("soon3_all") or 0)
        if soon3 > 0:
            blocks.append(("sub", "另外 %s 分将在 3 天内过期，优先用它跑批量任务" % fmt(soon3)))
        blocks.append(("foot", "点击关闭 · %d 秒后自动隐藏 · 右键可调「耗尽提醒阈值」"
                       % int(self.cfg.get("alert_seconds", 45))))
        self._close_panel(self._toast)          # 与启动盘点面板互斥，避免叠在一起
        self._toast = None
        self._close_panel(self._alert_win)
        self._alert_win = self._panel(blocks, accent="#E9A79F",
                                      seconds=int(self.cfg.get("alert_seconds", 45)))

    # ---------------- 通用面板
    def _panel(self, blocks, accent=C_BORDER, seconds=20):
        fam = self.f_main.actual("family")
        t = tk.Toplevel(self.root)
        t.overrideredirect(True)
        t.configure(bg=C_BG)
        try:
            t.attributes("-topmost", True)
        except Exception:
            pass
        body = tk.Frame(t, bg=C_BG, highlightthickness=1, highlightbackground=accent,
                        highlightcolor=accent)
        body.pack(fill="both", expand=True)
        for kind, payload in blocks:
            if kind == "title":
                tk.Label(body, text=payload, bg=C_BG, fg=C_TEXT, anchor="w",
                         justify="left", font=(fam, 10, "bold")).pack(
                    fill="x", padx=14, pady=(6, 1))
            elif kind == "alert":
                tk.Label(body, text=payload, bg=C_BG, fg=C_DANGER, anchor="w",
                         justify="left", font=(fam, 11, "bold")).pack(
                    fill="x", padx=14, pady=(8, 1))
            elif kind == "sub":
                tk.Label(body, text=payload, bg=C_BG, fg=C_SUB, anchor="w",
                         justify="left", font=(fam, 9)).pack(fill="x", padx=14, pady=1)
            elif kind == "hr":
                tk.Frame(body, bg="#EDF1F5", height=1).pack(fill="x", padx=14, pady=6)
            elif kind == "sect":
                tk.Frame(body, bg="#EDF1F5", height=1).pack(fill="x", padx=14, pady=(8, 3))
                tk.Label(body, text=payload, bg=C_BG, fg=C_ACCENT, anchor="w",
                         justify="left", font=(fam, 9, "bold")).pack(
                    fill="x", padx=14, pady=(0, 2))
            elif kind == "fail":
                tk.Label(body, text=payload, bg=C_BG, fg=C_DANGER, anchor="w",
                         justify="left", font=(fam, 9)).pack(fill="x", padx=14, pady=1)
            elif kind == "row":
                head, right, hot = payload
                row = tk.Frame(body, bg=C_BG)
                row.pack(fill="x", padx=14, pady=1)
                tk.Label(row, text=head, bg=C_BG, fg=C_TEXT, anchor="w", width=21,
                         font=(fam, 9, "bold")).pack(side="left")
                tk.Label(row, text=right, bg=C_BG, fg=(C_DANGER if hot else C_SUB),
                         anchor="w", font=(fam, 9)).pack(side="left", padx=(8, 0))
            elif kind == "foot":
                tk.Label(body, text=payload, bg=C_BG, fg=C_SUB, anchor="w",
                         justify="left", font=(fam, 8)).pack(fill="x", padx=14, pady=(8, 8))

        t.update_idletasks()
        pw = max(486, t.winfo_reqwidth())
        ph = t.winfo_reqheight()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = max(0, min(self._anchor_right - pw, sw - pw - 8))
        y = max(0, self.pos[1] - ph - 10)
        if y < 8:
            y = min(sh - ph - 8, self.pos[1] + self.H + 10)
        t.geometry("%dx%d+%d+%d" % (pw, ph, x, y))

        def close(_e=None):
            self._close_panel(t)

        for w in [t, body] + list(body.winfo_children()):
            try:
                w.bind("<Button-1>", close)
            except Exception:
                pass
            for c in (w.winfo_children() if hasattr(w, "winfo_children") else []):
                try:
                    c.bind("<Button-1>", close)
                except Exception:
                    pass
        if seconds and seconds > 0:
            t.after(int(max(5, seconds) * 1000), close)
        return t

    @staticmethod
    def _log(msg):
        """轻量日志（pythonw 下没有控制台，写入文件便于排错）。"""
        try:
            os.makedirs(APP_DIR, exist_ok=True)
            with open(os.path.join(APP_DIR, "widget.log"), "a", encoding="utf-8") as f:
                f.write("%s  %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                      msg))
        except Exception:
            pass

    @staticmethod
    def _close_panel(win):
        if win is None:
            return
        try:
            win.destroy()
        except Exception:
            pass

    def _close_toast(self):
        t, self._toast = self._toast, None
        self._close_panel(t)

    # ---------------- 明细窗口
    def _detail(self):
        s = self.snap or {}
        w = tk.Toplevel(self.root)
        w.title("WorkBuddy 积分明细")
        w.configure(bg=C_BG)
        try:
            w.attributes("-topmost", True)
        except Exception:
            pass
        w.geometry("+%d+%d" % (self.root.winfo_x() - 240, max(60, self.root.winfo_y() - 420)))

        head = "WorkBuddy 积分明细 · %s" % s.get("ts", "-")
        tk.Label(w, text=head, bg=C_BG, fg=C_TEXT,
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", padx=14, pady=(12, 6))

        box = tk.Text(w, width=76, height=24, bg="#FBFCFD", fg=C_TEXT, relief="flat",
                      font=("Microsoft YaHei UI", 9), wrap="word")
        box.pack(padx=14, pady=(0, 12), fill="both", expand=True)

        lines = []
        lines.append("总览：%d 个账号 / 成功 %d / 全账号余额 %s / 今日消耗 %s / 本次运行消耗 %s"
                     % (s.get("account_count", 0), s.get("ok_count", 0),
                        fmt(s.get("total_remain_all", 0)),
                        fmt_used(s.get("used_today_all", 0)),
                        fmt_used(s.get("used_session_all", 0))))
        lines.append("近 3 天到期合计 %s 分；近 5 天到期合计 %s 分"
                     % (fmt(s.get("soon3_all", 0)), fmt(s.get("soon5_all", 0))))
        bs = self._burn_stats or {}
        lines.append("消耗预测：%s" % self._burn_line())
        if bs.get("spent") is not None:
            lines.append("  采样跨度 %s 天 / 样本 %d 个 / 期间净消耗 %s 分 / 当前总余额 %s 分"
                         % (fmt(bs.get("span_days", 0)), bs.get("samples", 0),
                            fmt(bs.get("spent", 0)), fmt(bs.get("remain", 0))))
        low = self._low_balance_accounts(s)
        if low:
            lines.append("低余额账号（< %s 分）：%s"
                         % (fmt(self.cfg.get("runout_min_balance", 100)),
                            "、".join("%s(%s)" % (a.get("label"), fmt(a.get("total_remain", 0)))
                                      for a in low)))
        lines.append("")
        lines.append("— 近 5 天到期批次 —")
        soon = s.get("soon_all") or []
        if not soon:
            lines.append("（无）")
        for b in soon:
            lines.append("  %s  %s  剩 %s 分  %s  [%s]"
                         % (b.get("end") or "-", b.get("account") or "-",
                            fmt(b.get("remain", 0)), b.get("package") or "",
                            countdown_text(b.get("ts"))))
        lines.append("")
        lines.append("— 各账号 —")
        for a in s.get("accounts") or []:
            if not a.get("ok"):
                lines.append("  %s：取数失败（%s）" % (a.get("label"), a.get("error")))
                continue
            tag = "当前在线" if a.get("is_current") else "历史登录"
            tok = a.get("token_exp_days")
            tok = ("token %s天" % tok) if tok is not None else "token 未知"
            lines.append("  %s（%s / %s / %s）余额 %s，有效批次 %d，最近到期 %s 剩 %s"
                         % (a.get("label"), a.get("phone") or "-", tag, tok,
                            fmt(a.get("total_remain", 0)), a.get("active_batch_count", 0),
                            a.get("next_end") or "-", fmt(a.get("next_remain") or 0)))
            if (a.get("token_exp_days") is not None and a.get("token_exp_days") < 7):
                lines.append("      ⚠ 该账号需在本机客户端重新登录一次以续期")
        lines.append("")
        lines.append("提示：3 日内到期的额度优先消耗；体验版 500 分按月清零；")
        lines.append("      批量任务放闲时（工作日 09:00-12:00 / 14:00-18:00 之外、夜间与周末）。")
        box.insert("1.0", "\n".join(lines))
        box.configure(state="disabled")
        tk.Button(w, text="关闭", command=w.destroy, relief="flat", bg="#EDF2F6",
                  fg=C_TEXT, padx=16, pady=4).pack(pady=(0, 12))

    # ---------------- 生命周期
    def quit(self):
        self.stop_flag.set()
        try:
            self._flush_burn()          # 退出前落盘当天最后的余额，
        except Exception:               # 保证下次启动能算出日均消耗
            pass
        try:
            os.remove(PID_PATH)
        except Exception:
            pass
        self.root.destroy()

    def run(self):
        os.makedirs(APP_DIR, exist_ok=True)
        try:
            pid_file_write(PID_PATH)      # 带创建时间，之后能被身份校验
        except Exception:
            with open(PID_PATH, "w", encoding="utf-8") as f:
                f.write(str(os.getpid()))
        widget_log("widget started (managed=%s, follow=%s, exit_with=%s, grace=%s)"
                   % (MANAGED, self.cfg.get("follow_workbuddy", True),
                      self.cfg.get("exit_with_workbuddy", True),
                      self.cfg.get("exit_grace_seconds", 8)))
        self.root.mainloop()


def _pid_alive(pid):
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259       # STILL_ACTIVE
    except Exception:
        return False


# ---------------------------------------------------------------- pid 文件：进程身份校验
# 只存 pid 是不够的：硬关机/重启时 pid 文件的清理逻辑根本来不及跑，
# 留在磁盘上的那个 pid 过一阵会被**别的程序复用**。那时"pid 存活"就成了假阳性，
# 后果是：新进程以为自己已经跑着了而拒绝启动（挂件再也不出现），
# 更糟的是 stop 会去杀一个毫不相干的进程。
# 所以写入时连**进程创建时间**一起存，读取时比对身份，不对就当过期文件删掉。
PY_IMAGE_NAMES = ("python.exe", "pythonw.exe")
CREATE_TIME_TOLERANCE = 10 ** 7          # FILETIME 单位是 100ns，1 秒 = 1e7


def _k32():
    import ctypes
    import ctypes.wintypes as wt
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = wt.HANDLE
    k.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k.CloseHandle.argtypes = [wt.HANDLE]
    k.QueryFullProcessImageNameW.restype = wt.BOOL
    k.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD,
                                             ctypes.c_wchar_p, ctypes.POINTER(wt.DWORD)]
    k.GetProcessTimes.restype = wt.BOOL
    k.GetProcessTimes.argtypes = [wt.HANDLE] + [ctypes.POINTER(wt.FILETIME)] * 4
    return k


def proc_identity(pid):
    """进程身份：{alive, image(完整路径), created(创建时间 FILETIME 整数)}。

    拿不到就当 None，绝不让调用方"因为拿不到就以为它死了"。
    """
    try:
        import ctypes
        import ctypes.wintypes as wt
        k = _k32()
        h = k.OpenProcess(0x1000, False, int(pid))
        if not h:
            return {"alive": False, "image": None, "created": None}
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = wt.DWORD(1024)
            img = buf.value if k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)) else None
            c, e, kt, u = (wt.FILETIME() for _ in range(4))
            created = None
            if k.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                 ctypes.byref(kt), ctypes.byref(u)):
                created = (c.dwHighDateTime << 32) | c.dwLowDateTime
            return {"alive": True, "image": img, "created": created}
        finally:
            k.CloseHandle(h)
    except Exception:
        return {"alive": False, "image": None, "created": None}


def pid_file_write(path):
    """写入自己的 pid + 创建时间（第二行）。"""
    info = proc_identity(os.getpid())
    text = str(os.getpid())
    if info.get("created"):
        text += "\n%d" % info["created"]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _quiet_remove(path):
    try:
        os.remove(path)
    except Exception:
        pass


def pid_file_read(path, stale_log=None):
    """读取 pid 文件并做身份校验；确属过期就删除文件并返回 None。

    判定过期的三种情况：
      * 进程已不存在；
      * 存在但映像不是 python/pythonw（= pid 被别的程序复用了）；
      * 存在、也是 python，但创建时间对不上（= 同名不同进程）。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            parts = f.read().split()
    except Exception:
        return None
    if not parts:
        _quiet_remove(path)
        return None
    try:
        pid = int(parts[0])
    except Exception:
        _quiet_remove(path)
        return None
    want_created = int(parts[1]) if len(parts) > 1 else 0

    if pid == os.getpid():          # 自己写的文件别把自己当过客
        return None

    info = proc_identity(pid)
    reason = None
    if not info["alive"]:
        reason = "进程已不存在"
    elif info["image"] and os.path.basename(info["image"]).lower() not in PY_IMAGE_NAMES:
        reason = "pid 已被 %s 复用" % os.path.basename(info["image"])
    elif want_created and info["created"] and info["created"] != want_created:
        reason = "同名进程但创建时间不符（pid 复用）"

    if reason is None:
        return pid
    if stale_log:
        stale_log("stale pid file %s: pid=%d, %s -> removed"
                  % (os.path.basename(path), pid, reason))
    _quiet_remove(path)
    return None


def already_running():
    return pid_file_read(PID_PATH, watcher_log)


def show_msg(text, title="WorkBuddy 积分挂件"):
    if tk is None:
        # 没有 tkinter 弹不出对话框，但绝不能把消息吞掉：至少进日志
        try:
            print(text)
        except Exception:
            pass
        watcher_log("message (no tkinter): %s" % text.replace("\n", " "))
        return
    try:
        root = tk.Tk()
        root.withdraw()
        from tkinter import messagebox
        messagebox.showinfo(title, text)
        root.destroy()
    except Exception:
        pass


def stop_running(quiet=False):
    # 必须用身份校验过的读取：pid 被复用的话，直接 os.kill 会杀掉毫不相干的进程
    pid = pid_file_read(PID_PATH, watcher_log)
    if pid:
        try:
            os.kill(pid, 9)
            if not quiet:
                print("已停止挂件 pid=%d" % pid)
        except Exception as e:
            if not quiet:
                print("停止失败：%s" % e)
    elif not quiet:
        print("挂件未在运行")
    _quiet_remove(PID_PATH)


# ---------------------------------------------------------------- 监听器：随 WorkBuddy 启停
def widget_pid():
    return pid_file_read(PID_PATH, watcher_log)


def watcher_pid():
    """监听器是否真在跑（含心跳新鲜度）。

    进程身份对了还不够：监听器可能卡死在某个调用里（进程还在、循环不动）。
    所以它每轮都刷新 watcher.heartbeat，这里只把"心跳文件存在且明显过旧"
    判为停摆，避免新监听器永远起不来。
    """
    pid = pid_file_read(WATCHER_PID_PATH, watcher_log)
    if not pid:
        return None
    try:
        if os.path.exists(WATCHER_HEARTBEAT):
            age = time.time() - os.path.getmtime(WATCHER_HEARTBEAT)
            if age > HEARTBEAT_STALE:
                watcher_log("watcher pid=%d heartbeat stale (%.0fs) -> treated as dead"
                            % (pid, age))
                _quiet_remove(WATCHER_PID_PATH)
                return None
    except Exception:
        pass
    return pid


def heartbeat():
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        with open(WATCHER_HEARTBEAT, "w", encoding="utf-8") as f:
            f.write("%d\n%.0f" % (os.getpid(), time.time()))
    except Exception:
        pass


def watcher_log(msg):
    """监听器没有控制台（pythonw），出问题只能靠日志。"""
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        if os.path.exists(WATCHER_LOG) and os.path.getsize(WATCHER_LOG) > 200 * 1024:
            os.remove(WATCHER_LOG)          # 别让它无限长大
        with open(WATCHER_LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def widget_log(msg):
    """挂件侧的日志。挂件同样是 pythonw 出没，出问题也只有日志能查。

    记的是"为什么进/出这个状态"，尤其是 exit_with_workbuddy 的判定过程——
    「关 WorkBuddy 后挂件没关掉」这类问题，只有这里能还原现场。
    """
    try:
        os.makedirs(APP_DIR, exist_ok=True)
        if os.path.exists(WIDGET_LOG) and os.path.getsize(WIDGET_LOG) > 200 * 1024:
            os.remove(WIDGET_LOG)
        with open(WIDGET_LOG, "a", encoding="utf-8") as f:
            f.write("[%s] pid=%d %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                          os.getpid(), msg))
    except Exception:
        pass


def _has_tkinter(exe):
    """这个解释器能不能 import tkinter（精简版 Python 常常没有）。"""
    if not exe or not os.path.exists(exe):
        return False
    try:
        r = subprocess.run([exe, "-c", "import tkinter"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, creationflags=0x08000000)   # CREATE_NO_WINDOW
        return r.returncode == 0
    except Exception:
        return False


def pythonw_path():
    """找一个**能跑本程序**的 pythonw.exe（无控制台）。

    不写死任何人的家目录，而且不只看"第一个找到的"：
    很多工具自带精简 Python（没有 tkinter）还排在 PATH 前面，
    直接用它拉起的挂件会立刻死掉、又没有任何报错。所以候选逐个试 import tkinter。
    全都不行时退回第一个候选（尽力而为，日志里会说明）。
    """
    cands = []
    env = os.environ.get("WB_PYTHONW")
    if env:
        cands.append(env)
    exe = sys.executable or ""
    if exe.lower().endswith("pythonw.exe"):
        cands.append(exe)
    cands.append(os.path.join(os.path.dirname(exe), "pythonw.exe"))
    found = shutil.which("pythonw")
    if found:
        cands.append(found)
    local = os.environ.get("LOCALAPPDATA", "")
    pfiles = os.environ.get("ProgramFiles", r"C:\Program Files")
    for root in (os.path.join(local, "Programs", "Python"), pfiles,
                 os.path.join(pfiles, "Python")):
        try:
            for name in sorted(os.listdir(root), reverse=True):
                cands.append(os.path.join(root, name, "pythonw.exe"))
        except Exception:
            pass

    seen, uniq = set(), []
    for p in cands:
        if p and p not in seen and os.path.exists(p):
            seen.add(p)
            uniq.append(p)
    for p in uniq:
        if _has_tkinter(p):
            return p
    return uniq[0] if uniq else exe


def launch_widget():
    """拉起挂件（--managed：WorkBuddy 退出后它会自己结束）。"""
    script = os.path.abspath(__file__)
    cmd = [pythonw_path(), script, "--managed"]
    flags = 0x00000008 | 0x08000000        # DETACHED_PROCESS | CREATE_NO_WINDOW
    try:
        p = subprocess.Popen(cmd, cwd=os.path.dirname(script),
                             creationflags=flags, close_fds=True)
        watcher_log("launched widget pid=%d" % p.pid)
        return p
    except Exception as e:
        watcher_log("launch failed: %r" % e)
        return None


def cfg_exit_with_workbuddy():
    """配置里是否要求「WorkBuddy 退出后挂件也结束」。默认开。"""
    try:
        cfg = load_json(CONFIG_PATH, {})
        return bool(cfg.get("exit_with_workbuddy", True))
    except Exception:
        return True


def watch_loop(poll=1.5, kill_grace=15.0, relaunch_cooldown=25.0, max_ticks=0):
    """监听 WorkBuddy 客户端的启停，按需拉起/结束挂件。全程无界面。

    * WorkBuddy 出现  → 挂件没在跑就拉起来（cooldown 秒内不重复尝试，避免失败时疯狂重启）
    * WorkBuddy 消失  → 宽限 kill_grace 秒后，结束**我们自己拉起的**那个挂件

    两条设计原则：
    1. **只收拾自己拉起的进程**：不按 pid 文件去杀别人——手动双击启动的挂件不该被牵连。
       正常情况下挂件自己会退（--managed），这里的 kill 只是兜底。
    2. **宽限必须长于挂件自己的退出宽限**（exit_grace_seconds，默认 8 秒），
       否则会在挂件落盘当天余额采样之前把它强杀掉，导致消耗统计缺一天。

    max_ticks > 0 时只跑这么多帧就返回，供自动化测试使用。
    """
    os.makedirs(APP_DIR, exist_ok=True)
    pid_file_write(WATCHER_PID_PATH)
    heartbeat()
    watcher_log("watcher started pid=%d" % os.getpid())

    child = None
    last_launch = 0.0
    gone_since = 0.0
    gone_handled = False
    ticks = 0
    launch_count = 0
    try:
        while True:
            try:
                running = workbuddy_running()
            except Exception:
                running = None
            now = time.time()
            heartbeat()                    # 每轮刷新，卡死时外面能看出来
            if running is True:
                gone_since = 0.0
                gone_handled = False
                alive = (child is not None and child.poll() is None) or bool(widget_pid())
                if not alive and (now - last_launch) > relaunch_cooldown:
                    # 反复拉起又立刻死掉（最常见原因：这个 Python 没装 tkinter），
                    # 会每 25 秒无声重试一次。日志里点名几次，别让人干等。
                    launch_count += 1
                    if launch_count in (3, 10, 30):
                        watcher_log(
                            "widget has been launched %d times but never stayed alive; "
                            "check that this Python has tkinter: %s"
                            % (launch_count, pythonw_path()))
                    child = launch_widget()
                    last_launch = now
            elif running is False:
                if gone_since == 0.0:
                    gone_since = now
                elif not gone_handled and now - gone_since > kill_grace:
                    # 一次「WorkBuddy 被关掉」只处理一次，不要每帧都去捅进程表
                    gone_handled = True
                    if child is not None and child.poll() is None:
                        if cfg_exit_with_workbuddy():
                            watcher_log("widget pid=%d did not exit in %.0fs, killing"
                                        % (child.pid, kill_grace))
                            try:
                                child.kill()
                            except Exception:
                                pass
                    child = None
            ticks += 1
            if max_ticks and ticks >= max_ticks:
                break
            time.sleep(poll)
    except KeyboardInterrupt:
        pass
    finally:
        _quiet_remove(WATCHER_PID_PATH)
        _quiet_remove(WATCHER_HEARTBEAT)
        watcher_log("watcher stopped")


def stop_watcher(quiet=False):
    """先停监听器，再停挂件——顺序不能反，否则监听器会把挂件又拉起来。"""
    pid = watcher_pid()
    if pid:
        try:
            os.kill(pid, 9)
            if not quiet:
                print("已停止监听器 pid=%d" % pid)
        except Exception as e:
            if not quiet:
                print("停止监听器失败：%s" % e)
    elif not quiet:
        print("监听器未在运行")
    _quiet_remove(WATCHER_PID_PATH)
    _quiet_remove(WATCHER_HEARTBEAT)


def stop_all():
    stop_watcher()
    stop_running()


# ---------------------------------------------------------------- 开机自启项（启动的是监听器）
def autostart_installed():
    return os.path.exists(STARTUP_VBS)


def install_autostart():
    """在「启动」文件夹放一个只跑监听器的启动器。

    注意：这个文件必须是**纯 ASCII**——它用 ascii 编码写盘（避免任何代码页问题），
    所有中文提示都由 Python 侧弹窗负责。改动时不要把中文写进 body。
    """
    script = os.path.abspath(__file__)
    body = (
        "' WorkBuddy Credit Widget - start with WorkBuddy (auto-generated)\r\n"
        "'\r\n"
        "' Runs the WATCHER only (headless). The widget itself never starts at logon:\r\n"
        "'   logon            -> nothing visible, the watcher just waits\r\n"
        "'   WorkBuddy starts -> the widget appears\r\n"
        "'   WorkBuddy exits  -> the widget process ends\r\n"
        "'\r\n"
        "' Delete this file (or run autostart_off.vbs) to disable.\r\n"
        "Option Explicit\r\n"
        "Dim fso, sh, pyw, script\r\n"
        "Set fso = CreateObject(\"Scripting.FileSystemObject\")\r\n"
        "Set sh = CreateObject(\"WScript.Shell\")\r\n"
        "script = \"%s\"\r\n"
        "pyw = \"%s\"\r\n"
        "If Not fso.FileExists(pyw) Then WScript.Quit 1\r\n"
        "WScript.Sleep 6000\r\n"
        "sh.Run \"\"\"\" & pyw & \"\"\"\" & \" \"\"\" & script & \"\"\" --watch\", 0, False\r\n"
        % (script, pythonw_path())
    )
    body.encode("ascii")            # 先验证，避免写出半截文件
    os.makedirs(STARTUP_DIR, exist_ok=True)
    tmp = STARTUP_VBS + ".tmp"
    with open(tmp, "w", encoding="ascii", newline="") as f:
        f.write(body)
    os.replace(tmp, STARTUP_VBS)    # 原子替换：写坏了也不会把旧文件清空
    return STARTUP_VBS


def remove_autostart():
    try:
        os.remove(STARTUP_VBS)
        return True
    except Exception:
        return False


def _say(text, title="WorkBuddy 积分挂件"):
    """有控制台就打印，没有就弹对话框。

    启动器现在是「.vbs 只负责把 .bat 藏起来跑」，于是本进程虽然带着一个
    被隐藏的控制台（sys.stdout 不为 None），用户其实什么也看不到。
    所以启动器会设 WB_FORCE_MSG=1，强制走对话框。
    """
    if sys.stdout is None or os.environ.get("WB_FORCE_MSG") == "1":
        show_msg(text, title)
    else:
        print(text)


def start_watcher_now():
    """立刻拉起监听器（不等下次登录）。"""
    if watcher_pid():
        return watcher_pid()
    script = os.path.abspath(__file__)
    try:
        p = subprocess.Popen([pythonw_path(), script, "--watch"],
                             cwd=os.path.dirname(script),
                             creationflags=0x00000008 | 0x08000000)
        return p.pid
    except Exception as e:
        watcher_log("start watcher failed: %r" % e)
        return None


def main():
    global MANAGED
    ap = argparse.ArgumentParser(
        prog="wb_credit_widget",
        description="WorkBuddy 积分挂件：多账号余额 / 今日消耗 / 到期与耗尽提醒。")
    ap.add_argument("--version", action="version",
                    version="%(prog)s " + __version__)
    ap.add_argument("--stop", action="store_true", help="只停止挂件")
    ap.add_argument("--stop-all", action="store_true", help="停止挂件 + 监听器")
    ap.add_argument("--watch", action="store_true",
                    help="监听模式：WorkBuddy 启动则拉起挂件，WorkBuddy 退出则结束挂件")
    ap.add_argument("--managed", action="store_true",
                    help="（内部使用）标记本进程由监听器拉起，WorkBuddy 退出后自行结束")
    ap.add_argument("--autostart", choices=["on", "off", "status"],
                    help="管理开机自启项（放的是监听器，不是挂件窗口）")
    ap.add_argument("--dump", action="store_true", help="命令行打印一次汇总后退出")
    a = ap.parse_args()

    if a.autostart == "on":
        try:
            p = install_autostart()
        except Exception as e:
            _say("安装自启项失败：%s" % e)
            return
        start_watcher_now()
        _say("已启用「随 WorkBuddy 自动启动」。\n\n"
             "启动项：%s\n\n"
             "说明：启动项里放的是一个「无界面监听器」，不是挂件窗口——\n"
             "· 开机后它只是安静等待，不显示任何窗口；\n"
             "· WorkBuddy 一启动 → 挂件自动出现；\n"
             "· WorkBuddy 一退出 → 挂件进程自动结束。\n\n"
             "彻底不要任何开机项：运行 autostart_off.vbs，或右键挂件 →\n"
             "「随 WorkBuddy 自动启动」→ 移除。" % p)
        return
    if a.autostart == "off":
        stop_watcher(quiet=True)
        ok = remove_autostart()
        _say("已移除「随 WorkBuddy 自动启动」，并停掉了监听器。\n\n"
             "以后登录电脑不会再有任何后台程序；\n"
             "想用挂件请手动双击 start_widget.vbs。"
             if ok else "本来就没有自启项（监听器已停掉）。")
        return
    if a.autostart == "status":
        w, g = watcher_pid(), widget_pid()
        _say("开机自启项：%s\n监听器：%s\n挂件进程：%s\nWorkBuddy：%s"
             % (STARTUP_VBS if autostart_installed() else "未安装",
                ("运行中 pid=%d" % w) if w else "未运行",
                ("运行中 pid=%d" % g) if g else "未运行",
                {True: "运行中", False: "未运行"}.get(workbuddy_running(), "无法检测")))
        return
    if a.stop_all:
        w, g = watcher_pid(), widget_pid()
        stop_all()
        _say("已停止：%s\n%s"
             % ("监听器 pid=%d" % w if w else "监听器（本来就没运行）",
                "挂件 pid=%d" % g if g else "挂件（本来就没运行）"))
        return
    if a.stop:
        g = widget_pid()
        stop_running()
        if g and sys.stdout is None:
            _say("已停止挂件 pid=%d" % g)
        return
    if a.dump:
        snap = collect({})
        burn = load_state().get("burn", {}) or {}
        out = {k: v for k, v in snap.items() if k != "accounts"}
        out["burn"] = burn_stats(burn, snap.get("total_remain_all") or 0,
                                 int(DEFAULT_CONFIG["runout_window_days"]))
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return
    if a.watch:
        old = watcher_pid()
        if old:
            # 登录时绝不能弹窗：用户看到莫名其妙的对话框只会以为程序坏了。
            # 真在跑就安静退出；是过期/卡死的 pid 文件已被 watcher_pid 清掉了。
            watcher_log("another watcher is alive (pid=%d), this one exits quietly" % old)
            if sys.stdout is not None:
                print("监听器已经在运行了（进程 %d）" % old)
            return
        watch_loop()
        return
    if tk is None:
        _say("这个 Python 没有 tkinter，挂件界面起不来。\n\n"
             "请用官网安装的 Python 运行（安装包里自带 tkinter），"
             "或设置 WB_PYTHONW 指向它。\n\n"
             "当前解释器：%s\n\n"
             "（命令行子命令 --stop / --dump / --autostart status 仍然可用。）"
             % sys.executable)
        return
    MANAGED = bool(a.managed)
    other = already_running()
    if other:
        if a.managed:
            # 监听器拉起的实例发现已经有挂件在跑（手动启动的 / 上一轮没退干净）
            # → 静默退出，别弹窗打扰
            watcher_log("widget already running (pid=%d), managed launch exits quietly" % other)
            if sys.stdout is not None:
                print("挂件已经在运行了（进程 %d）" % other)
            return
        show_msg("积分挂件已经在运行了（进程 %d）。\n\n"
                 "如果屏幕上没看到它，请先在右下角找白色小胶囊；\n"
                 "仍找不到就先运行 stop_widget.bat，再重新双击 start_widget.vbs。"
                 % other)
        return
    Widget().run()


if __name__ == "__main__":
    main()
