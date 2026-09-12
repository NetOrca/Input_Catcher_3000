"""
Research mode for Input_Catcher_3000.

Turns the overlay into a measurement instrument for reverse-engineering
game mechanics from OBS recordings. Everything here is OFF unless
"research_mode" is enabled in the settings screen, so the default
streaming overlay is unchanged.

What research mode adds:

  * A JSON-lines event log (one file per session, in research_logs/
    next to the exe) with a millisecond timestamp on every key/mouse
    press, release (with hold duration), wheel tick, mouse-position
    sample, raw mouse delta, and marker.
  * A visible session clock (mm:ss.mmm) drawn at the top of the overlay.
    It reads the SAME clock the log uses, so reading the clock off any
    video frame anchors the whole log to the footage.
  * A marker hotkey (default F8). Each press writes a numbered marker to
    the log and shows "MARK n" in the overlay.
  * Optional OBS sync over obs-websocket 5.x (built into OBS 28+), zero
    extra dependencies: a record hotkey (default F9) toggles recording,
    and every marker also stamps OBS's own recording timecode into the
    log, which makes log-to-video alignment exact without OCR.

Log format: see RESEARCH_MODE.md.
"""

import base64
import ctypes
import ctypes.wintypes as wt
import hashlib
import json
import os
import queue
import socket
import struct
import threading
import time
import uuid

VERSION = "1.2.1"  # keep in step with version_info.txt (the exe's Properties tab)

DEFAULTS = {
    "research_mode": False,
    "research_log_dir": "",          # "" -> <app dir>\research_logs
    "marker_hotkey": "F8",
    "record_hotkey": "F9",
    "marker_prefix": "trial",
    "mouse_sample_hz": 60,
    "obs_sync": False,
    "obs_host": "127.0.0.1",
    "obs_port": 4455,
    "obs_password": "",
}

FKEY_VK = {"F%d" % i: 0x6F + i for i in range(1, 13)}
FKEY_NAMES = ["F%d" % i for i in range(1, 13)]


# ------------------------------------------------------------------ clock --
class SessionClock:
    """Monotonic ms since the session started. perf_counter on Windows
    is QueryPerformanceCounter -- sub-microsecond resolution."""

    def __init__(self):
        self.origin = time.perf_counter()
        self.wall_start = time.time()

    def ms(self):
        return (time.perf_counter() - self.origin) * 1000.0

    def fmt(self, ms=None):
        if ms is None:
            ms = self.ms()
        total = int(ms)
        m, rem = divmod(total, 60000)
        s, milli = divmod(rem, 1000)
        return "%02d:%02d.%03d" % (m, s, milli)


# -------------------------------------------------------------- event log --
class EventLog:
    """Append-only JSON lines. One dict per line, always with "t" (ms on
    the session clock) and "type". Thread-safe: the OBS thread writes
    too."""

    def __init__(self, log_dir, clock, meta=None):
        os.makedirs(log_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S", time.localtime(clock.wall_start))
        self.path = os.path.join(log_dir, "research_%s.jsonl" % stamp)
        self.clock = clock
        self._lock = threading.Lock()
        self._f = open(self.path, "a", encoding="utf-8", buffering=1)
        rec = {
            "type": "session",
            "version": VERSION,
            "wall_start": clock.wall_start,
            "wall_start_iso": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(clock.wall_start)),
            "clock_note": "t = ms since session start; overlay clock shows the same value",
        }
        if meta:
            rec.update(meta)
        self.write(rec)

    def write(self, rec):
        if "t" not in rec:
            rec["t"] = round(self.clock.ms(), 3)
        line = json.dumps(rec, separators=(",", ":"))
        with self._lock:
            if self._f is not None:
                self._f.write(line + "\n")

    def close(self):
        with self._lock:
            if self._f is not None:
                self._f.write(json.dumps(
                    {"type": "session_end", "t": round(self.clock.ms(), 3)}) + "\n")
                self._f.close()
                self._f = None


