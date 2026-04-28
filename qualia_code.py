# Qualia
import time, math, os, json, board, busio, displayio, terminalio, vectorio, rtc
import neopixel
from adafruit_display_text import label
from adafruit_bitmap_font import bitmap_font
from adafruit_qualia.displays.round40 import Round40
from adafruit_qualia.peripherals import Peripherals

# ── Constants ──────────────────────────────────────────────────────────────
FRAME_PERIOD = 0.082   # 2× dot-clock period (~12 fps)
TEST_SPEED   = 10      # wake ramp speed multiplier in test mode
CX, CY       = 360, 360

PIX_CENTER = 0
PIX_INNER  = list(range(1, 7))
PIX_OUTER  = list(range(7, 19))

S_IDLE       = "IDLE"
S_WAKE_RAMP  = "WAKE_RAMP"
S_ALARMING   = "ALARMING"
S_SNOOZED    = "SNOOZED"
S_DISMISSED  = "DISMISSED"
S_TEST_WAKE  = "TEST_WAKE"
S_TEST_ALARM = "TEST_ALARM"

# ── Display ────────────────────────────────────────────────────────────────
displayio.release_displays()
_drv = Round40()
_drv.init()
display = _drv.display
display.auto_refresh = False
_peripherals = Peripherals()
_peripherals.backlight = True

# ── NeoPixels ──────────────────────────────────────────────────────────────
pixels = neopixel.NeoPixel(
    board.A0, 19, pixel_order=neopixel.GRBW, auto_write=False, brightness=1.0
)
pixels.fill((0, 0, 0, 0))
pixels.show()

# ── UART ───────────────────────────────────────────────────────────────────
uart     = busio.UART(board.TX, board.A1, baudrate=115200, timeout=0,
                      receiver_buffer_size=512)
uart_buf        = b""
in_cfg          = False
_config_received = False

def send(msg):
    uart.write((msg + "\n").encode())

# ── Config helpers (defined early — used by web server routes) ──────────────
def deep_update(target, source):
    for k, v in source.items():
        if isinstance(v, dict) and isinstance(target.get(k), dict):
            deep_update(target[k], v)
        else:
            target[k] = v

def _set_nested(d, keys, val):
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    try:    val = int(val)
    except ValueError:
        try:    val = float(val)
        except ValueError: pass
    d[keys[-1]] = val

def apply_cfg_line(line):
    if "=" not in line:
        return
    k, v = line.split("=", 1)
    _set_nested(cfg, k.split("."), v)

def save_cfg():
    try:
        with open("/alarm_config.json", "w") as f:
            json.dump(cfg, f)
    except OSError:
        pass  # CIRCUITPY may be read-only

def _active_alarm():
    """Return (key, alarm_dict) for the highest-priority alarm that applies today,
    or (None, None) if nothing is scheduled.
    Priority: oneoff > weekday > weekend."""
    t  = time.localtime()
    wd = t.tm_wday  # 0=Mon … 6=Sun
    alarms = cfg.get("alarms", {})

    # oneoff: must be enabled and date must match today
    oo = alarms.get("oneoff", {})
    if oo.get("enabled"):
        try:
            yy, mm, dd = (int(x) for x in oo["date"].split("-"))
            if (yy, mm, dd) == (t.tm_year, t.tm_mon, t.tm_mday):
                return "oneoff", oo
        except (KeyError, ValueError):
            pass

    if wd < 5:
        wk = alarms.get("weekday", {})
        if wk.get("enabled"):
            return "weekday", wk
    else:
        we = alarms.get("weekend", {})
        if we.get("enabled"):
            return "weekend", we

    return None, None

