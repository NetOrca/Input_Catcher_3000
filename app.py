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

v1.1 adds an optional Research mode (event log with ms timestamps,
on-screen session clock, marker/record hotkeys, OBS websocket sync) --
implemented in research.py, documented in RESEARCH_MODE.md. Off by
default; the plain overlay is unchanged when it is off.

`--no-gui` skips the settings screen and starts from the saved config.
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

import research  # research mode: event log, session clock, markers, OBS sync
import voice     # voice mode: talk, transcribe locally, type into the Claude app

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
DEFAULT_CONFIG.update(research.DEFAULTS)
DEFAULT_CONFIG.update(voice.VOICE_DEFAULTS)


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
BASE_PANEL_H = 260
PANEL_H = BASE_PANEL_H
LEFT_MARGIN = 40
BOTTOM_MARGIN = 90
ROW_H = 30
FOCAL_Y = PANEL_H - 60
MAX_ROWS = PANEL_H // ROW_H
# Research mode adds a header band with the session clock above the key
# log. HEADER_H is 0 in normal mode so the overlay is pixel-identical to
# the plain streaming build.
RESEARCH_HEADER_H = ROW_H + 6
HEADER_H = 0
RESEARCH = None  # research.ResearchSession when research mode is on
# Small reminder drawn to the right of the clock, e.g. "F8 mark  F9 rec",
# so the user never has to open settings to remember their hotkeys.
HEADER_HINT = ""
HINT_FONT_SIZE = 14
HINT_GAP = 12
SMALL_FONT = None
# Voice mode adds a footer band at the bottom: "F7 voice" when idle, a
# red "F7 send  0:04" readout while the mic is open.
VOICE_FOOTER_H = ROW_H + 4
FOOTER_H = 0
VOICE = None          # voice.VoiceSession when voice mode is on
VOICE_HOTKEY = "F7"
VOICE_VK = 0x76
LIVE_RGB = (255, 90, 90)
HOLD_THRESHOLD = 0.30
FADE_DURATION = 1.1
FONT_SIZE = 22
DEFAULT_FONT_PATHS = [r"C:\Windows\Fonts\consolab.ttf", r"C:\Windows\Fonts\arialbd.ttf"]
TICK_MS = 33
# Research mode redraws faster so the on-screen clock lags the log by at
# most ~one 60 fps frame (the clock on any video frame is the render
# time; log events carry the true time).
RESEARCH_TICK_MS = 16
HOLD_RGB = (255, 205, 90)
WM_MOUSEMOVE = 0x0200
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
    """Returns True on a fresh press, False for Windows auto-repeat of a
    key that is already down (those are not logged twice)."""
    if key_id in held:
        return False
    e = {"label": label, "born": time.time(), "released": None,
         "t0": RESEARCH.clock.ms() if RESEARCH is not None else None}
    held[key_id] = e
    entries.append(e)
    return True


def release(key_id):
    """Returns the research-clock time of the matching press (or None)."""
    e = held.pop(key_id, None)
    if e is not None:
        e["released"] = time.time()
        return e.get("t0")
    return None


def tap(label):
    now = time.time()
    entries.append({"label": label, "born": now, "released": now})


# ------------------------------------------------------------------ hooks --
def kb_hook(nCode, wParam, lParam):
    if nCode >= 0:
        info = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        if info.dwExtraInfo == voice.INJECT_MAGIC:
            # Our own Ctrl+V / Enter / Alt taps while delivering a voice
            # message -- not the user's keystrokes, keep them out of the
            # overlay and the log.
            return user32.CallNextHookEx(None, nCode, wParam, lParam)
        extended = bool(info.flags & LLKHF_EXTENDED)
        name = vk_to_name(info.vkCode, info.scanCode, extended)
        key_id = ("kb", info.vkCode)
        if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            label = name
            if VOICE is not None and key_id not in held and info.vkCode == VOICE_VK:
                label = VOICE.toggle()
            elif RESEARCH is not None and key_id not in held:
                # Hotkeys show as MARK n / REC in the overlay but are still
                # logged under their real key name, so the log stays a
                # complete, consistent record of what was pressed.
                if info.vkCode == RESEARCH.marker_vk:
                    label = RESEARCH.marker()
                elif info.vkCode == RESEARCH.record_vk:
                    label = RESEARCH.record_toggle()
            if press(key_id, label) and RESEARCH is not None:
                RESEARCH.key_down(name, info.vkCode)
        elif wParam in (WM_KEYUP, WM_SYSKEYUP):
            t0 = release(key_id)
            if RESEARCH is not None:
                RESEARCH.key_up(name, info.vkCode, t0)
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


