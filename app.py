"""
Input_Catcher_3000 -- customizable always-on-top key/mouse overlay
(public build).

Free download, no install, closed source. Built from Alfred's
personal InputOverlayCustom overlay
(D:\\OBSStudio\\InputOverlayCustom\\overlay.py) with a settings screen
added so anyone can pick their own box color, text color, and font
before it starts. See SYSTEM_keycast-product on Google Drive
(AlfredSystems project) for the concept doc.

On launch: shows a small settings window (box color, text color, font),
pre-filled with the last-saved choices if any. Clicking "Start Overlay"
saves those choices next to the exe and starts the overlay immediately.
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time
import tkinter as tk
from tkinter import colorchooser, filedialog

from PIL import Image, ImageDraw, ImageFont, ImageChops, ImageTk

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

HANDLE = ctypes.c_void_p
HDC = HANDLE
HBITMAP = HANDLE
HGDIOBJ = HANDLE
HHOOK = HANDLE
HMODULE = HANDLE


# ---------------------------------------------------------------- paths --
def app_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(app_dir(), "input_catcher_3000_config.json")

DEFAULT_CONFIG = {
    "box_color": [30, 30, 30, 150],
    "text_color": [255, 255, 255],
    "font_path": "",
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for k in DEFAULT_CONFIG:
            if k in saved:
                cfg[k] = saved[k]
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------------- config --
PANEL_H = 260
LEFT_MARGIN = 40
BOTTOM_MARGIN = 90
ROW_H = 30
FOCAL_Y = PANEL_H - 60
MAX_ROWS = PANEL_H // ROW_H
HOLD_THRESHOLD = 0.30
FADE_DURATION = 1.1
FONT_SIZE = 22
DEFAULT_FONT_PATHS = [r"C:\Windows\Fonts\consolab.ttf", r"C:\Windows\Fonts\arialbd.ttf"]
TICK_MS = 33
HOLD_RGB = (255, 205, 90)
TEXT_PAD_X = 18
BOX_RADIUS = 14
WIDEST_LABEL = "Backspace(hold)"

# These four are filled in from the user's saved settings right before
# main() is called -- see the __main__ block at the bottom of this file.
FONT = None
PANEL_W = 220
BOX_FILL = (30, 30, 30, 150)
FG_RGB = (255, 255, 255)

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
WM_MOUSEWHEEL = 0x020A
LLKHF_EXTENDED = 0x01
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_TIMER = 0x0113

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SM_CXSCREEN = 0
SM_CYSCREEN = 1
ULW_ALPHA = 0x02
AC_SRC_OVER = 0
AC_SRC_ALPHA = 1
SW_SHOWNOACTIVATE = 4
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010


# ------------------------------------------------------------- structures --
class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wt.DWORD), ("scanCode", wt.DWORD), ("flags", wt.DWORD),
        ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_void_p),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", wt.POINT), ("mouseData", wt.DWORD), ("flags", wt.DWORD),
        ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_void_p),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wt.DWORD),
        ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_int, wt.WPARAM, wt.LPARAM)
WNDPROCTYPE = ctypes.WINFUNCTYPE(ctypes.c_long, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROCTYPE),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", HANDLE),
        ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
        ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON),
    ]


# ---- fix 64-bit handle truncation: ctypes defaults restype to 32-bit int --
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [
    wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wt.HWND, HANDLE, HMODULE, HANDLE,
]
user32.GetDC.restype = HDC
user32.GetDC.argtypes = [wt.HWND]
user32.SetWindowsHookExW.restype = HHOOK
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, HMODULE, wt.DWORD]
gdi32.CreateCompatibleDC.restype = HDC
gdi32.CreateCompatibleDC.argtypes = [HDC]
gdi32.CreateDIBSection.restype = HBITMAP
gdi32.CreateDIBSection.argtypes = [
    HDC, ctypes.POINTER(BITMAPINFO), wt.UINT,
    ctypes.POINTER(ctypes.c_void_p), HANDLE, wt.DWORD,
]
gdi32.SelectObject.restype = HGDIOBJ
gdi32.SelectObject.argtypes = [HDC, HGDIOBJ]
kernel32.GetModuleHandleW.restype = HMODULE
user32.DefWindowProcW.restype = ctypes.c_long
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.CallNextHookEx.restype = ctypes.c_long
user32.CallNextHookEx.argtypes = [HHOOK, ctypes.c_int, wt.WPARAM, wt.LPARAM]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.SetWindowPos.argtypes = [
    wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wt.UINT,
]
user32.SetWindowPos.restype = wt.BOOL
HWND_TOPMOST = wt.HWND(-1)
user32.UpdateLayeredWindow.argtypes = [
    wt.HWND, HDC, ctypes.POINTER(wt.POINT), ctypes.POINTER(wt.SIZE),
    HDC, ctypes.POINTER(wt.POINT), wt.DWORD,
    ctypes.POINTER(BLENDFUNCTION), wt.DWORD,
]
user32.UpdateLayeredWindow.restype = wt.BOOL
# Used by close_existing_overlay() to find and close a leftover overlay
# window from a previous, not-cleanly-closed run.
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.PostMessageW.restype = wt.BOOL
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
# Found live: hook handles (unlike this app's small HWNDs) can exceed the
# 32-bit range ctypes defaults to, so UnhookWindowsHookEx needs the same
# explicit-argtypes fix -- it only ever crashed on the graceful-shutdown
# path (WM_DESTROY -> here), which nothing had exercised until
# close_existing_overlay() started actually closing a prior instance.
user32.UnhookWindowsHookEx.restype = wt.BOOL
user32.UnhookWindowsHookEx.argtypes = [HHOOK]
# Used by close_existing_overlay() to walk every top-level window on the
# desktop (not just look up one class name) so it can find a stray copy
# of THIS app regardless of which screen it's stuck on -- a running
# overlay (by window class) or an unstarted settings dialog left open
# from a previous launch (by window title, since every settings window
# shares the same title and class -- "TkTopLevel" is tkinter's generic
# toplevel class, used by every Tk app, not just this one).
EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
user32.EnumWindows.restype = wt.BOOL
user32.EnumWindows.argtypes = [EnumWindowsProc, wt.LPARAM]
user32.GetWindowThreadProcessId.restype = wt.DWORD
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextLengthW.argtypes = [wt.HWND]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
kernel32.GetCurrentProcessId.restype = wt.DWORD


# ------------------------------------------------------------- key names --
_SPECIAL_KEYS = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x1B: "Esc", 0x20: "Space",
    0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down", 0x2E: "Del",
    0x2D: "Ins", 0x24: "Home", 0x23: "End", 0x21: "PgUp", 0x22: "PgDn",
    0x14: "CapsLock", 0xA0: "LShift", 0xA1: "RShift", 0xA2: "LCtrl",
    0xA3: "RCtrl", 0xA4: "LAlt", 0xA5: "RAlt", 0x2C: "PrtScn",
}


def vk_to_name(vk_code, scan_code, extended):
    if vk_code in _SPECIAL_KEYS:
        return _SPECIAL_KEYS[vk_code]
    if 0x70 <= vk_code <= 0x87:
        return "F%d" % (vk_code - 0x6F)
    lparam = (scan_code << 16) | ((1 if extended else 0) << 24)
    buf = ctypes.create_unicode_buffer(32)
    n = user32.GetKeyNameTextW(lparam, buf, 32)
    return buf.value if n > 0 else ("VK_%02X" % vk_code)


# --------------------------------------------------------------- log state --
held = {}     # key_id -> entry, currently down
entries = []  # all entries still visible (held + fading out)


def press(key_id, label):
    if key_id in held:
        return
    e = {"label": label, "born": time.time(), "released": None}
    held[key_id] = e
    entries.append(e)


def release(key_id):
    e = held.pop(key_id, None)
    if e is not None:
        e["released"] = time.time()


def tap(label):
    now = time.time()
    entries.append({"label": label, "born": now, "released": now})


# ------------------------------------------------------------------ hooks --
def kb_hook(nCode, wParam, lParam):
    if nCode >= 0:
        info = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        extended = bool(info.flags & LLKHF_EXTENDED)
        name = vk_to_name(info.vkCode, info.scanCode, extended)
        if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            press(("kb", info.vkCode), name)
        elif wParam in (WM_KEYUP, WM_SYSKEYUP):
            release(("kb", info.vkCode))
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


def mouse_hook(nCode, wParam, lParam):
    if nCode >= 0:
        info = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        if wParam == WM_LBUTTONDOWN:
            press(("m", "L"), "LMB")
        elif wParam == WM_LBUTTONUP:
            release(("m", "L"))
        elif wParam == WM_RBUTTONDOWN:
            press(("m", "R"), "RMB")
        elif wParam == WM_RBUTTONUP:
            release(("m", "R"))
        elif wParam == WM_MBUTTONDOWN:
            press(("m", "M"), "MMB")
        elif wParam == WM_MBUTTONUP:
            release(("m", "M"))
        elif wParam == WM_MOUSEWHEEL:
            delta = ctypes.c_short(info.mouseData >> 16).value
            tap("Scroll Up" if delta > 0 else "Scroll Down")
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


KB_PROC = HOOKPROC(kb_hook)
MOUSE_PROC = HOOKPROC(mouse_hook)


# ------------------------------------------------------------- rendering --
def load_font(custom_path=""):
    if custom_path:
        try:
            return ImageFont.truetype(custom_path, FONT_SIZE)
        except OSError:
            pass  # fall through to the built-in defaults below
    for p in DEFAULT_FONT_PATHS:
        try:
            return ImageFont.truetype(p, FONT_SIZE)
        except OSError:
            continue
    return ImageFont.load_default()


def compute_panel_width():
    """Auto-fit width: measure the worst-case label at the current font
    so the panel is exactly as wide as it needs to be, whatever font
    the user picked."""
    dummy = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(dummy)
    bbox = d.textbbox((0, 0), WIDEST_LABEL, font=FONT)
    text_w = bbox[2] - bbox[0]
    return text_w + TEXT_PAD_X * 2


def draw_line(draw, text, y, rgba):
    draw.text((TEXT_PAD_X, y), text, font=FONT, fill=rgba)


def render_frame():
    global entries
    now = time.time()
    entries = [e for e in entries
               if e["released"] is None or (now - e["released"]) < FADE_DURATION]
    ordered = sorted(entries, key=lambda e: e["born"], reverse=True)[:MAX_ROWS]

    img = Image.new("RGBA", (PANEL_W, PANEL_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        (0, 0, PANEL_W - 1, PANEL_H - 1), radius=BOX_RADIUS, fill=BOX_FILL)
    for i, e in enumerate(ordered):
        y = FOCAL_Y - i * ROW_H
        if y < -ROW_H:
            continue
        if e["released"] is None:
            is_hold = (now - e["born"]) >= HOLD_THRESHOLD
            text = "%s(hold)" % e["label"] if is_hold else e["label"]
            rgb = HOLD_RGB if is_hold else FG_RGB
            alpha = 255
        else:
            age = now - e["released"]
            alpha = max(0, int(255 * (1 - age / FADE_DURATION)))
            text = e["label"]
            rgb = FG_RGB
        if alpha > 0:
            draw_line(draw, text, y, rgb + (alpha,))
    return img


def render_preview_frame():
    """Static demo frame for the settings-screen preview -- shows the
    widest label so the user sees exactly what the fitted panel looks
    like, with no dependency on live key state."""
    img = Image.new("RGBA", (PANEL_W, PANEL_H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        (0, 0, PANEL_W - 1, PANEL_H - 1), radius=BOX_RADIUS, fill=BOX_FILL)
    draw_line(draw, WIDEST_LABEL, FOCAL_Y, FG_RGB + (255,))
    draw_line(draw, "Space", FOCAL_Y - ROW_H, FG_RGB + (255,))
    return img


def to_bgra_premultiplied(img):
    r, g, b, a = img.split()
    pr = ImageChops.multiply(r, a)
    pg = ImageChops.multiply(g, a)
    pb = ImageChops.multiply(b, a)
    return Image.merge("RGBA", (pb, pg, pr, a)).tobytes()


# ------------------------------------------------------------- Win32 glue --
def make_dib(hdc):
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = PANEL_W
    bmi.bmiHeader.biHeight = -PANEL_H  # top-down
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0  # BI_RGB
    bits_ptr = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(hdc, ctypes.byref(bmi), 0,
                                   ctypes.byref(bits_ptr), None, 0)
    return hbmp, bits_ptr


def wnd_proc(hwnd, msg, wparam, lparam):
    if msg == WM_TIMER:
        update_overlay(hwnd)
        return 0
    if msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


WND_PROC_CB = WNDPROCTYPE(wnd_proc)

screen_dc = None
mem_dc = None
dib_bits = None
dib_bmp = None
win_x = 0
win_y = 0


def update_overlay(hwnd):
    # Keep re-asserting topmost -- other apps can steal the topmost
    # z-order slot when they activate.
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
    frame = render_frame()
    data = to_bgra_premultiplied(frame)
    ctypes.memmove(dib_bits, data, len(data))

    size = wt.SIZE(PANEL_W, PANEL_H)
    src_pt = wt.POINT(0, 0)
    dst_pt = wt.POINT(win_x, win_y)
    blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
    user32.UpdateLayeredWindow(
        hwnd, screen_dc, ctypes.byref(dst_pt), ctypes.byref(size),
        mem_dc, ctypes.byref(src_pt), 0, ctypes.byref(blend), ULW_ALPHA,
    )


SETTINGS_TITLE = "Input_Catcher_3000 Settings"
OVERLAY_CLASS = "Input_Catcher_3000_OverlayWnd"


def _find_other_instance_windows():
    """Walk every top-level window on the desktop and collect the ones
    that belong to a DIFFERENT process and look like this app: either
    the overlay window (matched by class) or the settings dialog
    (matched by title, since tkinter's "TkTopLevel" class is shared by
    every Tk app on the machine, not unique to this one).

    Matching by PID instead of relying on FindWindowW's single result is
    what makes this catch a stray instance in EITHER state -- an overlay
    that's actually running, or a second copy still sitting unstarted at
    its own settings screen (which FindWindowW-by-class alone can never
    see, since that window is never given the overlay's class name)."""
    my_pid = kernel32.GetCurrentProcessId()
    found = []

    def callback(hwnd, lparam):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == my_pid or pid.value == 0:
            return True
        cls_buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls_buf, 256)
        is_match = cls_buf.value == OVERLAY_CLASS
        if not is_match:
            title_len = user32.GetWindowTextLengthW(hwnd)
            if title_len > 0:
                title_buf = ctypes.create_unicode_buffer(title_len + 1)
                user32.GetWindowTextW(hwnd, title_buf, title_len + 1)
                is_match = title_buf.value == SETTINGS_TITLE
        if is_match:
            found.append(hwnd)
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return found