def push_active_alarm_audio(alarm):
    """Send the active alarm's audio settings to S2 Mini before play commands."""
    if alarm is None:
        return
    wake  = alarm.get("wake",  {})
    ala   = alarm.get("alarm", {})
    wa    = wake.get("audio",  {})
    aa    = ala.get("audio",   {})
    send(f"SET audio.wake_song={wa.get('song', '')}")
    send(f"SET audio.wake_max_volume={wa.get('max_volume', 0.6)}")
    send(f"SET audio.volume_ramp_end={wa.get('volume_ramp_end', 0.7)}")
    send(f"SET audio.alarm_song={aa.get('song', '')}")
    send(f"SET audio.alarm_volume={aa.get('volume', 0.9)}")

def _push_dict_to_s2(d, prefix=""):
    """Recursively walk cfg dict and send SET key=val for every leaf to S2 Mini."""
    for k, v in d.items():
        key = f"{prefix}.{k}" if prefix else str(k)
        if isinstance(v, dict):
            _push_dict_to_s2(v, key)
        elif isinstance(v, bool):
            send(f"SET {key}={1 if v else 0}")
        else:
            send(f"SET {key}={v}")

# ── Load config ────────────────────────────────────────────────────────────
_DEFAULT_ALARM = {
    "enabled": True, "hour": 7, "minute": 0,
    "wake": {
        "enabled": True, "ramp_minutes": 10,
        "audio":    {"enabled": True, "song": "/sd/orchestral/morning_mood_short.wav",
                     "max_volume": 0.6, "volume_ramp_end": 0.7},
        "lighting": {"enabled": True, "direction": "sunrise_out", "max_brightness": 0.8},
    },
    "alarm": {
        "snooze_minutes": 9, "max_snoozes": 3,
        "audio":    {"enabled": True, "song": "/sd/songs/eye_of_the_tiger.wav", "volume": 0.9},
        "lighting": {"enabled": True, "effect": "PULSE", "brightness": 0.8,
                     "color": "warm_white", "speed": "medium"},
    },
}
_DEFAULT_CFG = {
    "alarms": {
        "weekday": dict(_DEFAULT_ALARM),
        "weekend": dict(_DEFAULT_ALARM),
        "oneoff":  dict(_DEFAULT_ALARM, enabled=False, date="2026-01-01"),
    },
    "global": {"display_timeout_s": 10},
}
try:
    with open("/alarm_config.json") as f:
        cfg = json.load(f)
    _config_received = True   # saved config is valid — show IP without waiting for S2
except (OSError, ValueError):
    cfg = _DEFAULT_CFG

# ── WiFi / NTP ─────────────────────────────────────────────────────────────
_pool           = None
_ntp            = None
_last_ntp       = 0.0
_wifi_connected = False
_local_ip       = None

try:
    import wifi, socketpool, adafruit_ntp
    _ssid = os.getenv("WIFI_SSID", "")
    _pw   = os.getenv("WIFI_PASSWORD", "")
    _tz   = int(os.getenv("TIMEZONE_OFFSET", "0"))
    if _ssid:
        wifi.radio.connect(_ssid, _pw)
        _pool = socketpool.SocketPool(wifi.radio)
        _ntp  = adafruit_ntp.NTP(_pool, tz_offset=_tz, cache_seconds=3600)
        rtc.RTC().datetime = _ntp.datetime
        _last_ntp       = time.monotonic()
        _wifi_connected = True
        _local_ip       = str(wifi.radio.ipv4_address)
        print(f"WiFi: {_local_ip}")
        send("REQUEST_CONFIG")  # re-request in case boot CONFIG was dropped during WiFi setup
except Exception as e:
    print(f"WiFi failed: {e}")

