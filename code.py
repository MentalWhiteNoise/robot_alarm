# code.py – Robot Alarm Clock
# Hardware: Adafruit Qualia ESP32-S3 + Round RGB TTL TFT Display 4" 720x720
# Requires: adafruit_display_text in /lib

import displayio
import terminalio
import time
import vectorio
from adafruit_display_text import label

import network
from alarm_state import AlarmClock
from adafruit_qualia.displays.round40 import Round40

# ── Display ───────────────────────────────────────────────────────────────────
displayio.release_displays()
_drv = Round40()
_drv.init()
display = _drv.display
display.auto_refresh = False

# TODO: to turn off the backlight, drive the BL pin low here.
# The Qualia ESP32-S3 backlight pin is TBD — add when wiring IR sensor.

_t = _drv._timings
_H = _t["width"]  + _t["hsync_back_porch"]  + _t["hsync_front_porch"]  + _t["hsync_pulse_width"]
_V = _t["height"] + _t["vsync_back_porch"]  + _t["vsync_front_porch"]  + _t["vsync_pulse_width"]
FRAME_PERIOD = (_H * _V / _t["frequency"]) * 3   # ~0.123 s ≈ 8 fps

CX, CY = 360, 360

# ── Colors (all below 0x88 per display flicker ceiling) ──────────────────────
C_BG     = 0x000000
C_TIME   = 0x7AAACE   # light blue — eye white color from robot palette
C_DATE   = 0x4A6880   # dimmer blue
C_STATUS = 0x2A4455   # dim, unobtrusive

# ── Scene ─────────────────────────────────────────────────────────────────────
def pal(c):
    p = displayio.Palette(1)
    p[0] = c
    return p

scene = displayio.Group()
scene.append(vectorio.Circle(pixel_shader=pal(C_BG), radius=362, x=CX, y=CY))

# Time label — "12:34" at scale 12 → ~72×168 px per char, ~360px wide for 5 chars
time_lbl = label.Label(
    terminalio.FONT, text="--:--", scale=12,
    color=C_TIME,
    anchor_point=(0.5, 0.5),
    anchored_position=(CX, CY - 60),
)

# AM/PM label — smaller, to the right of centre
ampm_lbl = label.Label(
    terminalio.FONT, text="--", scale=4,
    color=C_TIME,
    anchor_point=(0.5, 0.5),
    anchored_position=(CX + 200, CY - 120),
)

# Date label — "Mon Apr 12"
date_lbl = label.Label(
    terminalio.FONT, text="", scale=4,
    color=C_DATE,
    anchor_point=(0.5, 0.5),
    anchored_position=(CX, CY + 110),
)

# Status line — alarm mode / next alarm
status_lbl = label.Label(
    terminalio.FONT, text="", scale=2,
    color=C_STATUS,
    anchor_point=(0.5, 0.5),
    anchored_position=(CX, CY + 195),
)

# IP address — dim, near bottom; disappears once you've bookmarked it
ip_lbl = label.Label(
    terminalio.FONT, text="", scale=2,
    color=C_STATUS,
    anchor_point=(0.5, 0.5),
    anchored_position=(CX, CY + 240),
)

for lbl in (time_lbl, ampm_lbl, date_lbl, status_lbl, ip_lbl):
    scene.append(lbl)

display.root_group = scene

# ── Alarm clock + WiFi ────────────────────────────────────────────────────────
network.connect()   # non-blocking; sets RTC via NTP if WiFi available
ip_lbl.text = network.device_ip() or "no wifi"
clock = AlarmClock()

# ── Helpers ───────────────────────────────────────────────────────────────────
DAYS   = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

def _update_display(t):
    h24  = t.tm_hour
    mins = t.tm_min
    ampm = "AM" if h24 < 12 else "PM"
    h12  = h24 % 12
    if h12 == 0:
        h12 = 12

    time_lbl.text   = "{:02d}:{:02d}".format(h12, mins)
    ampm_lbl.text   = ampm
    date_lbl.text   = "{} {} {}".format(DAYS[t.tm_wday],
                                         MONTHS[t.tm_mon - 1],
                                         t.tm_mday)

    s = network.settings
    mode = s.get("alarm_mode", "off")
    if mode == "off":
        status_lbl.text = "alarm off"
    elif mode == "scheduled":
        wday = t.tm_wday
        import alarm_config as _cfg
        hm = s.get("alarm_weekday") if wday in _cfg.WEEKDAYS else s.get("alarm_weekend")
        status_lbl.text = "alarm {}:{:02d}".format(hm[0], hm[1]) if hm else "scheduled"
    elif mode == "custom":
        hm = s.get("alarm_oneoff")
        status_lbl.text = "alarm {}:{:02d}".format(hm[0], hm[1]) if hm else "custom"

# ── Main Loop ─────────────────────────────────────────────────────────────────
t_prev      = time.monotonic()
frame_timer = 0.0
last_min    = -1
dirty       = True

while True:
    now = time.monotonic()
    dt  = now - t_prev
    if dt > 0.1:
        dt = 0.1
    t_prev = now
    frame_timer += dt

    network.poll(dt)
    clock.tick(dt, wall_time=network.wall_time())

    # Update labels once per minute (or on first pass)
    t = time.localtime()
    if t.tm_min != last_min:
        last_min = t.tm_min
        _update_display(t)
        dirty = True

    if dirty and frame_timer >= FRAME_PERIOD:
        display.refresh()
        dirty       = False
        frame_timer = 0.0