def close_existing_overlay():
    """If a previous Input_Catcher_3000 instance is still around -- a
    running overlay, or a second copy left sitting at its own settings
    screen because it was launched but never started or closed -- ask
    every window it has open to close before we open a new one.
    Otherwise the old window keeps running underneath the new one: same
    screen position, invisible and click-through, with no title bar or
    taskbar entry, so it's very hard to find and close by hand afterwards
    (Task Manager shows it only as a same-named, indistinguishable
    Input_Catcher_3000.exe process).

    Returns the number of distinct windows it asked to close (0 if none
    were found) -- used by the settings screen's "Close All Instances"
    button to report back what it did.
    """
    seen = set()
    deadline = time.time() + 4.0  # ceiling; never hang the new launch on this
    while time.time() < deadline:
        windows = _find_other_instance_windows()
        if not windows:
            break
        for hwnd in windows:
            if hwnd not in seen:
                seen.add(hwnd)
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        time.sleep(0.2)
    return len(seen)


def main():
    global screen_dc, mem_dc, dib_bits, dib_bmp, win_x, win_y

    close_existing_overlay()

    h_instance = kernel32.GetModuleHandleW(None)
    class_name = "Input_Catcher_3000_OverlayWnd"

    wc = WNDCLASSEXW()
    wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
    wc.lpfnWndProc = WND_PROC_CB
    wc.hInstance = h_instance
    wc.lpszClassName = class_name
    user32.RegisterClassExW(ctypes.byref(wc))

    screen_w = user32.GetSystemMetrics(SM_CXSCREEN)
    screen_h = user32.GetSystemMetrics(SM_CYSCREEN)
    win_x = LEFT_MARGIN
    win_y = screen_h - PANEL_H - BOTTOM_MARGIN

    ex_style = (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOPMOST |
                WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE)
    hwnd = user32.CreateWindowExW(
        ex_style, class_name, "Input_Catcher_3000", WS_POPUP,
        win_x, win_y, PANEL_W, PANEL_H, None, None, h_instance, None,
    )

    screen_dc = user32.GetDC(None)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    dib_bmp, dib_bits = make_dib(screen_dc)
    gdi32.SelectObject(mem_dc, dib_bmp)

    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    user32.SetTimer(hwnd, 1, TICK_MS, None)

    hook_kb = user32.SetWindowsHookExW(WH_KEYBOARD_LL, KB_PROC, h_instance, 0)
    hook_mouse = user32.SetWindowsHookExW(WH_MOUSE_LL, MOUSE_PROC, h_instance, 0)

    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

    user32.UnhookWindowsHookEx(hook_kb)
    user32.UnhookWindowsHookEx(hook_mouse)