def mouse_hook(nCode, wParam, lParam):
    if nCode >= 0:
        info = ctypes.cast(lParam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
        if wParam == WM_MOUSEMOVE:
            if RESEARCH is not None:
                RESEARCH.mouse_move(info.pt.x, info.pt.y)
        elif wParam in _MOUSE_DOWN:
            btn = _MOUSE_DOWN[wParam]
            if press(("m", btn), btn + "MB") and RESEARCH is not None:
                RESEARCH.button_down(btn + "MB")
        elif wParam in _MOUSE_UP:
            btn = _MOUSE_UP[wParam]
            t0 = release(("m", btn))
            if RESEARCH is not None:
                RESEARCH.button_up(btn + "MB", t0)
        elif wParam == WM_MOUSEWHEEL:
            delta = ctypes.c_short(info.mouseData >> 16).value
            tap("Scroll Up" if delta > 0 else "Scroll Down")
            if RESEARCH is not None:
                RESEARCH.wheel(delta)
    return user32.CallNextHookEx(None, nCode, wParam, lParam)


_MOUSE_DOWN = {WM_LBUTTONDOWN: "L", WM_RBUTTONDOWN: "R", WM_MBUTTONDOWN: "M"}
_MOUSE_UP = {WM_LBUTTONUP: "L", WM_RBUTTONUP: "R", WM_MBUTTONUP: "M"}


KB_PROC = HOOKPROC(kb_hook)
MOUSE_PROC = HOOKPROC(mouse_hook)


# ------------------------------------------------------------- rendering --
def load_font(custom_path="", size=None):
    size = size or FONT_SIZE
    if custom_path:
        try:
            return ImageFont.truetype(custom_path, size)
        except OSError:
            pass  # fall through to the built-in defaults below
    for p in DEFAULT_FONT_PATHS:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def make_header_hint(cfg):
    """'F8 mark  F9 rec' -- the record half only when OBS sync is on,
    since F9 does nothing useful without it."""
    hint = "%s mark" % cfg.get("marker_hotkey", "F8")
    if cfg.get("obs_sync"):
        hint += "  %s rec" % cfg.get("record_hotkey", "F9")
    return hint


def compute_panel_width():
    """Auto-fit width: measure the worst-case label at the current font
    so the panel is exactly as wide as it needs to be, whatever font
    the user picked."""
    dummy = Image.new("RGBA", (10, 10))
    d = ImageDraw.Draw(dummy)
    bbox = d.textbbox((0, 0), WIDEST_LABEL, font=FONT)
    text_w = bbox[2] - bbox[0]
    if HEADER_H and HEADER_HINT and SMALL_FONT is not None:
        # Research header: clock + hotkey hint on one line may be wider
        # than the widest key label; the panel grows to fit.
        cb = d.textbbox((0, 0), "00:00.000", font=FONT)
        hb = d.textbbox((0, 0), HEADER_HINT, font=SMALL_FONT)
        header_w = (cb[2] - cb[0]) + HINT_GAP + (hb[2] - hb[0])
        text_w = max(text_w, header_w)
    return text_w + TEXT_PAD_X * 2


def draw_line(draw, text, y, rgba):
    draw.text((TEXT_PAD_X, y), text, font=FONT, fill=rgba)


def draw_header_band(draw, clock_text):
    """Research mode: session clock (mm:ss.mmm) at the top of the panel,
    full opacity, the hotkey hint in a smaller face to its right, and a
    hairline under both. Reading the clock off a video frame is what
    anchors the event log to the footage."""
    draw_line(draw, clock_text, 3, FG_RGB + (255,))
    if HEADER_HINT and SMALL_FONT is not None:
        cb = draw.textbbox((TEXT_PAD_X, 3), clock_text, font=FONT)
        x = cb[2] + HINT_GAP
        y = 3 + (FONT_SIZE - HINT_FONT_SIZE) // 2 + 2
        draw.text((x, y), HEADER_HINT, font=SMALL_FONT, fill=FG_RGB + (200,))
    y = HEADER_H - 2
    draw.line((TEXT_PAD_X, y, PANEL_W - TEXT_PAD_X, y),
              fill=FG_RGB + (110,), width=1)


def draw_header(draw):
    draw_header_band(draw, RESEARCH.clock.fmt())


def apply_layout(research_on, cfg=None):
    """Sets the panel geometry for normal vs research mode. Called once
    before the overlay window is created (and by the settings preview).
    cfg supplies the hotkey names + font for the header hint."""
    global PANEL_H, FOCAL_Y, MAX_ROWS, HEADER_H, HEADER_HINT, SMALL_FONT
    global FOOTER_H, VOICE_HOTKEY, VOICE_VK
    cfg = cfg or {}
    voice_on = bool(cfg.get("voice_mode"))
    HEADER_H = RESEARCH_HEADER_H if research_on else 0
    FOOTER_H = VOICE_FOOTER_H if voice_on else 0
    PANEL_H = BASE_PANEL_H + HEADER_H + FOOTER_H
    FOCAL_Y = PANEL_H - 60 - FOOTER_H
    MAX_ROWS = (PANEL_H - HEADER_H - FOOTER_H) // ROW_H + 1
    HEADER_HINT = make_header_hint(cfg) if research_on else ""
    VOICE_HOTKEY = cfg.get("voice_hotkey", "F7")
    VOICE_VK = research.FKEY_VK.get(VOICE_HOTKEY, 0x76)
    if research_on or voice_on:
        SMALL_FONT = load_font(cfg.get("font_path", ""), HINT_FONT_SIZE)
    else:
        SMALL_FONT = None


def draw_footer_band(draw, text, style):
    """Voice mode: hairline + one small line at the bottom of the panel.
    style 'idle' = dim key reminder, 'live' = red dot + red text while
    the mic is open, 'note' = gold status (sent / error)."""
    top = PANEL_H - FOOTER_H
    draw.line((TEXT_PAD_X, top + 1, PANEL_W - TEXT_PAD_X, top + 1),
              fill=FG_RGB + (110,), width=1)
    y = top + 6
    x = TEXT_PAD_X
    if style == "live":
        r = 5
        cy = y + HINT_FONT_SIZE // 2 + 1
        draw.ellipse((x, cy - r, x + 2 * r, cy + r), fill=LIVE_RGB + (255,))
        x += 2 * r + 8
        rgba = LIVE_RGB + (255,)
    elif style == "note":
        rgba = HOLD_RGB + (255,)
    else:
        rgba = FG_RGB + (200,)
    draw.text((x, y), text, font=SMALL_FONT, fill=rgba)


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
    if RESEARCH is not None:
        draw_header(draw)
    if VOICE is not None:
        draw_footer_band(draw, *VOICE.footer_text(VOICE_HOTKEY))
    for i, e in enumerate(ordered):
        y = FOCAL_Y - i * ROW_H
        if y < HEADER_H:
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
    if HEADER_H:
        draw_header_band(draw, "00:12.345")
        draw_line(draw, "MARK 3", FOCAL_Y - ROW_H * 2, HOLD_RGB + (255,))
    if FOOTER_H:
        draw_footer_band(draw, "%s voice" % VOICE_HOTKEY, "idle")
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
        if RESEARCH is not None:
            RESEARCH.tick()
        update_overlay(hwnd)
        return 0
    if msg == research.WM_INPUT:
        if RESEARCH is not None:
            RESEARCH.raw_input(lparam)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
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


SETTINGS_TITLE_BASE = "Input_Catcher_3000 Settings"
SETTINGS_TITLE = "%s  v%s" % (SETTINGS_TITLE_BASE, research.VERSION)
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
                # Prefix match so an older build's settings window (no
                # version suffix, or a different one) is still caught.
                is_match = title_buf.value.startswith(SETTINGS_TITLE_BASE)
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


def main(cfg=None):
    global screen_dc, mem_dc, dib_bits, dib_bmp, win_x, win_y, RESEARCH, VOICE

    close_existing_overlay()
    cfg = cfg or {}
    research_on = bool(cfg.get("research_mode"))
    apply_layout(research_on, cfg)

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

    if research_on:
        # Session clock starts here, so t=0 in the log is the moment the
        # overlay appeared; the on-screen clock shows the same value.
        RESEARCH = research.ResearchSession(cfg, app_dir(), (screen_w, screen_h))
        ok = research.register_raw_mouse(hwnd)
        RESEARCH.log.write({"type": "raw_mouse_registered", "ok": bool(ok),
                            "log_path": RESEARCH.log.path})
    if cfg.get("voice_mode"):
        VOICE = voice.VoiceSession(
            cfg, app_dir(),
            on_event=lambda rec: RESEARCH.log.write(rec) if RESEARCH is not None else None)
    fast = research_on or VOICE is not None
    user32.SetTimer(hwnd, 1, RESEARCH_TICK_MS if fast else TICK_MS, None)

    hook_kb = user32.SetWindowsHookExW(WH_KEYBOARD_LL, KB_PROC, h_instance, 0)
    hook_mouse = user32.SetWindowsHookExW(WH_MOUSE_LL, MOUSE_PROC, h_instance, 0)

    msg = wt.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))

    user32.UnhookWindowsHookEx(hook_kb)
    user32.UnhookWindowsHookEx(hook_mouse)
    if VOICE is not None:
        VOICE.close()
        VOICE = None
    if RESEARCH is not None:
        RESEARCH.close()
        RESEARCH = None