# ---------------------------------------------------------- mouse sampling --
class MouseSampler:
    """Two views of the mouse, both throttled to mouse_sample_hz:

    "mouse"     absolute cursor position from the low-level mouse hook
                (fine for desktop apps and menus).
    "mouse_raw" accumulated raw-input deltas (WM_INPUT). This is what a
                game with a locked/recentered cursor actually consumes,
                so aim sway, sensitivity and zoom scaling come from here,
                not from the absolute position.
    """

    def __init__(self, log, hz):
        self.log = log
        self.interval_ms = 1000.0 / max(1, int(hz))
        self._last_abs_t = -1e9
        self._last_x = None
        self._last_y = None
        self._acc_dx = 0
        self._acc_dy = 0
        self._acc_n = 0
        self._last_raw_t = -1e9

    def on_move(self, x, y):
        now = self.log.clock.ms()
        if now - self._last_abs_t < self.interval_ms:
            return
        dx = 0 if self._last_x is None else x - self._last_x
        dy = 0 if self._last_y is None else y - self._last_y
        if self._last_x is not None and dx == 0 and dy == 0:
            return
        self._last_x, self._last_y, self._last_abs_t = x, y, now
        self.log.write({"type": "mouse", "t": round(now, 3),
                        "x": x, "y": y, "dx": dx, "dy": dy})

    def on_raw_delta(self, dx, dy):
        self._acc_dx += dx
        self._acc_dy += dy
        self._acc_n += 1
        self.flush_raw()

    def flush_raw(self, force=False):
        if self._acc_n == 0:
            return
        now = self.log.clock.ms()
        if not force and now - self._last_raw_t < self.interval_ms:
            return
        self.log.write({"type": "mouse_raw", "t": round(now, 3),
                        "dx": self._acc_dx, "dy": self._acc_dy,
                        "n": self._acc_n,
                        "span_ms": round(now - self._last_raw_t, 3)
                        if self._last_raw_t > -1e8 else None})
        self._acc_dx = self._acc_dy = self._acc_n = 0
        self._last_raw_t = now


# ------------------------------------------------------------- raw input --
# Registers the overlay window as a raw-input sink so it receives WM_INPUT
# mouse deltas even though it is never the foreground window
# (RIDEV_INPUTSINK). This is the only way to see the deltas a game reads
# once it locks the cursor.
WM_INPUT = 0x00FF
RID_INPUT = 0x10000003
RIDEV_INPUTSINK = 0x00000100
RIM_TYPEMOUSE = 0
MOUSE_MOVE_ABSOLUTE = 0x01


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wt.USHORT), ("usUsage", wt.USHORT),
                ("dwFlags", wt.DWORD), ("hwndTarget", wt.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wt.DWORD), ("dwSize", wt.DWORD),
                ("hDevice", ctypes.c_void_p), ("wParam", wt.WPARAM)]


class _RAWMOUSE_BTN(ctypes.Structure):
    _fields_ = [("usButtonFlags", wt.USHORT), ("usButtonData", wt.USHORT)]


class _RAWMOUSE_UNION(ctypes.Union):
    _fields_ = [("ulButtons", wt.ULONG), ("btn", _RAWMOUSE_BTN)]


class RAWMOUSE(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("usFlags", wt.USHORT), ("u", _RAWMOUSE_UNION),
                ("ulRawButtons", wt.ULONG), ("lLastX", ctypes.c_long),
                ("lLastY", ctypes.c_long), ("ulExtraInformation", wt.ULONG)]


class RAWINPUT_MOUSE(ctypes.Structure):
    _fields_ = [("header", RAWINPUTHEADER), ("mouse", RAWMOUSE)]


_user32 = ctypes.windll.user32
_user32.RegisterRawInputDevices.restype = wt.BOOL
_user32.RegisterRawInputDevices.argtypes = [
    ctypes.POINTER(RAWINPUTDEVICE), wt.UINT, wt.UINT]
_user32.GetRawInputData.restype = wt.UINT
_user32.GetRawInputData.argtypes = [
    ctypes.c_void_p, wt.UINT, ctypes.c_void_p,
    ctypes.POINTER(wt.UINT), wt.UINT]