# ------------------------------------------------------------ settings UI --
def _rgb_to_hex(rgb):
    return "#%02x%02x%02x" % tuple(int(c) for c in rgb)


def build_settings_gui(cfg):
    """Shows the Input_Catcher_3000 settings window. Blocks until the user
    clicks Start Overlay or closes the window. Returns the edited cfg
    dict, or None if the window was closed without starting."""
    global FONT, PANEL_W, BOX_FILL, FG_RGB

    root = tk.Tk()
    root.title("Input_Catcher_3000 Settings")
    root.resizable(False, False)
    root.configure(bg="#1e1e1e")

    box_rgb = list(cfg["box_color"][:3])
    box_alpha = cfg["box_color"][3] if len(cfg["box_color"]) > 3 else 150
    text_rgb = list(cfg["text_color"])
    state = {"font_path": cfg.get("font_path", ""), "started": False}

    def refresh_preview():
        global FONT, PANEL_W, BOX_FILL, FG_RGB
        FONT = load_font(state["font_path"])
        BOX_FILL = tuple(box_rgb) + (box_alpha,)
        FG_RGB = tuple(text_rgb)
        PANEL_W = compute_panel_width()
        panel = render_preview_frame()
        backdrop = Image.new("RGBA", panel.size, (60, 60, 60, 255))
        composed = Image.alpha_composite(backdrop, panel).convert("RGB")
        tk_img = ImageTk.PhotoImage(composed)
        preview_label.image = tk_img  # keep a reference, tkinter needs it
        preview_label.configure(image=tk_img)

    def pick_box_color():
        nonlocal box_rgb
        rgb, _ = colorchooser.askcolor(color=_rgb_to_hex(box_rgb),
                                        title="Choose box color")
        if rgb:
            box_rgb = [int(c) for c in rgb]
            refresh_preview()

    def pick_text_color():
        nonlocal text_rgb
        rgb, _ = colorchooser.askcolor(color=_rgb_to_hex(text_rgb),
                                        title="Choose text color")
        if rgb:
            text_rgb = [int(c) for c in rgb]
            refresh_preview()

    def pick_font():
        path = filedialog.askopenfilename(
            title="Choose a font",
            filetypes=[("Font files", "*.ttf *.otf"), ("All files", "*.*")],
        )
        if path:
            state["font_path"] = path
            font_label.configure(text=os.path.basename(path))
            refresh_preview()

    def reset_font():
        state["font_path"] = ""
        font_label.configure(text="Default (Consolas Bold)")
        refresh_preview()

    def on_alpha_change(val):
        nonlocal box_alpha
        box_alpha = int(float(val))
        refresh_preview()

    def start_clicked():
        state["started"] = True
        root.destroy()

    def close_all_clicked():
        n = close_existing_overlay()
        if n:
            close_status_label.configure(
                text="Closed %d running instance%s." % (n, "" if n == 1 else "s"))
        else:
            close_status_label.configure(text="No running instance found.")

    LBL = {"bg": "#1e1e1e", "fg": "#e8e8e8", "font": ("Segoe UI", 9)}
    top = tk.Frame(root, padx=18, pady=16, bg="#1e1e1e")
    top.pack()

    preview_label = tk.Label(top, bg="#1e1e1e")

    tk.Label(top, text="Input_Catcher_3000", font=("Segoe UI", 16, "bold"),
             bg="#1e1e1e", fg="#ffffff").grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 2))
    tk.Label(top, text="A key & mouse overlay for streaming.",
             **LBL).grid(row=1, column=0, columnspan=2, sticky="w",
                          pady=(0, 14))

    tk.Label(top, text="Box color:", **LBL).grid(row=2, column=0, sticky="w")
    tk.Button(top, text="Choose...", command=pick_box_color).grid(
        row=2, column=1, sticky="w", pady=2)

    tk.Label(top, text="Box opacity:", **LBL).grid(row=3, column=0, sticky="w")
    scale = tk.Scale(top, from_=0, to=255, orient="horizontal",
                      command=on_alpha_change, length=160,
                      bg="#1e1e1e", fg="#e8e8e8", highlightthickness=0,
                      troughcolor="#3a3a3a")
    scale.set(box_alpha)
    scale.grid(row=3, column=1, sticky="w", pady=2)

    tk.Label(top, text="Text color:", **LBL).grid(row=4, column=0, sticky="w")
    tk.Button(top, text="Choose...", command=pick_text_color).grid(
        row=4, column=1, sticky="w", pady=2)

    tk.Label(top, text="Font:", **LBL).grid(row=5, column=0, sticky="w")
    font_frame = tk.Frame(top, bg="#1e1e1e")
    font_frame.grid(row=5, column=1, sticky="w", pady=2)
    font_label = tk.Label(
        font_frame,
        text=os.path.basename(state["font_path"]) if state["font_path"]
        else "Default (Consolas Bold)", **LBL)
    font_label.pack(side="left")
    tk.Button(font_frame, text="Browse...", command=pick_font).pack(
        side="left", padx=(6, 0))
    tk.Button(font_frame, text="Reset", command=reset_font).pack(
        side="left", padx=(4, 0))

    tk.Label(top, text="Preview:", **LBL).grid(
        row=6, column=0, sticky="nw", pady=(14, 0))
    preview_label.grid(row=6, column=1, sticky="w", pady=(14, 0))

    tk.Button(top, text="Start Overlay", font=("Segoe UI", 10, "bold"),
              bg="#2d7d46", fg="white", activebackground="#358a4f",
              command=start_clicked).grid(
        row=7, column=0, columnspan=2, pady=(18, 0), sticky="we")

    # Manual escape hatch: normally Start Overlay closes any leftover
    # instance on its own, but this gives a visible, no-questions-asked
    # way to clear a stuck one (a different PC/Windows build behaving
    # differently, security software blocking the message, etc.) without
    # ever touching Task Manager.
    tk.Button(top, text="Close All Instances", font=("Segoe UI", 9),
              command=close_all_clicked).grid(
        row=8, column=0, columnspan=2, pady=(8, 0), sticky="we")
    close_status_label = tk.Label(top, text="", **LBL)
    close_status_label.grid(row=9, column=0, columnspan=2, sticky="w", pady=(4, 0))

    refresh_preview()
    root.mainloop()

    if not state["started"]:
        return None

    return {
        "box_color": box_rgb + [box_alpha],
        "text_color": text_rgb,
        "font_path": state["font_path"],
    }


# ------------------------------------------------------------------ entry --
if __name__ == "__main__":
    # Close any stray copy of this app up front, before even showing our
    # own settings screen -- not just later inside main(). Without this,
    # launching a second copy while a first one is still sitting unstarted
    # at ITS settings screen (never got to Start Overlay, so main() and
    # its own close_existing_overlay() call never ran) leaves two settings
    # windows open at once with no cleanup happening on either side.
    close_existing_overlay()

    saved_cfg = load_config()
    result_cfg = build_settings_gui(saved_cfg)
    if result_cfg is None:
        sys.exit(0)
    save_config(result_cfg)

    FONT = load_font(result_cfg.get("font_path", ""))
    BOX_FILL = tuple(result_cfg["box_color"])
    FG_RGB = tuple(result_cfg["text_color"])
    PANEL_W = compute_panel_width()

    main()