# ------------------------------------------------------------ settings UI --
def _rgb_to_hex(rgb):
    return "#%02x%02x%02x" % tuple(int(c) for c in rgb)


class Tooltip:
    """Hover tooltip for any tk widget (tkinter has no built-in one).
    Shows after a short delay, hides on leave."""

    def __init__(self, widget, text, delay_ms=350):
        self.widget, self.text, self.delay = widget, text, delay_ms
        self._after = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after is not None:
            self.widget.after_cancel(self._after)
            self._after = None

    def _show(self):
        if self._tip is not None:
            return
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 8
        y = self.widget.winfo_rooty() - 4
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_attributes("-topmost", True)
        self._tip.wm_geometry("+%d+%d" % (x, y))
        tk.Label(self._tip, text=self.text, justify="left", wraplength=300,
                 bg="#2b2b2b", fg="#f0f0f0", font=("Segoe UI", 9),
                 relief="solid", bd=1, padx=8, pady=6,
                 highlightthickness=0).pack()

    def _hide(self, _e=None):
        self._cancel()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


def info_icon(parent, text):
    """A small gold 'i' badge on a gray disc that shows `text` on hover.
    Drawn on a Canvas so it looks the same on every Windows theme."""
    size = 18
    c = tk.Canvas(parent, width=size, height=size, bg="#1e1e1e",
                  highlightthickness=0, cursor="question_arrow")
    c.create_oval(1, 1, size - 1, size - 1, fill="#4a4a4a", outline="#4a4a4a")
    c.create_oval(3, 3, size - 3, size - 3, fill="#e6b422", outline="#c99a12")
    c.create_text(size // 2, size // 2 + 1, text="i",
                  font=("Georgia", 10, "bold"), fill="#1e1e1e")
    Tooltip(c, text)
    return c


