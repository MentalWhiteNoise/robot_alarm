import time
import board
import busio
import digitalio
import sdcardio
import storage
import audiobusio
import audiocore
import json
from adafruit_tlv320 import TLV320DAC3100

# ── Boot delay ─────────────────────────────────────────────────────────────
time.sleep(1)

# ── SPI / SD card ──────────────────────────────────────────────────────────
spi = busio.SPI(board.IO36, board.IO35, board.IO37)
sd = sdcardio.SDCard(spi, board.IO34)
vfs = storage.VfsFat(sd)
storage.mount(vfs, "/sd")

with open("/sd/alarm_config.json") as f:
    cfg = json.load(f)

# ── DAC init ───────────────────────────────────────────────────────────────
rst = digitalio.DigitalInOut(board.IO7)
rst.direction = digitalio.Direction.OUTPUT
rst.value = False
time.sleep(0.01)
rst.value = True
time.sleep(0.05)

i2c = busio.I2C(board.IO9, board.IO8)
dac = TLV320DAC3100(i2c)
dac.configure_clocks(sample_rate=44100)
dac.speaker_output = True
dac.speaker_gain = 6
dac.speaker_volume = -10.0
dac.dac_volume = -20.0
time.sleep(0.35)

# ── SD warmup — prevents I2S DMA contention on first play ──────────────────
with open(cfg["audio"]["wake_song"], "rb") as f:
    while f.read(4096):
        pass

# ── I2S / UART ────────────────────────────────────────────────────────────
i2s = audiobusio.I2SOut(board.IO39, board.IO40, board.IO33)
uart = busio.UART(board.IO17, board.IO16, baudrate=9600, timeout=0)
uart_buf = b""

# ── State tracking ─────────────────────────────────────────────────────────
# S2 Mini tracks state derived from Qualia PLAY/STOP/STATE commands.
# Default IDLE is safe: no audio, no test actions until Qualia confirms.
STATE_IDLE      = "IDLE"
STATE_WAKE_RAMP = "WAKE_RAMP"
STATE_ALARMING  = "ALARMING"
STATE_SNOOZED   = "SNOOZED"

current_state = STATE_IDLE
state_synced  = False           # True once we receive STATE reply from Qualia
STATE_REQUEST_TIMEOUT = 30.0   # give up and assume IDLE after this many seconds

# ── Button (GPIO10, active LOW) ────────────────────────────────────────────
btn = digitalio.DigitalInOut(board.IO10)
btn.direction = digitalio.Direction.INPUT
btn.pull = digitalio.Pull.UP

BTN_DEBOUNCE_S = 0.02

# Window to collect presses before firing: IDLE needs 600 ms to catch triple;
# other states only need 400 ms (no triple discrimination needed).
BTN_WINDOW = {
    STATE_IDLE:      0.60,
    STATE_WAKE_RAMP: 0.40,
    STATE_ALARMING:  0.40,
    STATE_SNOOZED:   0.40,
}

btn_last        = True   # True = released (active LOW)
btn_press_count = 0
btn_window_start = 0.0

# ── IR sensor (GPIO11, active LOW) ─────────────────────────────────────────
ir = digitalio.DigitalInOut(board.IO11)
ir.direction = digitalio.Direction.INPUT
ir.pull = digitalio.Pull.UP

ir_last = True

# ── Audio state ────────────────────────────────────────────────────────────
wave_file    = None
current_song = None

KEEPALIVE_INTERVAL  = 30.0
last_keepalive      = time.monotonic()

HEARTBEAT_INTERVAL  = 10.0
last_heartbeat      = time.monotonic()

LONG_PRESS_S        = 2.0
btn_down_time       = 0.0
btn_long_fired      = False
disp_mode_always_on = True   # default: display always on


# ── Helpers ────────────────────────────────────────────────────────────────

def vol_to_db(vol):
    # 0.0 → -30 dB (nearly silent), 1.0 → 0 dB (full)
    return -30.0 * (1.0 - max(0.0, min(1.0, vol)))


def set_volume(vol):
    dac.dac_volume = vol_to_db(vol)


def stop_audio():
    global wave_file, current_song
    if i2s.playing:
        i2s.stop()
    if wave_file is not None:
        wave_file.close()
        wave_file = None
    current_song = None


def play_song(path, vol):
    global wave_file, current_song
    stop_audio()
    wave_file = open(path, "rb")
    wave = audiocore.WaveFile(wave_file)
    set_volume(vol)
    i2s.play(wave, loop=True)
    current_song = path


def send(msg):
    uart.write((msg + "\n").encode())


def apply_set(kv):
    """Apply a SET key=value command from Qualia (web config update)."""
    if "=" not in kv:
        return
    key, val = kv.split("=", 1)
    parts = key.strip().split(".")
    d = cfg
    try:
        for p in parts[:-1]:
            d = d[p]
        leaf = parts[-1]
        try:
            d[leaf] = int(val)
        except ValueError:
            try:
                d[leaf] = float(val)
            except ValueError:
                d[leaf] = val
    except (KeyError, TypeError):
        pass