# ── Web server ─────────────────────────────────────────────────────────────
_server = None
if _wifi_connected and _pool:
    try:
        from adafruit_httpserver import Server, Request, Response, POST

        _server = Server(_pool)

        @_server.route("/")
        def _r_index(req):
            with open("/index.html", "rb") as f:
                body = f.read()
            return Response(req, body, content_type="text/html")

        @_server.route("/sounds.json")
        def _r_sounds(req):
            with open("/sounds.json", "rb") as f:
                body = f.read()
            return Response(req, body, content_type="application/json")

        @_server.route("/config.json")
        def _r_config(req):
            body = json.dumps(cfg).encode()
            return Response(req, body, content_type="application/json")

        @_server.route("/save", POST)
        def _r_save(req):
            try:
                body = req.body
                if isinstance(body, (bytes, bytearray)):
                    body = body.decode()
                new_cfg = json.loads(body)
                deep_update(cfg, new_cfg)
                save_cfg()
                _push_dict_to_s2(new_cfg)
                send("SAVE_CONFIG")
                _, active = _active_alarm()
                push_active_alarm_audio(active)
                return Response(req, b'{"ok":true}', content_type="application/json")
            except Exception as e:
                return Response(req, f'{{"error":"{e}"}}'.encode(),
                                content_type="application/json")

        _server.start(str(wifi.radio.ipv4_address), port=80)
        print(f"Web server: http://{wifi.radio.ipv4_address}")

        try:
            import mdns as _mdns_mod
            _mdns = _mdns_mod.Server(wifi.radio)
            _mdns.hostname = "robot-alarm"
            _mdns.advertise_service(service_type="_http", protocol="_tcp", port=80)
            print("mDNS: http://robot-alarm.local")
        except Exception:
            pass

    except Exception as e:
        print(f"Web server failed: {e}")
        _server = None

# ── Color palette (RGBW) ───────────────────────────────────────────────────
COLORS = {
    "warm_white": (180,  80,   0, 200),
    "cool_white": ( 80,  90, 120, 220),
    "pure_white": (  0,   0,   0, 255),
    "red":        (255,   0,   0,   0),
    "orange":     (255,  60,   0,   0),
    "amber":      (255, 120,   0,   0),
    "yellow":     (200, 180,   0,   0),
    "green":      (  0, 200,   0,   0),
    "cyan":       (  0, 180, 180,   0),
    "blue":       (  0,   0, 255,   0),
    "purple":     (150,   0, 200,   0),
    "magenta":    (220,   0, 150,   0),
}

def get_color(name):
    return COLORS.get(name, COLORS["warm_white"])

def scale_color(c, f):
    return tuple(min(255, int(v * f)) for v in c)

def lerp_color(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(4))

# Wake ramp color waypoints: deep red-orange → orange → warm yellow → warm white
_RC = [(200, 40, 0, 0), (220, 110, 0, 0), (200, 150, 0, 20), (80, 50, 0, 230)]

def ramp_color(t):
    if t < 0.33:  return lerp_color(_RC[0], _RC[1], t / 0.33)
    if t < 0.66:  return lerp_color(_RC[1], _RC[2], (t - 0.33) / 0.33)
    return lerp_color(_RC[2], _RC[3], (t - 0.66) / 0.34)

# ── NeoPixel effects ───────────────────────────────────────────────────────
_SPEED = {
    "PULSE":     {"slow": 6.0,  "medium": 3.0,  "fast": 1.5 },
    "CHASE":     {"slow": 3.0,  "medium": 1.5,  "fast": 0.75},
    "WAVE":      {"slow": 1.5,  "medium": 0.6,  "fast": 0.25},
    "DUAL_SPIN": {"slow": 3.0,  "medium": 1.5,  "fast": 0.75},
    "FLASH":     {"slow": 2.0,  "medium": 1.0,  "fast": 0.5 },
}
_eph = 0.0

def _neo_off():
    pixels.fill((0, 0, 0, 0))

def neo_wake_ramp(t, direction, max_br):
    _neo_off()
    col   = ramp_color(t)
    rings = [[PIX_CENTER], PIX_INNER, PIX_OUTER]
    if direction == "sunrise_in":
        rings = [PIX_OUTER, PIX_INNER, [PIX_CENTER]]
    thresholds = [0.33, 0.66, 1.0]
    for idx, ring in enumerate(rings):
        ph_start = thresholds[idx - 1] if idx > 0 else 0.0
        ph_end   = thresholds[idx]
        if t < ph_start:
            continue
        br = max_br if t >= ph_end else ((t - ph_start) / (ph_end - ph_start)) * max_br * 0.75
        c  = scale_color(col, br)
        for i in ring:
            pixels[i] = c

