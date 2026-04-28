# network.py – WiFi, NTP, HTTP settings server
#
# Usage in code.py:
#   import network
#   network.connect()            # call once at startup
#   ...
#   while True:
#       ...
#       network.poll()           # call every loop — non-blocking
#       wall = network.wall_time()  # time.struct_time or None if not synced
#
# HTTP API (served at http://<DEVICE_HOSTNAME>.local or http://<ip>/)
#   GET  /              → settings web UI
#   GET  /api/status    → JSON: current time + alarm state
#   POST /api/settings  → JSON body: update alarm settings
#
# Settings are persisted to alarm_settings.json on CIRCUITPY.

import os
import json
import time
import rtc

# ── Defaults (used if alarm_settings.json is missing/corrupt) ─────────────────
_DEFAULTS = {
    "alarm_weekday": [7, 0],
    "alarm_weekend": [8, 30],
    "alarm_oneoff":  None,
    "alarm_mode":    "off",
}

SETTINGS_PATH = "/alarm_settings.json"

# ── Module state ──────────────────────────────────────────────────────────────
_pool    = None
_server  = None
_synced  = False          # True once NTP has set the RTC at least once
_ntp_retry_timer = 0.0   # seconds until next NTP attempt
NTP_RETRY_INTERVAL = 30 * 60   # retry every 30 min

settings = {}   # live settings dict — read by AlarmClock


# ── Settings persistence ──────────────────────────────────────────────────────

def load_settings():
    global settings
    try:
        with open(SETTINGS_PATH, "r") as f:
            settings = json.load(f)
        # Fill in any keys missing from an older file
        for k, v in _DEFAULTS.items():
            if k not in settings:
                settings[k] = v
    except Exception:
        settings = dict(_DEFAULTS)
    print("settings loaded:", settings)


def save_settings():
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(settings, f)
        print("settings saved")
    except Exception as e:
        print("settings save failed:", e)


# ── WiFi + NTP ────────────────────────────────────────────────────────────────

def connect():
    """
    Attempt WiFi connection and NTP sync at startup.
    Failures are silent — device works offline using RTC's current time.
    """
    global _pool, _server, _synced

    load_settings()

    try:
        import wifi
        import socketpool
        ssid = os.getenv("CIRCUITPY_WIFI_SSID", "")
        pwd  = os.getenv("CIRCUITPY_WIFI_PASSWORD", "")
        if not ssid:
            print("no WiFi credentials in settings.toml — skipping")
            return
        print("wifi: connecting to", ssid)
        wifi.radio.connect(ssid, pwd)
        _pool = socketpool.SocketPool(wifi.radio)
        print("wifi: connected, ip =", wifi.radio.ipv4_address)

        # mDNS — makes device reachable as http://robot.local
        try:
            import mdns
            hostname = os.getenv("DEVICE_HOSTNAME", "robot")
            mdns_server = mdns.Server(wifi.radio)
            mdns_server.hostname = hostname
            mdns_server.advertise_service(service_type="_http", protocol="_tcp", port=80)
            print("mdns: advertising as", hostname + ".local")
        except Exception as e:
            print("mdns unavailable:", e)

        _sync_ntp()
        _start_server()

    except Exception as e:
        print("wifi/network init failed:", e)


def _sync_ntp():
    global _synced, _ntp_retry_timer
    try:
        import adafruit_ntp
        tz_offset = int(os.getenv("TZ_OFFSET", "0"))
        ntp = adafruit_ntp.NTP(_pool, tz_offset=tz_offset, socket_timeout=5)
        rtc.RTC().datetime = ntp.datetime
        _synced = True
        _ntp_retry_timer = NTP_RETRY_INTERVAL
        t = rtc.RTC().datetime
        print("ntp: synced — {:02d}:{:02d}:{:02d}".format(t.tm_hour, t.tm_min, t.tm_sec))
    except Exception as e:
        print("ntp sync failed:", e)
        _ntp_retry_timer = 5 * 60   # retry in 5 min on failure


def _start_server():
    global _server
    try:
        from adafruit_httpserver import Server, Request, Response, GET, POST
        _server = Server(_pool, root_path=None, debug=False)

        # Register routes — equivalent to @server.route(...) decorators,
        # but works with a lazily-created server instance.

        def _index(request: Request):
            return Response(request, body=_UI_HTML, content_type="text/html")

        def _status(request: Request):
            t = rtc.RTC().datetime
            body = json.dumps({
                "time": "{:02d}:{:02d}:{:02d}".format(t.tm_hour, t.tm_min, t.tm_sec),
                "date": "{}-{:02d}-{:02d}".format(t.tm_year, t.tm_mon, t.tm_mday),
                "synced": _synced,
                "settings": settings,
            })
            return Response(request, body=body, content_type="application/json")

        def _set_settings(request: Request):
            try:
                data = json.loads(request.body)
                _apply_settings(data)
                save_settings()
                return Response(request, body='{"ok":true}',
                                content_type="application/json")
            except Exception as e:
                return Response(request,
                                body=json.dumps({"ok": False, "error": str(e)}),
                                content_type="application/json", status=(400, "Bad Request"))

        _server.route("/", GET)(_index)
        _server.route("/api/status", GET)(_status)
        _server.route("/api/settings", POST)(_set_settings)

        _server.start(port=80)
        import wifi
        print("http: server started at http://{}".format(wifi.radio.ipv4_address))
    except Exception as e:
        print("http server failed to start:", e)
        _server = None


