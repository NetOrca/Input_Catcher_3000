# Input_Catcher_3000 -- Research mode

Research mode turns the overlay into a measurement instrument for
reverse-engineering game mechanics from OBS recordings. It is OFF by
default; the plain streaming overlay is unchanged.

Turn it on in the settings screen under **Research mode**. Every session
then writes one log file to `research_logs\` next to the exe (or the
folder you pick), named `research_YYYYMMDD_HHMMSS.jsonl`.

## What you get

* **Event log** -- every key/mouse press and release with a millisecond
  timestamp, hold duration on release, wheel ticks, mouse position
  samples, and raw mouse deltas (what a game with a locked cursor
  actually reads).
* **On-screen clock** -- `mm:ss.mmm` at the top of the overlay. It shows
  the SAME clock the log uses, so reading the clock off any video frame
  anchors the whole log to the footage (±1 frame).
* **Marker key** (default F8) -- writes a numbered marker to the log and
  shows `MARK n` in the overlay. Press it at the start of every trial.
* **Record key** (default F9) -- with OBS sync on, starts/stops OBS
  recording from the same keyboard, and the log records when.
* **OBS sync** -- every marker also asks OBS for its current recording
  timecode and writes it next to the marker. That makes log-to-video
  alignment exact, no clock-reading needed.

## OBS sync setup (OBS 28 or newer)

1. In OBS: **Tools > WebSocket Server Settings**, tick **Enable WebSocket
   server**. Port 4455 is the default. Copy the password (or turn
   authentication off).
2. In Input_Catcher_3000 settings: tick **OBS sync**, enter the port and
   password, click **Test OBS**. It should say `OK: OBS <version> ...`.
3. Start OBS before the overlay, or it will reconnect on the next marker.

Windows Firewall may ask about OBS the first time the server is enabled.
The overlay talks to OBS over localhost only, which works even if you
click Cancel.

## Log format

One JSON object per line. Every line has `"t"` = milliseconds since the
session started (the overlay clock shows the same number) and `"type"`.

| type | fields | meaning |
|---|---|---|
| `session` | `version`, `wall_start`, `wall_start_iso`, `screen_w/h`, hotkeys, `mouse_sample_hz`, `obs_sync` | first line |
| `raw_mouse_registered` | `ok`, `log_path` | raw-input delta capture available |
| `down` | `dev` (`kb`/`mouse`), `key`, `vk` | press (auto-repeat is NOT logged twice) |
| `up` | `dev`, `key`, `vk`, `hold_ms` | release; `hold_ms` = time since the matching `down` |
| `wheel` | `delta` | +120 per notch up, -120 per notch down |
| `mouse` | `x`, `y`, `dx`, `dy` | absolute cursor position, throttled to `mouse_sample_hz`, only when it moved |
| `mouse_raw` | `dx`, `dy`, `n`, `span_ms` | raw-input deltas summed over the sample interval (`n` = raw packets) |
| `marker` | `n`, `name` | marker key pressed; `name` = `<prefix>_<n>` |
| `obs_sync` | `marker`, `name`, `t` (marker time), `t_query`, `t_reply`, `obs_recording`, `obs_duration_ms`, `obs_timecode` | OBS record position at that marker |
| `obs` | `event` (`connected`, `connect_failed`, `status`, `record_toggled`, `skipped`, `error`), ... | OBS link state |
| `session_end` | | last line (only on a clean close) |

The marker key and record key are logged as ordinary `down`/`up` events
under their real key names (`F8`, `F9`) as well.

## Aligning the log to the video

With OBS sync: for any `obs_sync` line,
`video_ms = obs_duration_ms + (event_t - t_reply)` for every event in
that recording. Use the marker closest to the event. Round-trip
(`t_reply - t_query`) is under 1 ms on the same PC; OBS reports duration
at frame granularity, so expect ±1 frame.

Without OBS sync: find a frame where the overlay clock is readable,
note the video time of that frame and the clock value, and offset the
whole log by the difference. Accuracy is ±1 frame plus however
precisely you read the clock.

## Measuring things

Give each experiment its own marker. Vary ONE thing per experiment, five
trials each. Keep the HUD visible. Freeform play is fine for feel
questions but useless for measurement.

Typical extractions: `hold_ms` on the fire key vs. observed damage
number (draw curve); frames between `down` and the first visible
effect (input latency / animation wind-up); `mouse_raw` dx sum vs.
degrees turned on screen (sensitivity, zoom scaling); HUD bar width per
frame between two markers (drain / regen rates).

## Config keys (input_catcher_3000_config.json)

`research_mode`, `research_log_dir`, `marker_hotkey`, `record_hotkey`,
`marker_prefix` (default `trial`), `mouse_sample_hz` (default 60),
`obs_sync`, `obs_host`, `obs_port`, `obs_password`.

`Input_Catcher_3000.exe --no-gui` skips the settings screen and starts
straight from the saved config.