def neo_alarm_effect(dt):
    global _eph
    n   = _cur_alarm.get("alarm", {}).get("lighting", {})
    ef  = n.get("effect", "PULSE")
    br  = n.get("brightness", 0.8)
    c1  = get_color(n.get("color",  "warm_white"))
    c2  = get_color(n.get("color2", "blue"))
    sp  = n.get("speed", "medium")
    per = _SPEED.get(ef, {}).get(sp, 3.0)
    _eph = (_eph + dt) % (per * 1000)
    ph   = _eph % per

    if ef == "SOLID":
        s = scale_color(c1, br)
        for i in range(19): pixels[i] = s

    elif ef == "PULSE":
        lv = (math.sin(2 * math.pi * ph / per) + 1) / 2
        s  = scale_color(c1, br * (0.15 + lv * 0.85))
        for i in range(19): pixels[i] = s

    elif ef == "CHASE":
        dim = scale_color(c1, br * 0.15)
        for i in range(19): pixels[i] = dim
        pixels[PIX_OUTER[int((ph / per) * 12) % 12]] = scale_color(c1, br)

    elif ef == "SUNRISE":
        s = scale_color(_RC[3], br)
        for i in range(19): pixels[i] = s

    elif ef == "WAVE":
        _neo_off()
        t = (ph % per) / per
        if t < 0.33:
            pixels[PIX_CENTER] = scale_color(c1, br * (t / 0.33))
        elif t < 0.66:
            pixels[PIX_CENTER] = scale_color(c1, br)
            s = scale_color(c1, br * ((t - 0.33) / 0.33))
            for i in PIX_INNER: pixels[i] = s
        else:
            pixels[PIX_CENTER] = scale_color(c1, br)
            for i in PIX_INNER: pixels[i] = scale_color(c1, br)
            s = scale_color(c1, br * ((t - 0.66) / 0.34))
            for i in PIX_OUTER: pixels[i] = s

    elif ef == "DUAL_SPIN":
        _neo_off()
        pixels[PIX_OUTER[int((ph / per) * 12) % 12]] = scale_color(c1, br)
        pixels[PIX_INNER[(6 - int((ph / per) * 6) % 6) % 6]] = scale_color(c2, br)

    elif ef == "FLASH":
        if (ph % per) / per < 0.5:
            s = scale_color(c1, br)
            pixels[PIX_CENTER] = s
            for i in PIX_INNER: pixels[i] = s
            for i in PIX_OUTER: pixels[i] = (0, 0, 0, 0)
        else:
            s = scale_color(c2, br)
            pixels[PIX_CENTER] = (0, 0, 0, 0)
            for i in PIX_INNER: pixels[i] = (0, 0, 0, 0)
            for i in PIX_OUTER: pixels[i] = s

# ── Display scene ──────────────────────────────────────────────────────────
_P_BG   = displayio.Palette(1); _P_BG[0]   = 0x050A14
_P_FACE = displayio.Palette(1); _P_FACE[0] = 0x0D1E2E

scene = displayio.Group()
scene.append(vectorio.Circle(pixel_shader=_P_BG,   radius=362, x=CX, y=CY))
scene.append(vectorio.Circle(pixel_shader=_P_FACE, radius=315, x=CX, y=CY))

_DAYS   = ("Mon","Tue","Wed","Thu","Fri","Sat","Sun")
_MONTHS = ("","Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec")

_lbl_time  = label.Label(terminalio.FONT, text="--:--",  color=0x5588AA,
                          scale=10, anchor_point=(0.5, 0.5),
                          anchored_position=(CX, CY - 30))