def push_config():
    lines = ["CONFIG_START"]
    lines += [
        f"alarm.hour={cfg['alarm']['hour']}",
        f"alarm.minute={cfg['alarm']['minute']}",
        f"alarm.days={cfg['alarm']['days']}",
        f"timing.wake_ramp_minutes={cfg['timing']['wake_ramp_minutes']}",
        f"timing.snooze_minutes={cfg['timing']['snooze_minutes']}",
        f"timing.max_snoozes={cfg['timing']['max_snoozes']}",
        f"timing.display_timeout_s={cfg['timing']['display_timeout_s']}",
        f"timing.volume_ramp_end={cfg['timing']['volume_ramp_end']}",
        f"audio.wake_max_volume={cfg['audio']['wake_max_volume']}",
        f"audio.alarm_volume={cfg['audio']['alarm_volume']}",
        f"neopixels.wake.direction={cfg['neopixels']['wake']['direction']}",
        f"neopixels.wake.max_brightness={cfg['neopixels']['wake']['max_brightness']}",
        f"neopixels.alarm.effect={cfg['neopixels']['alarm']['effect']}",
        f"neopixels.alarm.brightness={cfg['neopixels']['alarm']['brightness']}",
    ]
    # Optional per-effect fields
    for opt in ("color", "color2", "speed"):
        if opt in cfg["neopixels"]["alarm"]:
            lines.append(f"neopixels.alarm.{opt}={cfg['neopixels']['alarm'][opt]}")
    lines.append("CONFIG_END")
    for line in lines:
        send(line)
        time.sleep(0.01)


def handle_button(state, count):
    """Translate press count + state into a UART command for Qualia."""
    if state == STATE_IDLE:
        if count == 1:
            send("DISPLAY")
        elif count == 2:
            send("TEST_WAKE")
        else:
            send("TEST_ALARM")
    elif state == STATE_ALARMING:
        if count == 1:
            send("SNOOZE")
        else:
            send("DISMISS")
    else:  # WAKE_RAMP, SNOOZED
        if count == 1:
            send("DISPLAY")
        else:
            send("DISMISS")


# ── Boot: push config, request state from Qualia ───────────────────────────
push_config()
send("GET_STATE")
state_request_time = time.monotonic()

# ── Main loop ──────────────────────────────────────────────────────────────
while True:
    now = time.monotonic()

    # Give up waiting for STATE reply after timeout — stay IDLE
    if not state_synced and (now - state_request_time) > STATE_REQUEST_TIMEOUT:
        state_synced = True

    # UART receive — handle commands from Qualia
    data = uart.read(64)
    if data:
        uart_buf += data
    while b"\n" in uart_buf:
        raw, uart_buf = uart_buf.split(b"\n", 1)
        cmd = raw.decode().strip()

        if cmd.startswith("STATE "):
            current_state = cmd[6:]
            state_synced  = True

        elif cmd == "PLAY WAKE":
            current_state = STATE_WAKE_RAMP
            play_song(cfg["audio"]["wake_song"], 0.0)  # Qualia ramps vol via VOL

        elif cmd == "PLAY ALARM":
            current_state = STATE_ALARMING
            play_song(cfg["audio"]["alarm_song"], cfg["audio"]["alarm_volume"])

        elif cmd in ("PLAY SNOOZE", "PLAY AWAKE"):
            current_state = STATE_SNOOZED
            play_song(cfg["audio"]["wake_song"], cfg["audio"]["wake_max_volume"])

        elif cmd == "STOP":
            current_state = STATE_IDLE
            stop_audio()

        elif cmd.startswith("VOL "):
            try:
                set_volume(float(cmd[4:]))
            except ValueError:
                pass

        elif cmd == "REQUEST_CONFIG":
            push_config()

        elif cmd.startswith("SET "):
            apply_set(cmd[4:])

    # IR sensor — send MOTION on falling edge (active LOW = motion)
    ir_val = ir.value
    if ir_last and not ir_val:
        print("S2 Mini | IR motion detected → MOTION")
        send("MOTION")
    ir_last = ir_val

    # Button — count presses within a state-appropriate window, then fire
    btn_val = btn.value
    if btn_last and not btn_val:                # falling edge = new press
        time.sleep(BTN_DEBOUNCE_S)
        if not btn.value:                       # still pressed after debounce
            btn_down_time = time.monotonic()
            if btn_press_count == 0:
                btn_window_start = time.monotonic()
            btn_press_count += 1
            print(f"S2 Mini | button press #{btn_press_count} (state={current_state})")
    btn_last = btn_val

    # Long press: button held >= LONG_PRESS_S → toggle display mode
    if not btn.value and btn_press_count > 0 and not btn_long_fired:
        if (now - btn_down_time) >= LONG_PRESS_S:
            btn_press_count     = 0             # cancel pending regular press
            btn_long_fired      = True
            disp_mode_always_on = not disp_mode_always_on
            cmd = "DISP_ALWAYS_ON" if disp_mode_always_on else "DISP_AUTO"
            print(f"S2 Mini | long press → {cmd}")
            send(cmd)
    elif btn.value:
        btn_long_fired = False

    # Only fire window after button is released (btn.value True = released, active LOW)
    window = BTN_WINDOW.get(current_state, 0.40)
    if btn_press_count > 0 and btn.value and (now - btn_window_start) > window:
        print(f"S2 Mini | firing {btn_press_count}-press (state={current_state})")
        handle_button(current_state, btn_press_count)
        btn_press_count = 0

    # SD keepalive — small read every 30 s while idle to prevent standby / DMA fault
    if not i2s.playing and (now - last_keepalive) >= KEEPALIVE_INTERVAL:
        try:
            with open(cfg["audio"]["wake_song"], "rb") as f:
                f.read(512)
        except OSError:
            pass
        last_keepalive = now

    if now - last_heartbeat >= HEARTBEAT_INTERVAL:
        print(f"S2 Mini | state: {current_state} | playing: {current_song}")
        last_heartbeat = now

    time.sleep(0.005)