def register_raw_mouse(hwnd):
    rid = RAWINPUTDEVICE(0x01, 0x02, RIDEV_INPUTSINK, hwnd)
    return bool(_user32.RegisterRawInputDevices(
        ctypes.byref(rid), 1, ctypes.sizeof(RAWINPUTDEVICE)))


def read_raw_mouse_delta(lparam):
    """Returns (dx, dy) for a relative-move WM_INPUT, else None."""
    size = wt.UINT(ctypes.sizeof(RAWINPUT_MOUSE))
    buf = RAWINPUT_MOUSE()
    got = _user32.GetRawInputData(ctypes.c_void_p(lparam), RID_INPUT,
                                  ctypes.byref(buf), ctypes.byref(size),
                                  ctypes.sizeof(RAWINPUTHEADER))
    if got == 0xFFFFFFFF or buf.header.dwType != RIM_TYPEMOUSE:
        return None
    if buf.mouse.usFlags & MOUSE_MOVE_ABSOLUTE:
        return None  # tablets / remote desktop; absolute view covers those
    return buf.mouse.lLastX, buf.mouse.lLastY


# ------------------------------------------------- minimal websocket client --
class _WebSocket:
    """Just enough RFC 6455 for obs-websocket: client handshake, masked
    text frames out, unfragmented frames in, ping/pong, close. No
    third-party package, in keeping with the rest of the app."""

    def __init__(self, host, port, timeout=3.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ("GET / HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
               "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
               "Sec-WebSocket-Version: 13\r\n\r\n") % (host, port, key)
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("handshake closed")
            resp += chunk
        head, _, rest = resp.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise ConnectionError("handshake refused: %r" % head[:80])
        self._buf = rest

    def _recv_exact(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("socket closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def send_text(self, text):
        payload = text.encode("utf-8")
        mask = os.urandom(4)
        n = len(payload)
        hdr = bytes([0x81])
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(hdr + mask + masked)

    def recv_text(self):
        """Blocks until a text frame arrives; answers pings itself."""
        while True:
            b0, b1 = self._recv_exact(2)
            opcode = b0 & 0x0F
            masked = b1 & 0x80
            n = b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if masked else None
            data = self._recv_exact(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x1:
                return data.decode("utf-8")
            if opcode == 0x9:  # ping -> pong
                self.sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
            elif opcode == 0x8:
                raise ConnectionError("server closed")
            # binary / continuation frames: obs-websocket never sends these
            # for a JSON session, ignore.

    def close(self):
        try:
            self.sock.sendall(bytes([0x88, 0x80]) + os.urandom(4))
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ------------------------------------------------------ obs-websocket v5 --
def obs_connect(host, port, password, timeout=3.0):
    """Open + identify. Returns a _WebSocket ready for requests, or
    raises. OBS 28+ ships obs-websocket 5.x; enable it under
    Tools > WebSocket Server Settings (port 4455 by default)."""
    ws = _WebSocket(host, port, timeout)
    hello = json.loads(ws.recv_text())
    if hello.get("op") != 0:
        raise ConnectionError("expected Hello, got op %r" % hello.get("op"))
    ident = {"rpcVersion": 1, "eventSubscriptions": 0}
    auth = hello.get("d", {}).get("authentication")
    if auth:
        if not password:
            raise ConnectionError("OBS requires a password (set one in settings)")
        secret = base64.b64encode(hashlib.sha256(
            (password + auth["salt"]).encode()).digest()).decode()
        ident["authentication"] = base64.b64encode(hashlib.sha256(
            (secret + auth["challenge"]).encode()).digest()).decode()
    ws.send_text(json.dumps({"op": 1, "d": ident}))
    try:
        reply = json.loads(ws.recv_text())
    except ConnectionError:
        # obs-websocket answers a bad password by closing the socket
        raise ConnectionError("OBS rejected the connection (wrong password?)")
    if reply.get("op") != 2:
        raise ConnectionError("identify failed (op %r)" % reply.get("op"))
    return ws


def obs_request(ws, request_type, data=None):
    rid = uuid.uuid4().hex
    msg = {"op": 6, "d": {"requestType": request_type, "requestId": rid}}
    if data:
        msg["d"]["requestData"] = data
    ws.send_text(json.dumps(msg))
    while True:
        reply = json.loads(ws.recv_text())
        if reply.get("op") == 7 and reply["d"].get("requestId") == rid:
            d = reply["d"]
            if not d["requestStatus"]["result"]:
                raise RuntimeError("%s: %s" % (
                    request_type, d["requestStatus"].get("comment", "failed")))
            return d.get("responseData") or {}


def obs_test(host, port, password):
    """Used by the settings screen's Test OBS button. Returns a short
    human-readable status string, never raises."""
    try:
        ws = obs_connect(host, int(port), password)
        try:
            v = obs_request(ws, "GetVersion")
            rs = obs_request(ws, "GetRecordStatus")
        finally:
            ws.close()
        return "OK: OBS %s, websocket %s, recording=%s" % (
            v.get("obsVersion", "?"), v.get("obsWebSocketVersion", "?"),
            "yes" if rs.get("outputActive") else "no")
    except (OSError, ConnectionError, RuntimeError, ValueError, KeyError) as e:
        return "Failed: %s" % e


class ObsSync(threading.Thread):
    """Background worker so nothing network-bound ever runs inside a
    Windows hook callback (a hook that stalls gets silently removed).
    Jobs: ("marker", n, name) stamps OBS's record timecode next to a
    marker; ("toggle_record",) starts/stops recording; ("status",)
    logs the current record state."""

    def __init__(self, host, port, password, log):
        super().__init__(daemon=True, name="ObsSync")
        self.host, self.port, self.password = host, int(port), password
        self.log = log
        self.jobs = queue.Queue()
        self.ws = None
        self.connected = False
        self._next_retry = 0.0

    def submit(self, *job):
        self.jobs.put((self.log.clock.ms(), job))

    def _ensure(self):
        if self.ws is not None:
            return True
        if time.time() < self._next_retry:
            return False
        try:
            self.ws = obs_connect(self.host, self.port, self.password)
            self.connected = True
            self.log.write({"type": "obs", "event": "connected",
                            "host": self.host, "port": self.port})
            return True
        except (OSError, ConnectionError, ValueError, KeyError) as e:
            self.connected = False
            self._next_retry = time.time() + 5.0
            self.log.write({"type": "obs", "event": "connect_failed",
                            "error": str(e)})
            return False

    def _drop(self):
        if self.ws is not None:
            self.ws.close()
        self.ws = None
        self.connected = False

    def run(self):
        self.submit("status")
        while True:
            t_submit, job = self.jobs.get()
            if job[0] == "quit":
                self._drop()
                return
            if not self._ensure():
                self.log.write({"type": "obs", "event": "skipped",
                                "job": job[0], "t_submit": round(t_submit, 3)})
                continue
            try:
                self._do(t_submit, job)
            except (OSError, ConnectionError, RuntimeError, ValueError, KeyError) as e:
                self.log.write({"type": "obs", "event": "error",
                                "job": job[0], "error": str(e)})
                self._drop()

    def _do(self, t_submit, job):
        clk = self.log.clock
        if job[0] == "marker":
            t_q = clk.ms()
            rs = obs_request(self.ws, "GetRecordStatus")
            t_r = clk.ms()
            self.log.write({
                "type": "obs_sync", "marker": job[1], "name": job[2],
                "t": round(t_submit, 3), "t_query": round(t_q, 3),
                "t_reply": round(t_r, 3),
                "obs_recording": bool(rs.get("outputActive")),
                "obs_duration_ms": rs.get("outputDuration"),
                "obs_timecode": rs.get("outputTimecode"),
            })
        elif job[0] == "toggle_record":
            t_q = clk.ms()
            r = obs_request(self.ws, "ToggleRecord")
            t_r = clk.ms()
            self.log.write({"type": "obs", "event": "record_toggled",
                            "t": round(t_submit, 3), "t_query": round(t_q, 3),
                            "t_reply": round(t_r, 3),
                            "obs_recording": bool(r.get("outputActive"))})
        elif job[0] == "status":
            rs = obs_request(self.ws, "GetRecordStatus")
            self.log.write({"type": "obs", "event": "status",
                            "obs_recording": bool(rs.get("outputActive")),
                            "obs_duration_ms": rs.get("outputDuration"),
                            "obs_timecode": rs.get("outputTimecode")})

    def stop(self):
        self.submit("quit")


# ---------------------------------------------------------------- session --
class ResearchSession:
    """Everything the overlay needs to talk to research mode. app.py
    creates one when research_mode is on and calls these from its hook
    callbacks / window proc."""

    def __init__(self, cfg, app_dir, screen_size):
        self.clock = SessionClock()
        log_dir = cfg.get("research_log_dir") or os.path.join(app_dir, "research_logs")
        self.log = EventLog(log_dir, self.clock, {
            "screen_w": screen_size[0], "screen_h": screen_size[1],
            "marker_hotkey": cfg.get("marker_hotkey", "F8"),
            "record_hotkey": cfg.get("record_hotkey", "F9"),
            "mouse_sample_hz": cfg.get("mouse_sample_hz", 60),
            "obs_sync": bool(cfg.get("obs_sync")),
        })
        self.sampler = MouseSampler(self.log, cfg.get("mouse_sample_hz", 60))
        self.marker_vk = FKEY_VK.get(cfg.get("marker_hotkey", "F8"), 0x77)
        self.record_vk = FKEY_VK.get(cfg.get("record_hotkey", "F9"), 0x78)
        self.marker_prefix = cfg.get("marker_prefix", "trial") or "trial"
        self.marker_count = 0
        self.obs = None
        if cfg.get("obs_sync"):
            self.obs = ObsSync(cfg.get("obs_host", "127.0.0.1"),
                               cfg.get("obs_port", 4455),
                               cfg.get("obs_password", ""), self.log)
            self.obs.start()

    # -- called from the keyboard hook -------------------------------------
    def key_down(self, name, vk):
        self.log.write({"type": "down", "dev": "kb", "key": name, "vk": vk})

    def key_up(self, name, vk, t_down):
        now = self.clock.ms()
        self.log.write({"type": "up", "dev": "kb", "key": name, "vk": vk,
                        "t": round(now, 3),
                        "hold_ms": round(now - t_down, 3) if t_down is not None else None})

    def marker(self):
        """Returns the label to show in the overlay for this press."""
        self.marker_count += 1
        n = self.marker_count
        name = "%s_%d" % (self.marker_prefix, n)
        self.log.write({"type": "marker", "n": n, "name": name})
        if self.obs is not None:
            self.obs.submit("marker", n, name)
        return "MARK %d" % n

    def record_toggle(self):
        if self.obs is None:
            self.log.write({"type": "obs", "event": "record_hotkey_no_sync"})
            return "REC (no OBS)"
        self.obs.submit("toggle_record")
        return "REC"

    # -- called from the mouse hook ----------------------------------------
    def button_down(self, name):
        self.log.write({"type": "down", "dev": "mouse", "key": name})

    def button_up(self, name, t_down):
        now = self.clock.ms()
        self.log.write({"type": "up", "dev": "mouse", "key": name,
                        "t": round(now, 3),
                        "hold_ms": round(now - t_down, 3) if t_down is not None else None})

    def wheel(self, delta):
        self.log.write({"type": "wheel", "delta": delta})

    def mouse_move(self, x, y):
        self.sampler.on_move(x, y)

    # -- called from the window proc ---------------------------------------
    def raw_input(self, lparam):
        d = read_raw_mouse_delta(lparam)
        if d is not None and (d[0] or d[1]):
            self.sampler.on_raw_delta(d[0], d[1])

    def tick(self):
        self.sampler.flush_raw(force=False)

    def close(self):
        self.sampler.flush_raw(force=True)
        if self.obs is not None:
            self.obs.stop()
            self.obs.join(timeout=2.0)
        self.log.close()
