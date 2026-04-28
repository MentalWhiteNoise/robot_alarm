# alarm_config.py – Alarm clock configuration
# Edit these values to configure alarm behavior.

# ── Hardware presence flags ───────────────────────────────────────────────────
# Set to True once each piece of hardware is physically wired up.
# When False the feature is skipped and safe defaults apply.

HAS_IR_SENSOR = False   # IR motion sensor — screen stays on permanently when False

# ── Alarm times ───────────────────────────────────────────────────────────────
# (hour, minute) in 24-hour time, or None to disable.

ALARM_WEEKDAY  = (7, 0)    # Mon–Fri 7:00 AM
ALARM_WEEKEND  = (8, 30)   # Sat–Sun 8:30 AM
ALARM_ONEOFF   = None      # (hour, minute) or None

# Weekday mask: 0=Mon … 6=Sun
WEEKDAYS = {0, 1, 2, 3, 4}   # Mon–Fri
WEEKEND  = {5, 6}             # Sat–Sun

# ── Wake phase ────────────────────────────────────────────────────────────────
# Gradual ramp before the alarm triggers. Screen stays OFF during wake.
WAKE_DURATION   = 20 * 60    # seconds — 20 min ramp-up before alarm time
                              # set to 0 to skip wake phase entirely

# ── Alarm phase ───────────────────────────────────────────────────────────────
ALARM_TIMEOUT   = 10 * 60    # seconds — auto-dismiss if never touched

# ── Snooze ────────────────────────────────────────────────────────────────────
SNOOZE_COUNT_MAX  = 3                 # number of snoozes allowed before force-dismiss
SNOOZE_DURATION_BASE  = 9 * 60       # first snooze: 9 min
SNOOZE_DURATION_DECR  = 60           # each snooze is 1 min shorter than the last
                                      # snooze 1 = 9 min, snooze 2 = 8 min, snooze 3 = 7 min
SNOOZE_DISMISS_WINDOW = 0.8          # seconds — two presses within this window = dismiss

# ── Post-alarm awake phase ────────────────────────────────────────────────────
AWAKE_DURATION  = 15 * 60   # seconds — screen/lights stay on after dismiss

# ── Motion / idle screen ──────────────────────────────────────────────────────
MOTION_SCREEN_ON = 30        # seconds — screen stays on after IR motion detected

# ── Volume / brightness ramp ─────────────────────────────────────────────────
# Values are 0.0 – 1.0; the state machine interpolates linearly over the phase.
WAKE_VOLUME_START  = 0.0
WAKE_VOLUME_END    = 0.6
WAKE_LIGHT_START   = 0.0
WAKE_LIGHT_END     = 0.6

ALARM_VOLUME       = 1.0
ALARM_LIGHT        = 1.0

SNOOZE_VOLUME      = 0.4
SNOOZE_LIGHT       = 0.3

AWAKE_LIGHT        = 0.5
