"""
Voice-to-Claude for Input_Catcher_3000.

Optional mode: press the voice key once to start listening, talk, press
it again to send. The speech is transcribed ON THIS PC (faster-whisper,
no cloud), then typed into the Claude desktop app for you: the app
window is brought forward, the text is pasted into the message box, and
Enter is pressed (optional). Focus is then handed back to whatever you
were using. Claude reads its replies aloud on its side.

The overlay shows the voice key in a footer band at the bottom of the
panel and switches to a red "listening" readout while the mic is open.

Off unless "voice_mode" is enabled in settings. First use downloads the
speech model (~150 MB) into a "models" folder next to the exe.
"""

import ctypes
import ctypes.wintypes as wt
import os
import threading
import time

VOICE_DEFAULTS = {
    "voice_mode": False,
    "voice_hotkey": "F7",
    "voice_target_title": "Claude",   # exact window title of the Claude app
    "voice_send_enter": True,          # press Enter after pasting
    "voice_return_focus": True,        # give focus back to the previous window
    "voice_model": "base.en",          # faster-whisper model name
    "voice_input_device": "",          # "" = system default mic
}

SAMPLE_RATE = 16000
MAX_SECONDS = 120          # hard stop so a forgotten toggle can't record forever
INJECT_MAGIC = 0x1C3000    # dwExtraInfo tag on our own synthetic keys

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.GetForegroundWindow.restype = wt.HWND
user32.SetForegroundWindow.argtypes = [wt.HWND]
user32.SetForegroundWindow.restype = wt.BOOL
user32.IsIconic.argtypes = [wt.HWND]
user32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
user32.OpenClipboard.argtypes = [wt.HWND]
user32.SetClipboardData.restype = wt.HANDLE
user32.SetClipboardData.argtypes = [wt.UINT, wt.HANDLE]
kernel32.GlobalAlloc.restype = wt.HGLOBAL
kernel32.GlobalAlloc.argtypes = [wt.UINT, ctypes.c_size_t]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [wt.HGLOBAL]
kernel32.GlobalUnlock.argtypes = [wt.HGLOBAL]

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
SW_RESTORE = 9
VK_CONTROL, VK_MENU, VK_RETURN, VK_V = 0x11, 0x12, 0x0D, 0x56
KEYEVENTF_KEYUP = 0x0002


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wt.WORD), ("wScan", wt.WORD), ("dwFlags", wt.DWORD),
                ("time", wt.DWORD), ("dwExtraInfo", ctypes.c_void_p)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", _KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wt.DWORD), ("u", _U)]


def _key(vk, up=False):
    inp = _INPUT()
    inp.type = 1  # INPUT_KEYBOARD
    inp.ki = _KEYBDINPUT(vk, 0, KEYEVENTF_KEYUP if up else 0, 0, INJECT_MAGIC)
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


def _tap(*vks):
    for vk in vks:
        _key(vk)
    for vk in reversed(vks):
        _key(vk, up=True)


def set_clipboard_text(text):
    data = text.encode("utf-16-le") + b"\x00\x00"
    for _ in range(10):  # another app may briefly hold the clipboard
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        return False
    try:
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        p = kernel32.GlobalLock(h)
        ctypes.memmove(p, data, len(data))
        kernel32.GlobalUnlock(h)
        user32.SetClipboardData(CF_UNICODETEXT, h)
    finally:
        user32.CloseClipboard()
    return True


def find_target_window(title):
    return user32.FindWindowW(None, title) or None


def deliver_text(title, text, press_enter=True, return_focus=True):
    """Bring the target window forward, paste, optionally press Enter,
    then hand focus back. Returns (ok, message)."""
    hwnd = find_target_window(title)
    if not hwnd:
        return False, "window '%s' not found" % title
    if not set_clipboard_text(text):
        return False, "clipboard busy"
    prev = user32.GetForegroundWindow()
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    # Windows only lets the foreground change after some input activity;
    # a harmless Alt tap satisfies it.
    _tap(VK_MENU)
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.25)
    if user32.GetForegroundWindow() != hwnd:
        return False, "could not focus '%s'" % title
    _tap(VK_CONTROL, VK_V)
    time.sleep(0.15)
    if press_enter:
        _tap(VK_RETURN)
        time.sleep(0.15)
    if return_focus and prev and prev != hwnd:
        _tap(VK_MENU)
        user32.SetForegroundWindow(prev)
    return True, "sent"