_lbl_date  = label.Label(terminalio.FONT, text="--- --", color=0x2A3D4E,
                          scale=4,  anchor_point=(0.5, 0.5),
                          anchored_position=(CX, CY + 100))
_lbl_state = label.Label(terminalio.FONT, text="",       color=0x2A3D4E,
                          scale=2,  anchor_point=(0.5, 0.5),
                          anchored_position=(CX, CY + 174))
scene.append(_lbl_time)
scene.append(_lbl_date)
scene.append(_lbl_state)
display.root_group = scene

_icon_font      = bitmap_font.load_font("/material_icons_32.bdf")
_lbl_alarm_icon = label.Label(_icon_font, text=chr(0xE856), color=0x1E2D3A,
                               anchor_point=(0.5, 0.5),
                               anchored_position=(CX - 40, CY - 140))
scene.append(_lbl_alarm_icon)

# AUTO mode indicator: box outline + "AUTO" text + slash overlay
# Slash drawn in face bg color — "erases" through the box when visible (always-on mode)
_ABOX_X = CX + 24    # box top-left x (centered at CX+40)
_ABOX_Y = CY - 151   # box top-left y (centered at CY-140)
_ABOX_W = 32
_ABOX_H = 22
_P_ABOX  = displayio.Palette(1); _P_ABOX[0]  = 0x335566
_P_SLASH = displayio.Palette(1); _P_SLASH[0] = 0x0D1E2E  # face bg = erases box

scene.append(vectorio.Rectangle(pixel_shader=_P_ABOX, width=_ABOX_W, height=2,
                                 x=_ABOX_X, y=_ABOX_Y))
scene.append(vectorio.Rectangle(pixel_shader=_P_ABOX, width=_ABOX_W, height=2,
                                 x=_ABOX_X, y=_ABOX_Y + _ABOX_H - 2))
scene.append(vectorio.Rectangle(pixel_shader=_P_ABOX, width=2, height=_ABOX_H,
                                 x=_ABOX_X, y=_ABOX_Y))
scene.append(vectorio.Rectangle(pixel_shader=_P_ABOX, width=2, height=_ABOX_H,
                                 x=_ABOX_X + _ABOX_W - 2, y=_ABOX_Y))
_lbl_auto = label.Label(terminalio.FONT, text="AUTO", color=0x335566,
                         scale=1, anchor_point=(0.5, 0.5),
                         anchored_position=(CX + 40, CY - 140))
scene.append(_lbl_auto)

_slash_group = displayio.Group()
_slash_group.append(vectorio.Polygon(
    pixel_shader=_P_SLASH, x=_ABOX_X, y=_ABOX_Y,
    points=[(1, _ABOX_H-2), (5, _ABOX_H-2), (_ABOX_W-2, 1), (_ABOX_W-6, 1)],
))
_slash_group.hidden = True  # shown when always-on, hidden when auto mode
scene.append(_slash_group)

_prev_disp = ("", "", "", "", "")

