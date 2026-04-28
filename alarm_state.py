# alarm_state.py – Alarm clock state machine
#
# States
# ------
#   IDLE_OFF    Screen off. Waiting for motion or alarm time.
#   IDLE_ON     Screen on (robot face). Alarm set but not yet firing.
#   WAKE        Pre-alarm ramp. Sound + lights grow. Screen stays OFF.
#   ALARM       Alarm firing. Screen on. Sound + lights at full.
#   SNOOZE      Brief sleep between alarms. Screen on. Softer sound/lights.
#   AWAKE       Post-dismiss wind-down. Screen + lights on, no sound.
#
# Integration
# -----------
# In code.py, call AlarmClock.tick(dt) once per main loop iteration.
# Check AlarmClock.screen_on to decide whether to refresh the display.
# Check AlarmClock.wake_level / alarm_active / etc. to drive lights + audio.

import time
import alarm_config as cfg
import network   # live settings dict: network.settings

# ── State constants ───────────────────────────────────────────────────────────
IDLE_OFF = "idle_off"
IDLE_ON  = "idle_on"
WAKE     = "wake"
ALARM    = "alarm"
SNOOZE   = "snooze"
AWAKE    = "awake"


def _minutes_match(t, hour, minute):
    """True if time.struct_time t is within the same minute as (hour, minute)."""
    return t.tm_hour == hour and t.tm_min == minute and t.tm_sec < 5