def _apply_settings(data):
    """Validate and merge incoming settings dict into the live settings."""
    if "alarm_mode" in data:
        m = data["alarm_mode"]
        if m in ("off", "scheduled", "custom"):
            settings["alarm_mode"] = m

    for key in ("alarm_weekday", "alarm_weekend"):
        if key in data:
            v = data[key]
            if v is None:
                settings[key] = None
            elif isinstance(v, list) and len(v) == 2:
                h, m = int(v[0]), int(v[1])
                if 0 <= h <= 23 and 0 <= m <= 59:
                    settings[key] = [h, m]

    if "alarm_oneoff" in data:
        v = data["alarm_oneoff"]
        if v is None:
            settings["alarm_oneoff"] = None
        elif isinstance(v, list) and len(v) == 2:
            h, m = int(v[0]), int(v[1])
            if 0 <= h <= 23 and 0 <= m <= 59:
                settings["alarm_oneoff"] = [h, m]

    print("settings updated:", settings)


# ── Main loop integration ─────────────────────────────────────────────────────

HTTP_POLL_INTERVAL = 0.5   # seconds between HTTP socket checks
_http_poll_timer   = 0.0

def poll(dt=0.0):
    """Call once per main loop iteration. Handles HTTP requests + NTP re-sync."""
    global _ntp_retry_timer, _http_poll_timer, _server

    _http_poll_timer += dt
    if _server is not None and _http_poll_timer >= HTTP_POLL_INTERVAL:
        _http_poll_timer = 0.0
        try:
            _server.poll()   # routes handle request/response internally
        except Exception as e:
            print("http poll error:", e)
            # Socket is likely in a bad state — tear down and restart cleanly.
            try:
                _server.stop()
            except Exception:
                pass
            _server = None
            print("http: restarting server...")
            _start_server()

    if _pool is not None and not _synced:
        _ntp_retry_timer -= dt
        if _ntp_retry_timer <= 0:
            _sync_ntp()


def wall_time():
    """
    Return rtc.RTC().datetime if the clock has been synced, else None.
    AlarmClock.tick() accepts None and simply skips alarm-trigger checks.
    """
    if _synced:
        return rtc.RTC().datetime
    return None


def device_ip():
    """Return the device IP address string, or None if not connected."""
    try:
        import wifi
        ip = str(wifi.radio.ipv4_address)
        return ip if ip != "0.0.0.0" else None
    except Exception:
        return None


# ── Embedded web UI ──────────────────────────────────────────────────────────
# Single-page app — no external dependencies, works offline from the device.