class VoiceSession:
    """State machine: idle -> listening -> transcribing -> (sent|error) -> idle.
    All heavy work runs on background threads; hooks only call toggle()."""

    def __init__(self, cfg, app_dir, on_event=None):
        self.cfg = cfg
        self.on_event = on_event or (lambda rec: None)
        self.model_dir = os.path.join(app_dir, "models")
        self.state = "loading"
        self.message = ""
        self.message_until = 0.0
        self.ready = False
        self._model = None
        self._stream = None
        self._chunks = []
        self._t_start = 0.0
        self._lock = threading.Lock()
        threading.Thread(target=self._load_model, daemon=True,
                         name="VoiceModelLoad").start()

    # ---- model ------------------------------------------------------------
    def _load_model(self):
        try:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(
                self.cfg.get("voice_model", "base.en"), device="cpu",
                compute_type="int8", download_root=self.model_dir)
            self.ready = True
            self._set("idle")
            self.on_event({"type": "voice", "event": "model_ready"})
        except Exception as e:  # noqa: BLE001 - surface anything to the user
            self._set("error", "model failed: %s" % str(e)[:60], 8.0)
            self.on_event({"type": "voice", "event": "model_failed",
                           "error": str(e)})

    def _set(self, state, message="", hold=0.0):
        with self._lock:
            self.state = state
            self.message = message
            self.message_until = time.time() + hold if hold else 0.0

    # ---- hotkey -------------------------------------------------------------
    def toggle(self):
        """Called from the keyboard hook on the voice key. Returns the label
        to show in the key log."""
        if not self.ready:
            self._set("loading", "voice: model still loading", 2.0)
            return "VOICE (loading)"
        if self.state == "listening":
            self._stop_and_send()
            return "VOICE send"
        if self.state in ("idle", "sent", "error"):
            self._start()
            return "VOICE listen"
        return "VOICE busy"

    def _start(self):
        import sounddevice as sd
        self._chunks = []
        dev = self.cfg.get("voice_input_device") or None
        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                device=dev, callback=self._on_audio)
            self._stream.start()
        except Exception as e:  # noqa: BLE001
            self._stream = None
            self._set("error", "mic: %s" % str(e)[:50], 5.0)
            self.on_event({"type": "voice", "event": "mic_failed", "error": str(e)})
            return
        self._t_start = time.time()
        self._set("listening")
        self.on_event({"type": "voice", "event": "listen_start"})

    def _on_audio(self, indata, frames, t, status):
        self._chunks.append(bytes(indata))
        if time.time() - self._t_start > MAX_SECONDS:
            threading.Thread(target=self._stop_and_send, daemon=True).start()

    def _stop_and_send(self):
        with self._lock:
            if self.state != "listening":
                return
            self.state = "transcribing"
            self.message = ""
        stream, self._stream = self._stream, None
        try:
            if stream is not None:
                stream.stop()
                stream.close()
        except Exception:  # noqa: BLE001
            pass
        seconds = time.time() - self._t_start
        chunks = self._chunks
        self._chunks = []
        threading.Thread(target=self._transcribe_and_send,
                         args=(chunks, seconds), daemon=True,
                         name="VoiceTranscribe").start()

    def _transcribe_and_send(self, chunks, seconds):
        import numpy as np
        audio = np.frombuffer(b"".join(chunks), dtype=np.int16).astype(np.float32) / 32768.0
        if audio.size < SAMPLE_RATE // 4:
            self._set("error", "too short", 2.0)
            return
        t0 = time.time()
        try:
            segments, _info = self._model.transcribe(
                audio, language="en", beam_size=1, vad_filter=True)
            text = " ".join(s.text.strip() for s in segments).strip()
        except Exception as e:  # noqa: BLE001
            self._set("error", "transcribe: %s" % str(e)[:50], 5.0)
            self.on_event({"type": "voice", "event": "transcribe_failed", "error": str(e)})
            return
        took = time.time() - t0
        if not text:
            self._set("error", "heard nothing", 2.0)
            self.on_event({"type": "voice", "event": "empty", "seconds": round(seconds, 2)})
            return
        ok, msg = deliver_text(
            self.cfg.get("voice_target_title", "Claude"), text,
            bool(self.cfg.get("voice_send_enter", True)),
            bool(self.cfg.get("voice_return_focus", True)))
        self.on_event({"type": "voice", "event": "sent" if ok else "deliver_failed",
                       "text": text, "seconds": round(seconds, 2),
                       "transcribe_s": round(took, 2), "detail": msg})
        if ok:
            self._set("sent", "sent: " + (text if len(text) < 40 else text[:37] + "..."), 3.0)
        else:
            self._set("error", msg, 5.0)

    # ---- overlay readout ----------------------------------------------------
    def footer_text(self, hotkey):
        """(text, style) for the footer band. style: 'idle'|'live'|'note'."""
        now = time.time()
        if self.state == "listening":
            return "%s send   %d:%02d" % (hotkey, *divmod(int(now - self._t_start), 60)), "live"
        if self.state == "transcribing":
            return "transcribing...", "note"
        if self.state == "loading":
            return "%s voice (loading model)" % hotkey, "idle"
        if self.message and now < self.message_until:
            return self.message, "note"
        if self.state in ("sent", "error") and now >= self.message_until:
            self._set("idle")
        return "%s voice" % hotkey, "idle"

    def close(self):
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # noqa: BLE001
                pass
            self._stream = None