def update_display_labels(cur_state, snooze_cnt):
    global _prev_disp
    t  = time.localtime()
    ts = f"{t.tm_hour:02d}:{t.tm_min:02d}"
    ds = f"{_DAYS[t.tm_wday]} {_MONTHS[t.tm_mon]} {t.tm_mday}"

    # auxiliary text — icons now handle alarm state and display mode
    if cur_state == S_SNOOZED:
        left = _cur_alarm.get("alarm", {}).get("max_snoozes", 3) - snooze_cnt
        ss = f"{left} left"
    elif cur_state in (S_TEST_WAKE, S_TEST_ALARM):
        ss = f"TEST {cur_state[5:]}"
    elif cur_state == S_IDLE:
        ss = _local_ip if _config_received else ""
    else:
        ss = ""

    # alarm state icon
    if cur_state in (S_ALARMING, S_TEST_ALARM):
        ai, ac = chr(0xE7F7), 0x557744   # notifications_active
    elif cur_state == S_SNOOZED:
        ai, ac = chr(0xE7F8), 0x446688   # notifications_paused
    elif _active_alarm()[1] is not None and alarm_fired != (t.tm_year, t.tm_mon, t.tm_mday):
        ai, ac = chr(0xE855), 0x336688   # alarm — armed for today
    else:
        ai, ac = chr(0xE856), 0x1E2D3A   # alarm_off

    cur = (ts, ds, ss, ai, disp_always_on)
    if cur == _prev_disp:
        return False
    if ts != _prev_disp[0]: _lbl_time.text  = ts
    if ds != _prev_disp[1]: _lbl_date.text  = ds
    if ss != _prev_disp[2]: _lbl_state.text = ss
    if ai != _prev_disp[3]:
        _lbl_alarm_icon.text  = ai
        _lbl_alarm_icon.color = ac
    if disp_always_on != _prev_disp[4]:
        _slash_group.hidden = not disp_always_on
    _prev_disp = cur
    return True

# ── State machine ──────────────────────────────────────────────────────────
state         = S_IDLE
state_enter_t = time.monotonic()
snooze_count  = 0
snooze_start  = 0.0
display_on    = True
display_on_t  = 0.0
alarm_fired   = None   # (year, mon, day) — prevents re-firing same alarm twice
_last_vol     = -1.0
disp_always_on  = True  # default: display always on; long press toggles
_disp_wake_lock = 0.0   # monotonic time before which wake commands are ignored
_cur_alarm      = {}    # alarm dict currently executing (set on state entry)
_last_sync_day  = time.localtime().tm_mday  # init to today; CONFIG_END handles boot push

def enter_state(new_state):
    global state, state_enter_t, snooze_count, snooze_start
    global display_on, display_on_t, alarm_fired, _eph, _last_vol, _cur_alarm
    state         = new_state
    state_enter_t = time.monotonic()
    _eph          = 0.0
    _last_vol     = -1.0
    send(f"STATE {new_state}")

    if new_state == S_IDLE:
        send("STOP")
        _neo_off(); pixels.show()
        display_on   = disp_always_on
        display_on_t = time.monotonic()
        snooze_count = 0

    elif new_state == S_WAKE_RAMP:
        _, _cur_alarm = _active_alarm()
        _cur_alarm = _cur_alarm or {}
        push_active_alarm_audio(_cur_alarm)
        send("PLAY WAKE")

    elif new_state == S_TEST_WAKE:
        _, _cur_alarm = _active_alarm()
        _cur_alarm = _cur_alarm or cfg.get("alarms", {}).get("weekday", {})
        push_active_alarm_audio(_cur_alarm)
        send("PLAY WAKE")

    elif new_state == S_ALARMING:
        if state not in (S_WAKE_RAMP, S_TEST_WAKE):
            # overdue boot or wake-disabled alarm — capture alarm now
            _, _cur_alarm = _active_alarm()
            _cur_alarm = _cur_alarm or {}
            push_active_alarm_audio(_cur_alarm)
        send("PLAY ALARM")
        display_on = True

    elif new_state == S_TEST_ALARM:
        if not _cur_alarm:
            _, _cur_alarm = _active_alarm()
            _cur_alarm = _cur_alarm or cfg.get("alarms", {}).get("weekday", {})
        push_active_alarm_audio(_cur_alarm)
        send("PLAY ALARM")
        display_on = True

    elif new_state == S_SNOOZED:
        snooze_count += 1
        snooze_start  = time.monotonic()
        send("PLAY SNOOZE")
        display_on = True

    elif new_state == S_DISMISSED:
        send("STOP")
        _neo_off(); pixels.show()
        display_on = True
        t = time.localtime()
        alarm_fired = (t.tm_year, t.tm_mon, t.tm_mday)
        # Auto-disable one-off after it fires
        oo = cfg.get("alarms", {}).get("oneoff", {})
        if oo.get("enabled"):
            try:
                yy, mm, dd = (int(x) for x in oo["date"].split("-"))
                if (yy, mm, dd) == (t.tm_year, t.tm_mon, t.tm_mday):
                    oo["enabled"] = False
                    save_cfg()
            except (KeyError, ValueError):
                pass