_UI_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Robot Alarm</title>
<style>
  body{font-family:system-ui,sans-serif;background:#111;color:#eee;
       max-width:420px;margin:0 auto;padding:1rem}
  h1{font-size:1.3rem;margin:0 0 .25rem}
  #clock{font-size:2.5rem;font-weight:bold;letter-spacing:.05em;color:#7af}
  #sync{font-size:.75rem;color:#888;margin-bottom:1.2rem}
  section{background:#1e1e1e;border-radius:.5rem;padding:1rem;margin-bottom:1rem}
  h2{font-size:.85rem;text-transform:uppercase;letter-spacing:.1em;
     color:#888;margin:0 0 .75rem}
  label{display:flex;align-items:center;gap:.75rem;margin-bottom:.6rem;font-size:.9rem}
  label span{min-width:4.5rem;color:#aaa}
  input[type=time],select{
    background:#2a2a2a;border:1px solid #444;color:#eee;
    padding:.35rem .5rem;border-radius:.3rem;font-size:.95rem;
    transition:border-color .15s;
  }
  input[type=time]{flex:1}
  select{width:100%}
  .changed{border-color:#c80!important;color:#fd8}
  .hidden{display:none}
  .btns{display:flex;gap:.5rem;margin-top:.25rem}
  button{flex:1;padding:.75rem;border:none;border-radius:.4rem;
         font-size:1rem;cursor:pointer;transition:opacity .15s}
  #btn_save{background:#2255aa;color:#fff}
  #btn_save:active{background:#1a3d80}
  #btn_cancel{background:#333;color:#bbb}
  #btn_cancel:active{background:#222}
  button:disabled{opacity:.35;cursor:default}
  #msg{text-align:center;font-size:.85rem;color:#4c8;margin-top:.6rem;min-height:1.2em}
</style>
</head>
<body>
<h1>Robot Alarm</h1>
<div id="clock">--:--:--</div>
<div id="sync">connecting...</div>

<section>
  <h2>Mode</h2>
  <select id="mode" onchange="onModeChange();checkDirty()">
    <option value="off">Off</option>
    <option value="scheduled">Scheduled</option>
    <option value="custom">Custom</option>
  </select>
</section>

<section id="sec_scheduled" class="hidden">
  <h2>Schedule</h2>
  <label><span>Mon \u2013 Fri</span><input type="time" id="t_weekday" oninput="checkDirty()"></label>
  <label><span>Sat \u2013 Sun</span><input type="time" id="t_weekend" oninput="checkDirty()"></label>
</section>

<section id="sec_custom" class="hidden">
  <h2>Custom Time</h2>
  <label><span>Time</span><input type="time" id="t_custom" oninput="checkDirty()"></label>
</section>

<div class="btns">
  <button id="btn_cancel" onclick="cancel()" disabled>Cancel</button>
  <button id="btn_save"   onclick="save()"   disabled>Save</button>
</div>
<div id="msg"></div>

<script>
// Original values loaded from device — used for dirty checking and cancel
var orig = {mode:'off', t_weekday:'', t_weekend:'', t_custom:''};

function fmt(hm){
  if(!hm)return '';
  return String(hm[0]).padStart(2,'0')+':'+String(hm[1]).padStart(2,'0');
}
function parse(s){
  if(!s)return null;
  const[h,m]=s.split(':');
  return[parseInt(h),parseInt(m)];
}
function val(id){return document.getElementById(id).value;}

function onModeChange(){
  const m=val('mode');
  document.getElementById('sec_scheduled').classList.toggle('hidden',m!=='scheduled');
  document.getElementById('sec_custom').classList.toggle('hidden',m!=='custom');
}

function checkDirty(){
  const fields=['mode','t_weekday','t_weekend','t_custom'];
  let dirty=false;
  for(const id of fields){
    const changed=val(id)!==orig[id];
    document.getElementById(id).classList.toggle('changed',changed);
    if(changed)dirty=true;
  }
  document.getElementById('btn_save').disabled=!dirty;
  document.getElementById('btn_cancel').disabled=!dirty;
}

function applyValues(v){
  document.getElementById('mode').value=v.mode;
  document.getElementById('t_weekday').value=v.t_weekday;
  document.getElementById('t_weekend').value=v.t_weekend;
  document.getElementById('t_custom').value=v.t_custom;
  onModeChange();
  checkDirty();
}

function cancel(){
  applyValues(orig);
}

// Load settings once on page open — never auto-overwrites while editing
async function loadSettings(){
  try{
    const r=await fetch('/api/status');
    const d=await r.json();
    document.getElementById('sync').textContent=
      d.synced?('synced \u2022 '+d.date):'not synced (no WiFi at boot)';
    const s=d.settings;
    orig={
      mode:s.alarm_mode||'off',
      t_weekday:fmt(s.alarm_weekday),
      t_weekend:fmt(s.alarm_weekend),
      t_custom:fmt(s.alarm_oneoff),
    };
    applyValues(orig);
    updateClock(d);
    setInterval(tickClock,1000);
  }catch(e){
    document.getElementById('sync').textContent='device unreachable';
  }
}

// Clock ticks every second — touches ONLY #clock, never form fields
function updateClock(d){document.getElementById('clock').textContent=d.time;}
async function tickClock(){
  try{const r=await fetch('/api/status');updateClock(await r.json());}catch(e){}
}

async function save(){
  const body={
    alarm_mode:val('mode'),
    alarm_weekday:parse(val('t_weekday')),
    alarm_weekend:parse(val('t_weekend')),
    alarm_oneoff:parse(val('t_custom')),
  };
  try{
    const r=await fetch('/api/settings',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body)
    });
    const d=await r.json();
    if(d.ok){
      // Commit current values as new baseline — clears dirty state
      orig={mode:val('mode'),t_weekday:val('t_weekday'),
            t_weekend:val('t_weekend'),t_custom:val('t_custom')};
      checkDirty();
      document.getElementById('msg').textContent='Saved!';
    }else{
      document.getElementById('msg').textContent='Error: '+d.error;
    }
  }catch(e){
    document.getElementById('msg').textContent='Save failed: '+e;
  }
  setTimeout(()=>document.getElementById('msg').textContent='',3000);
}

loadSettings();
</script>
</body>
</html>"""