MARKER_TIP = ("Marker key. Press it while recording to drop a numbered "
              "marker: MARK 1, MARK 2... shows in the overlay and is "
              "written to the log with the exact time (and OBS's recording "
              "time, if OBS sync is on). Press it at the start of each "
              "thing you want to measure.")
RECORD_TIP = ("Record key. Starts or stops OBS recording without switching "
              "to OBS, and logs when it happened. Needs OBS sync turned on "
              "and a working Test OBS; without that, pressing it just logs "
              "a note.")
VOICE_TIP = ("Voice key. Press once to start listening (the footer turns "
             "red), say your message, press again to send. Speech is turned "
             "into text on this PC, then pasted into the Claude app window "
             "and sent with Enter. Focus goes back to what you were doing. "
             "First use downloads a ~150 MB speech model.")


def build_settings_gui(cfg):
    """Shows the Input_Catcher_3000 settings window. Blocks until the user
    clicks Start Overlay or closes the window. Returns the edited cfg
    dict, or None if the window was closed without starting."""
    global FONT, PANEL_W, BOX_FILL, FG_RGB

    root = tk.Tk()
    root.title(SETTINGS_TITLE)
    root.resizable(False, False)
    root.configure(bg="#1e1e1e")

    box_rgb = list(cfg["box_color"][:3])
    box_alpha = cfg["box_color"][3] if len(cfg["box_color"]) > 3 else 150
    text_rgb = list(cfg["text_color"])
    state = {"font_path": cfg.get("font_path", ""), "started": False,
             "research_log_dir": cfg.get("research_log_dir", "")}
    research_var = tk.BooleanVar(value=bool(cfg.get("research_mode")))
    marker_var = tk.StringVar(value=cfg.get("marker_hotkey", "F8"))
    record_var = tk.StringVar(value=cfg.get("record_hotkey", "F9"))
    obs_var = tk.BooleanVar(value=bool(cfg.get("obs_sync")))
    obs_port_var = tk.StringVar(value=str(cfg.get("obs_port", 4455)))
    obs_pw_var = tk.StringVar(value=cfg.get("obs_password", ""))
    voice_var = tk.BooleanVar(value=bool(cfg.get("voice_mode")))
    voice_key_var = tk.StringVar(value=cfg.get("voice_hotkey", "F7"))
    voice_enter_var = tk.BooleanVar(value=bool(cfg.get("voice_send_enter", True)))
    voice_title_var = tk.StringVar(value=cfg.get("voice_target_title", "Claude"))

    def refresh_preview():
        global FONT, PANEL_W, BOX_FILL, FG_RGB
        apply_layout(research_var.get(), {
            "font_path": state["font_path"],
            "marker_hotkey": marker_var.get(),
            "record_hotkey": record_var.get(),
            "obs_sync": obs_var.get(),
            "voice_mode": voice_var.get(),
            "voice_hotkey": voice_key_var.get(),
        })
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

    # ---- Research mode (off by default; the plain overlay is unchanged) --
    def pick_log_dir():
        path = filedialog.askdirectory(title="Choose log folder")
        if path:
            state["research_log_dir"] = path
            log_dir_label.configure(text=path)

    def test_obs_clicked():
        obs_status_label.configure(text="Testing...")
        result = {}

        def worker():
            result["text"] = research.obs_test(
                cfg.get("obs_host", "127.0.0.1"), obs_port_var.get().strip() or "4455",
                obs_pw_var.get())

        def poll():
            if "text" in result:
                obs_status_label.configure(text=result["text"])
                if result["text"].startswith("OK") and not obs_var.get():
                    # A working connection is the only reason to run the
                    # test -- turn sync on so the user doesn't Start with
                    # the box still unticked and wonder why F9 is dead.
                    obs_var.set(True)
            else:
                root.after(100, poll)

        import threading
        threading.Thread(target=worker, daemon=True).start()
        root.after(100, poll)

    def on_research_toggle():
        st = "normal" if research_var.get() else "disabled"
        for w in research_widgets:
            w.configure(state=st)
        refresh_preview()

    rf = tk.LabelFrame(top, text=" Research mode ", bg="#1e1e1e", fg="#e8e8e8",
                       font=("Segoe UI", 9, "bold"), padx=10, pady=6)
    rf.grid(row=7, column=0, columnspan=2, sticky="we", pady=(14, 0))
    CHK = {"bg": "#1e1e1e", "fg": "#e8e8e8", "selectcolor": "#3a3a3a",
           "activebackground": "#1e1e1e", "activeforeground": "#e8e8e8",
           "font": ("Segoe UI", 9)}
    tk.Checkbutton(rf, text="Enable (event log + on-screen clock + markers)",
                   variable=research_var, command=on_research_toggle,
                   **CHK).grid(row=0, column=0, columnspan=3, sticky="w")
    tk.Label(rf, text="Marker key:", **LBL).grid(row=1, column=0, sticky="w")
    # Dropdown + info badge sit together in one small frame so the badge
    # is right beside the thing it explains, not out in the next column.
    marker_row = tk.Frame(rf, bg="#1e1e1e")
    marker_row.grid(row=1, column=1, columnspan=2, sticky="w", pady=1)
    marker_menu = tk.OptionMenu(marker_row, marker_var, *research.FKEY_NAMES)
    marker_menu.configure(width=4)
    marker_menu.pack(side="left")
    info_icon(marker_row, MARKER_TIP).pack(side="left", padx=(6, 0))
    tk.Label(rf, text="Record key:", **LBL).grid(row=2, column=0, sticky="w")
    record_row = tk.Frame(rf, bg="#1e1e1e")
    record_row.grid(row=2, column=1, columnspan=2, sticky="w", pady=1)
    record_menu = tk.OptionMenu(record_row, record_var, *research.FKEY_NAMES)
    record_menu.configure(width=4)
    record_menu.pack(side="left")
    info_icon(record_row, RECORD_TIP).pack(side="left", padx=(6, 0))
    # The preview's header hint mirrors these choices live.
    for v in (marker_var, record_var, obs_var):
        v.trace_add("write", lambda *_: refresh_preview())
    tk.Label(rf, text="Log folder:", **LBL).grid(row=3, column=0, sticky="w")
    log_dir_label = tk.Label(
        rf, text=state["research_log_dir"] or "research_logs (next to the exe)",
        **LBL)
    log_dir_label.grid(row=3, column=1, sticky="w")
    log_dir_btn = tk.Button(rf, text="Browse...", command=pick_log_dir)
    log_dir_btn.grid(row=3, column=2, sticky="w", padx=(6, 0), pady=1)
    obs_chk = tk.Checkbutton(
        rf, text="OBS sync (stamp OBS record time on markers; record key toggles recording)",
        variable=obs_var, **CHK)
    obs_chk.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
    tk.Label(rf, text="OBS port:", **LBL).grid(row=5, column=0, sticky="w")
    obs_port_entry = tk.Entry(rf, textvariable=obs_port_var, width=8)
    obs_port_entry.grid(row=5, column=1, sticky="w", pady=1)
    tk.Label(rf, text="OBS password:", **LBL).grid(row=6, column=0, sticky="w")
    obs_pw_entry = tk.Entry(rf, textvariable=obs_pw_var, width=22, show="*")
    obs_pw_entry.grid(row=6, column=1, sticky="w", pady=1)
    obs_test_btn = tk.Button(rf, text="Test OBS", command=test_obs_clicked)
    obs_test_btn.grid(row=6, column=2, sticky="w", padx=(6, 0))
    obs_status_label = tk.Label(rf, text="", **LBL)
    obs_status_label.grid(row=7, column=0, columnspan=3, sticky="w")
    research_widgets = [marker_menu, record_menu, log_dir_btn, obs_chk,
                        obs_port_entry, obs_pw_entry, obs_test_btn]
    on_research_toggle()

    # ---- Voice to Claude -------------------------------------------------
    def on_voice_toggle():
        st = "normal" if voice_var.get() else "disabled"
        for w in voice_widgets:
            w.configure(state=st)
        refresh_preview()

    vf = tk.LabelFrame(top, text=" Voice to Claude ", bg="#1e1e1e", fg="#e8e8e8",
                       font=("Segoe UI", 9, "bold"), padx=10, pady=6)
    vf.grid(row=8, column=0, columnspan=2, sticky="we", pady=(10, 0))
    tk.Checkbutton(vf, text="Enable (press the voice key, talk, press it again to send)",
                   variable=voice_var, command=on_voice_toggle,
                   **CHK).grid(row=0, column=0, columnspan=3, sticky="w")
    tk.Label(vf, text="Voice key:", **LBL).grid(row=1, column=0, sticky="w")
    voice_row = tk.Frame(vf, bg="#1e1e1e")
    voice_row.grid(row=1, column=1, columnspan=2, sticky="w", pady=1)
    voice_menu = tk.OptionMenu(voice_row, voice_key_var, *research.FKEY_NAMES)
    voice_menu.configure(width=4)
    voice_menu.pack(side="left")
    info_icon(voice_row, VOICE_TIP).pack(side="left", padx=(6, 0))
    tk.Label(vf, text="Send to window:", **LBL).grid(row=2, column=0, sticky="w")
    voice_title_entry = tk.Entry(vf, textvariable=voice_title_var, width=22)
    voice_title_entry.grid(row=2, column=1, sticky="w", pady=1)
    voice_enter_chk = tk.Checkbutton(
        vf, text="Press Enter to send (untick to just type it in)",
        variable=voice_enter_var, **CHK)
    voice_enter_chk.grid(row=3, column=0, columnspan=3, sticky="w")
    voice_widgets = [voice_menu, voice_title_entry, voice_enter_chk]
    voice_key_var.trace_add("write", lambda *_: refresh_preview())
    on_voice_toggle()

    tk.Button(top, text="Start Overlay", font=("Segoe UI", 10, "bold"),
              bg="#2d7d46", fg="white", activebackground="#358a4f",
              command=start_clicked).grid(
        row=9, column=0, columnspan=2, pady=(18, 0), sticky="we")

    # Manual escape hatch: normally Start Overlay closes any leftover
    # instance on its own, but this gives a visible, no-questions-asked
    # way to clear a stuck one (a different PC/Windows build behaving
    # differently, security software blocking the message, etc.) without
    # ever touching Task Manager.
    tk.Button(top, text="Close All Instances", font=("Segoe UI", 9),
              command=close_all_clicked).grid(
        row=10, column=0, columnspan=2, pady=(8, 0), sticky="we")
    close_status_label = tk.Label(top, text="", **LBL)
    close_status_label.grid(row=11, column=0, columnspan=2, sticky="w", pady=(4, 0))

    refresh_preview()
    root.mainloop()

    if not state["started"]:
        return None

    try:
        obs_port = int(obs_port_var.get().strip() or 4455)
    except ValueError:
        obs_port = 4455
    out = dict(cfg)  # keep any keys the UI doesn't expose (obs_host, prefix...)
    out.update({
        "box_color": box_rgb + [box_alpha],
        "text_color": text_rgb,
        "font_path": state["font_path"],
        "research_mode": bool(research_var.get()),
        "research_log_dir": state["research_log_dir"],
        "marker_hotkey": marker_var.get(),
        "record_hotkey": record_var.get(),
        "obs_sync": bool(obs_var.get()),
        "obs_port": obs_port,
        "obs_password": obs_pw_var.get(),
        "voice_mode": bool(voice_var.get()),
        "voice_hotkey": voice_key_var.get(),
        "voice_send_enter": bool(voice_enter_var.get()),
        "voice_target_title": voice_title_var.get().strip() or "Claude",
    })
    return out


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
    if "--no-gui" in sys.argv:
        # Skip the settings screen and start straight from the saved
        # config -- for shortcuts/scripts that launch it alongside OBS,
        # and for automated testing of research mode.
        result_cfg = saved_cfg
    else:
        result_cfg = build_settings_gui(saved_cfg)
        if result_cfg is None:
            sys.exit(0)
        save_config(result_cfg)

    apply_layout(bool(result_cfg.get("research_mode")), result_cfg)
    FONT = load_font(result_cfg.get("font_path", ""))
    BOX_FILL = tuple(result_cfg["box_color"])
    FG_RGB = tuple(result_cfg["text_color"])
    PANEL_W = compute_panel_width()

    main(result_cfg)