# ── Alarm scheduling ───────────────────────────────────────────────────────
def _now_s():
    t = time.localtime()
    return t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec

def check_wake_ramp():
    _, alarm = _active_alarm()
    if alarm is None:
        return False
    t = time.localtime()
    if alarm_fired == (t.tm_year, t.tm_mon, t.tm_mday):
        return False
    wake = alarm.get("wake", {})
    if not wake.get("enabled", True):
        return False
    alarm_s = alarm["hour"] * 3600 + alarm["minute"] * 60
    ramp_s  = wake.get("ramp_minutes", 10) * 60
    ns      = _now_s()
    return (alarm_s - ramp_s) <= ns < alarm_s

def check_alarm_time():
    """At or past alarm time (within 30 min) — handles overdue boot and wake-disabled alarms."""
    _, alarm = _active_alarm()
    if alarm is None:
        return False
    t = time.localtime()
    if alarm_fired == (t.tm_year, t.tm_mon, t.tm_mday):
        return False
    alarm_s = alarm["hour"] * 3600 + alarm["minute"] * 60
    ns      = _now_s()
    return alarm_s <= ns < alarm_s + 1800

# ── Main loop ──────────────────────────────────────────────────────────────
_last_frame     = time.monotonic()
_last_heartbeat = time.monotonic()
HEARTBEAT_INTERVAL = 10.0
enter_state(S_IDLE)

