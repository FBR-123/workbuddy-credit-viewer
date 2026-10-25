# -*- coding: utf-8 -*-
"""拍摄挂件单击预览：设为 DPI 感知 -> 真实点击挂件 -> 截取「胶囊 + 预览面板」。

用法：
    python shot_widget.py out.png               # 点一下挂件，拍「胶囊 + 单击预览面板」
    python shot_widget.py out.png --no-click    # 不点击，拍当前已显示的窗口（如自动弹出的提醒面板）

关键点（踩坑记录）：
  * 截图进程必须先 SetProcessDPIAware()，否则 GetWindowRect 返回逻辑坐标，
    而桌面 DC BitBlt 的源坐标按物理像素解释，两者差 scale 倍 → 截到错误区域。
  * 所有 GDI/user32 函数必须显式声明 argtypes/restype，否则 64 位句柄被截断，
    BitBlt 静默失败并返回未初始化内存（表现为错乱的花屏）。
  * 单击绑定在 Tk 的 canvas 子窗口上，PostMessage 到顶层 HWND 不触发事件，
    需用 SendInput 真实点击（点完还原光标）。
"""
import ctypes
import ctypes.wintypes as w
import struct
import time
import zlib
import sys
import os

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)      # PER_MONITOR_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

TH32CS_SNAPPROCESS = 0x2
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
MK_LBUTTON = 0x0001
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
WIDGET_TITLE = 'WorkBuddy 积分挂件'


class PE32(ctypes.Structure):
    _fields_ = [('dwSize', w.DWORD), ('cntUsage', w.DWORD), ('th32ProcessID', w.DWORD),
                ('th32DefaultHeapID', ctypes.POINTER(ctypes.c_ulong)), ('th32ModuleID', w.DWORD),
                ('cntThreads', w.DWORD), ('th32ParentProcessID', w.DWORD),
                ('pcPriClassBase', ctypes.c_long), ('dwFlags', w.DWORD),
                ('szExeFile', ctypes.c_char * 260)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', w.DWORD), ('biWidth', ctypes.c_long), ('biHeight', ctypes.c_long),
                ('biPlanes', w.WORD), ('biBitCount', w.WORD), ('biCompression', w.DWORD),
                ('biSizeImage', w.DWORD), ('biXPelsPerMeter', ctypes.c_long),
                ('biYPelsPerMeter', ctypes.c_long), ('biClrUsed', w.DWORD),
                ('biClrImportant', w.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [('bmiHeader', BITMAPINFOHEADER), ('bmiColors', w.DWORD * 3)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', ctypes.c_long), ('dy', ctypes.c_long), ('mouseData', w.DWORD),
                ('dwFlags', w.DWORD), ('time', w.DWORD), ('dwExtraInfo', ctypes.POINTER(ctypes.c_ulong))]


class INPUT(ctypes.Structure):
    _fields_ = [('type', w.DWORD), ('mi', MOUSEINPUT)]


user32.GetDC.restype = w.HDC
user32.GetDC.argtypes = [w.HWND]
user32.ReleaseDC.argtypes = [w.HWND, w.HDC]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
user32.GetClientRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
user32.GetWindowTextW.argtypes = [w.HWND, ctypes.c_wchar_p, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
user32.IsWindowVisible.argtypes = [w.HWND]
user32.SendInput.argtypes = [w.UINT, ctypes.c_void_p, ctypes.c_int]
user32.GetCursorPos.argtypes = [ctypes.POINTER(w.POINT)]
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleDC.restype = w.HDC
gdi32.CreateCompatibleDC.argtypes = [w.HDC]
gdi32.CreateDIBSection.restype = ctypes.c_void_p
gdi32.CreateDIBSection.argtypes = [w.HDC, ctypes.POINTER(BITMAPINFO), w.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), w.HANDLE, w.DWORD]
gdi32.SelectObject.restype = w.HGDIOBJ
gdi32.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
gdi32.BitBlt.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         w.HDC, ctypes.c_int, ctypes.c_int, w.DWORD]
gdi32.DeleteObject.argtypes = [w.HGDIOBJ]
gdi32.DeleteDC.argtypes = [w.HDC]


def pythonw_pids():
    s = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    e = PE32()
    e.dwSize = ctypes.sizeof(PE32)
    pids = []
    ok = kernel32.Process32First(s, ctypes.byref(e))
    while ok:
        if e.szExeFile.decode('mbcs', 'ignore').lower() in ('pythonw.exe', 'python.exe'):
            pids.append(e.th32ProcessID)
        ok = kernel32.Process32Next(s, ctypes.byref(e))
    kernel32.CloseHandle(s)
    return pids


def enum_windows(pids):
    out = []
    Proc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)

    def cb(hwnd, lparam):
        pid = w.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            r = w.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            buf = ctypes.create_unicode_buffer(512)
            user32.GetWindowTextW(hwnd, buf, 512)
            out.append({'hwnd': hwnd, 'pid': pid.value, 'title': buf.value,
                        'visible': bool(user32.IsWindowVisible(hwnd)),
                        'rect': (r.left, r.top, r.right, r.bottom)})
        return True

    user32.EnumWindows(Proc(cb), 0)
    return out