class AlarmClock:
    """
    Alarm state machine.  Instantiate once and call .tick(dt) every loop.

    Public read properties
    ----------------------
    state          : str — current state constant
    screen_on      : bool — whether the display should be active
    wake_level     : float 0–1 — ramp progress during WAKE phase (use for lights/audio)
    snooze_count   : int — how many times the alarm has been snoozed this cycle
    alarm_mode     : str — "weekday", "weekend", "oneoff", or "off"
    """

    def __init__(self):
        # Without an IR sensor the screen is always on; start in IDLE_ON.
        self.state         = IDLE_OFF if cfg.HAS_IR_SENSOR else IDLE_ON
        self.snooze_count  = 0

        # Internal timers (seconds elapsed in current phase)
        self._phase_timer  = 0.0
        self._idle_timer   = 0.0        # screen-on timer for motion / post-dismiss
        self._alarm_timer  = 0.0        # auto-dismiss watchdog

        # Snooze dismiss detection
        self._last_btn_t   = None       # time.monotonic() of last button press

        # wake_level: 0→1 ramp used for lights + audio during WAKE/ALARM/SNOOZE
        self.wake_level    = 0.0

        # Flags for the main loop to act on (cleared after one tick)
        self.alarm_just_started  = False
        self.snooze_just_started = False
        self.alarm_dismissed     = False

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def screen_on(self):
        return self.state in (IDLE_ON, ALARM, SNOOZE, AWAKE)

    def on_motion(self):
        """Call when IR sensor fires."""
        if self.state == IDLE_OFF:
            self._enter(IDLE_ON)
            self._idle_timer = 0.0

    def on_button_snooze_dismiss(self):
        """
        Call when the snooze/dismiss button is pressed.
        Single press → snooze (if in ALARM/SNOOZE).
        Two presses within SNOOZE_DISMISS_WINDOW → dismiss.
        """
        if self.state not in (ALARM, SNOOZE):
            return
        now = time.monotonic()
        if (self._last_btn_t is not None
                and (now - self._last_btn_t) <= cfg.SNOOZE_DISMISS_WINDOW):
            self._dismiss()
        else:
            self._snooze()
        self._last_btn_t = now

    def tick(self, dt, wall_time=None):
        """
        Advance the state machine.

        dt        : float seconds since last call
        wall_time : time.struct_time or None.  Pass rtc.RTC().datetime
                    (or the result of ntp sync) for alarm triggering.
                    If None, alarm triggering is disabled (useful during dev).
        """
        # Clear one-shot flags
        self.alarm_just_started  = False
        self.snooze_just_started = False
        self.alarm_dismissed     = False

        self._phase_timer += dt

        if self.state == IDLE_OFF:
            self._tick_idle_off(dt, wall_time)
        elif self.state == IDLE_ON:
            self._tick_idle_on(dt, wall_time)
        elif self.state == WAKE:
            self._tick_wake(dt)
        elif self.state == ALARM:
            self._tick_alarm(dt)
        elif self.state == SNOOZE:
            self._tick_snooze(dt)
        elif self.state == AWAKE:
            self._tick_awake(dt)

    # ── State tick methods ────────────────────────────────────────────────────

    def _tick_idle_off(self, dt, wall_time):
        if wall_time and self._alarm_due(wall_time):
            self._start_alarm_sequence()

    def _tick_idle_on(self, dt, wall_time):
        if wall_time and self._alarm_due(wall_time):
            self._start_alarm_sequence()
            return
        if cfg.HAS_IR_SENSOR:
            self._idle_timer += dt
            if self._idle_timer >= cfg.MOTION_SCREEN_ON:
                self._enter(IDLE_OFF)

    def _tick_wake(self, dt):
        dur = cfg.WAKE_DURATION if cfg.WAKE_DURATION > 0 else 1
        self.wake_level = min(1.0, self._phase_timer / dur)
        if self._phase_timer >= cfg.WAKE_DURATION:
            self._enter_alarm()

    def _tick_alarm(self, dt):
        self.wake_level = 1.0
        self._alarm_timer += dt
        if self._alarm_timer >= cfg.ALARM_TIMEOUT:
            self._dismiss()

    def _tick_snooze(self, dt):
        snooze_dur = max(60, cfg.SNOOZE_DURATION_BASE
                         - self.snooze_count * cfg.SNOOZE_DURATION_DECR)
        self.wake_level = min(1.0, self._phase_timer / snooze_dur)
        if self._phase_timer >= snooze_dur:
            self._enter_alarm()

    def _tick_awake(self, dt):
        if self._phase_timer >= cfg.AWAKE_DURATION:
            self._enter(IDLE_OFF)

    # ── Transitions ──────────────────────────────────────────────────────────

    def _enter(self, state):
        self.state        = state
        self._phase_timer = 0.0
        print("alarm state →", state)

    def _start_alarm_sequence(self):
        self.snooze_count = 0
        self.wake_level   = 0.0
        if cfg.WAKE_DURATION > 0:
            self._enter(WAKE)
        else:
            self._enter_alarm()

    def _enter_alarm(self):
        self._alarm_timer        = 0.0
        self.alarm_just_started  = True
        self._enter(ALARM)

    def _snooze(self):
        self.snooze_count += 1
        if self.snooze_count > cfg.SNOOZE_COUNT_MAX:
            self._dismiss()
            return
        self.snooze_just_started = True
        self.wake_level          = 0.0
        self._enter(SNOOZE)

    def _dismiss(self):
        self.alarm_dismissed = True
        self._idle_timer     = 0.0
        self._enter(AWAKE)

    # ── Alarm schedule check ─────────────────────────────────────────────────

    def _alarm_due(self, t):
        """Return True if wall_time matches the active alarm for today."""
        s    = network.settings
        mode = s.get("alarm_mode", "off")
        if mode == "off":
            return False
        wday = t.tm_wday  # 0=Mon … 6=Sun
        if mode == "scheduled":
            # Weekday time Mon–Fri, weekend time Sat–Sun
            hm = s.get("alarm_weekday") if wday in cfg.WEEKDAYS else s.get("alarm_weekend")
            return _minutes_match(t, *hm) if hm else False
        if mode == "custom":
            hm = s.get("alarm_oneoff")
            return _minutes_match(t, *hm) if hm else False
        return False