while True:
    now = time.monotonic()
    dt  = now - _last_frame

    # ── Web server poll ──────────────────────────────────────────────────────
    if _server:
        try:
            _server.poll()
        except Exception:
            pass

    # ── UART receive ────────────────────────────────────────────────────────
    chunk = uart.read(64)
    if chunk:
        uart_buf += chunk
    while b"\n" in uart_buf:
        raw, uart_buf = uart_buf.split(b"\n", 1)
        cmd = raw.decode().strip()
        if cmd and not in_cfg:
            print(f"Qualia | ← {cmd}")

        if cmd == "CONFIG_START":
            in_cfg = True
        elif cmd == "CONFIG_END":
            in_cfg = False
            _config_received = True  # noqa — module-level assignment, not local
            _, _a = _active_alarm()
            push_active_alarm_audio(_a)
        elif in_cfg:
            apply_cfg_line(cmd)
        elif cmd == "GET_STATE":
            send(f"STATE {state}")
        elif cmd == "DISP_ALWAYS_ON":
            disp_always_on = True
            display_on     = True
        elif cmd == "DISP_AUTO":
            disp_always_on  = False
            display_on      = False
            display_on_t    = now
            _disp_wake_lock = now + 10.0
        elif state in (S_TEST_WAKE, S_TEST_ALARM):
            if cmd in ("DISPLAY", "MOTION", "SNOOZE", "DISMISS"):
                enter_state(S_IDLE)
        else:
            if cmd in ("DISPLAY", "MOTION"):
                if now >= _disp_wake_lock:
                    display_on   = True
                    display_on_t = now
            elif cmd == "SNOOZE" and state == S_ALARMING:
                if snooze_count < _cur_alarm.get("alarm", {}).get("max_snoozes", 3):
                    enter_state(S_SNOOZED)
            elif cmd == "DISMISS" and state not in (S_IDLE, S_DISMISSED):
                enter_state(S_DISMISSED)
            elif cmd == "TEST_WAKE" and state == S_IDLE:
                enter_state(S_TEST_WAKE)
            elif cmd == "TEST_ALARM" and state in (S_IDLE, S_TEST_WAKE):
                enter_state(S_TEST_ALARM)

    # ── Alarm scheduling (IDLE only) ────────────────────────────────────────
    if state == S_IDLE:
        if check_wake_ramp():
            enter_state(S_WAKE_RAMP)
        elif check_alarm_time():
            enter_state(S_ALARMING)

    # ── State updates ────────────────────────────────────────────────────────
    if state in (S_WAKE_RAMP, S_TEST_WAKE):
        speed    = TEST_SPEED if state == S_TEST_WAKE else 1.0
        elapsed  = (now - state_enter_t) * speed
        wake_cfg = _cur_alarm.get("wake", {})
        ramp_dur = wake_cfg.get("ramp_minutes", 10) * 60
        ramp_t   = min(1.0, elapsed / ramp_dur)

        wl = wake_cfg.get("lighting", {})
        neo_wake_ramp(ramp_t,
                      wl.get("direction", "sunrise_out"),
                      wl.get("max_brightness", 0.8))
        pixels.show()

        wa      = wake_cfg.get("audio", {})
        vol_end = wa.get("volume_ramp_end", 0.7)
        wmax    = wa.get("max_volume", 0.6)
        vol     = round(min(wmax, ramp_t / vol_end * wmax) if ramp_t < vol_end else wmax, 3)
        if abs(vol - _last_vol) >= 0.005:
            send(f"VOL {vol:.3f}")
            _last_vol = vol

        if ramp_t >= 1.0:
            enter_state(S_TEST_ALARM if state == S_TEST_WAKE else S_ALARMING)

    elif state in (S_ALARMING, S_TEST_ALARM):
        neo_alarm_effect(dt)
        pixels.show()

    elif state == S_SNOOZED:
        wake_cfg = _cur_alarm.get("wake", {})
        wl = wake_cfg.get("lighting", {})
        neo_wake_ramp(1.0,
                      wl.get("direction", "sunrise_out"),
                      wl.get("max_brightness", 0.8))
        pixels.show()
        if now - snooze_start >= _cur_alarm.get("alarm", {}).get("snooze_minutes", 9) * 60:
            enter_state(S_ALARMING)

    elif state == S_DISMISSED:
        if now - state_enter_t >= 3.0:
            enter_state(S_IDLE)

    # ── Display timeout ──────────────────────────────────────────────────────
    if not disp_always_on and state in (S_IDLE, S_WAKE_RAMP, S_TEST_WAKE) and display_on:
        if now - display_on_t >= cfg.get("global", {}).get("display_timeout_s", 10):
            display_on = False

    # ── Display render (gated on FRAME_PERIOD + dirty flag) ──────────────────
    if dt >= FRAME_PERIOD:
        should_show = display_on or state in (S_ALARMING, S_SNOOZED,
                                              S_TEST_ALARM, S_DISMISSED)
        dirty = update_display_labels(state, snooze_count)
        if scene.hidden == should_show:
            scene.hidden = not should_show
            _peripherals.backlight = should_show
            dirty = True
        if dirty:
            display.refresh()
        _last_frame = now

    # ── Heartbeat ────────────────────────────────────────────────────────────
    if now - _last_heartbeat >= HEARTBEAT_INTERVAL:
        print(f"Qualia | {state} | disp_always_on={disp_always_on}")
        _last_heartbeat = now

    # ── NTP re-sync (hourly) ─────────────────────────────────────────────────
    if _ntp and now - _last_ntp >= 3600:
        try:
            rtc.RTC().datetime = _ntp.datetime
            _last_ntp = now
        except Exception:
            pass

    # ── Daily audio sync to S2 Mini (on day change) ──────────────────────────
    _td = time.localtime().tm_mday
    if _td != _last_sync_day:
        _, _next = _active_alarm()
        push_active_alarm_audio(_next)
        _last_sync_day = _td

    time.sleep(0.005)