def widget_windows(pids):
    return [x for x in enum_windows(pids)
            if x['visible'] and x['title'] == WIDGET_TITLE
            and (x['rect'][2] - x['rect'][0]) > 40]


def send_real_click(px, py):
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    pt = w.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    absx = int(px * 65535 / (sw - 1))
    absy = int(py * 65535 / (sh - 1))
    ins = (INPUT * 3)()
    for i, fl in enumerate((MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE,
                            MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP)):
        ins[i].type = INPUT_MOUSE
        ins[i].mi = MOUSEINPUT(absx, absy, 0, fl, 0, None)
    user32.SendInput(3, ctypes.byref(ins), ctypes.sizeof(INPUT))
    return (pt.x, pt.y)


def grab(x, y, cx, cy):
    hdc = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(hdc)
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = cx
    bi.bmiHeader.biHeight = -cy
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(mem, ctypes.byref(bi), DIB_RGB_COLORS,
                                  ctypes.byref(bits), None, 0)
    if not hbmp:
        return None
    old = gdi32.SelectObject(mem, w.HGDIOBJ(hbmp))
    gdi32.BitBlt(mem, 0, 0, cx, cy, hdc, x, y, SRCCOPY)
    gdi32.GdiFlush()
    data = ctypes.string_at(bits, cx * cy * 4)
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(w.HGDIOBJ(hbmp))
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, hdc)
    return data


def write_png(path, w_, h_, bgra):
    stride = w_ * 4
    raw = bytearray()
    for y in range(h_):
        row = bgra[y * stride:(y + 1) * stride]
        raw.append(0)
        for i in range(0, len(row), 4):
            raw.append(row[i + 2])
            raw.append(row[i + 1])
            raw.append(row[i])

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data +
                struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('>IIBBBBB', w_, h_, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', zlib.compress(bytes(raw), 6))
    png += chunk(b'IEND', b'')
    open(path, 'wb').write(png)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    no_click = "--no-click" in sys.argv          # 只拍摄当前已显示的窗口（不模拟点击）
    out = args[0] if args else os.path.join(os.environ['TEMP'], 'wb_click.png')
    sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
    print('DPI aware screen = %dx%d' % (sw, sh))

    pids = set(pythonw_pids())
    wins = widget_windows(pids)
    if not wins:
        print('NO_WIDGET')
        return 3
    capsule = min(wins, key=lambda x: (x['rect'][3] - x['rect'][1]))
    before = {x['hwnd'] for x in wins}
    r = capsule['rect']
    print('capsule rect=%s size=%dx%d' % (r, r[2] - r[0], r[3] - r[1]))

    old = None
    if no_click:
        time.sleep(1.0)
        # 不点击时，目标就是「当前所有已显示的面板」（而不是新出现的窗口）
        new = [x for x in widget_windows(pids)
               if x['hwnd'] != capsule['hwnd'] and (x['rect'][2] - x['rect'][0]) > 200]
    else:
        old = send_real_click((r[0] + r[2]) // 2, (r[1] + r[3]) // 2)
        time.sleep(2.0)
        new = [x for x in widget_windows(pids)
               if x['hwnd'] not in before and (x['rect'][2] - x['rect'][0]) > 200]
    if new:
        panel = max(new, key=lambda x: (x['rect'][2] - x['rect'][0]) * (x['rect'][3] - x['rect'][1]))
        pr = panel['rect']
        print('panel rect=%s size=%dx%d' % (pr, pr[2] - pr[0], pr[3] - pr[1]))
        x0 = max(0, min(r[0], pr[0]) - 12)
        y0 = max(0, min(r[1], pr[1]) - 12)
        x1 = min(sw, max(r[2], pr[2]) + 12)
        y1 = min(sh, max(r[3], pr[3]) + 12)
    else:
        print('WARN no panel (capture capsule only)')
        x0, y0 = max(0, r[0] - 12), max(0, r[1] - 12)
        x1, y1 = min(sw, r[2] + 12), min(sh, r[3] + 12)

    if old and old[0] > 0 and old[1] > 0:
        user32.SetCursorPos(old[0], old[1])

    full = grab(0, 0, sw, sh)
    if not full:
        print('GRAB_FAIL')
        return 5
    cw, ch = x1 - x0, y1 - y0
    stride = sw * 4
    buf = bytearray()
    for yy in range(y0, y1):
        s = yy * stride + x0 * 4
        buf += full[s:s + cw * 4]
    write_png(out, cw, ch, bytes(buf))
    print('SAVED %s  region=(%d,%d,%d,%d)  size=%dx%d' % (out, x0, y0, x1, y1, cw, ch))
    return 0


if __name__ == '__main__':
    sys.exit(main())
