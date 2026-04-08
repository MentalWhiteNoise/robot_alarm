# code.py – Robot Face v3
# Hardware: Adafruit Qualia ESP32-S3 + Round RGB TTL TFT Display 4" 720x720

import displayio
import time
import math
import random
import vectorio

from adafruit_qualia.displays.round40 import Round40

# ── Display ───────────────────────────────────────────────────────────────────
displayio.release_displays()
_drv = Round40()
_drv.init()
display = _drv.display
display.auto_refresh = False

_t = _drv._timings
_H = _t["width"]  + _t["hsync_back_porch"]  + _t["hsync_front_porch"]  + _t["hsync_pulse_width"]
_V = _t["height"] + _t["vsync_back_porch"]  + _t["vsync_front_porch"]  + _t["vsync_pulse_width"]
FRAME_PERIOD = (_H * _V / _t["frequency"]) * 3   # ~0.123 s ≈ 8 fps

CX, CY = 360, 360

# ── Palettes ──────────────────────────────────────────────────────────────────
def pal(c):
    p = displayio.Palette(1)
    p[0] = c
    return p

P_BG   = pal(0x000000)
P_EYE  = pal(0xAAAAAA)
P_DARK = pal(0x000000)

# ── Eye Geometry ──────────────────────────────────────────────────────────────
EYE_R   = 95     # eye white base radius
PUPIL_R = 34     # pupil radius — bigger = more expressive
HILIT_R = 13     # highlight dot (static)
LID_M   = 8

GAZE_R     = 90    # max gaze target radius — more travel overall
GAZE_FRAC  = 0.45  # group travels 45 %, pupil travels 55 % in local space
                   # lower fraction = pupil offset more visible within the white

# Eye size perspective — exaggerated
# At full horizontal gaze one eye grows +30 px, the other shrinks −30 px
EYE_SIZE_DELTA = 30

# Eyes closer together than before
L_REST_X  = CX - 118
R_REST_X  = CX + 118
EYE_REST_Y = CY - 42

# ── Scene ─────────────────────────────────────────────────────────────────────
scene = displayio.Group()
scene.append(vectorio.Circle(pixel_shader=P_BG, radius=362, x=CX, y=CY))
display.root_group = scene

# ── Eye Builder ───────────────────────────────────────────────────────────────
def build_eye(rest_x, rest_y):
    grp   = displayio.Group(x=rest_x, y=rest_y)
    white = vectorio.Circle(pixel_shader=P_EYE,  radius=EYE_R,   x=0, y=0)
    pupil = vectorio.Circle(pixel_shader=P_DARK, radius=PUPIL_R, x=0, y=0)
    hilit = vectorio.Circle(pixel_shader=P_EYE,  radius=HILIT_R, x=-28, y=-30)
    # Bottom squint — parked below eye (invisible), rises on happy/squint moods
    sqnt  = vectorio.Rectangle(pixel_shader=P_DARK,
                               x=-(EYE_R + LID_M), y=EYE_R + 1,
                               width=(EYE_R + LID_M) * 2,
                               height=EYE_R + LID_M)
    for s in (white, pupil, hilit, sqnt):
        grp.append(s)
    scene.append(grp)
    return grp, white, pupil, sqnt

l_grp, l_wh, l_pu, l_sq = build_eye(L_REST_X, EYE_REST_Y)
r_grp, r_wh, r_pu, r_sq = build_eye(R_REST_X, EYE_REST_Y)

# ── EyeState ──────────────────────────────────────────────────────────────────
class EyeState:
    """Two-spring eye — no blink.

    Spring 1 (gaze, stiff):  moves the whole Group (white+pupil+hilit together)
    Spring 2 (pupil, loose): pupil lags behind the group → googly parallax drift

    Eye size scales with horizontal gaze: looking away from this eye shrinks it,
    looking toward it grows it — perspective foreshortening illusion.
    sign_x: +1 for left eye (grows when target is positive/right), -1 for right.
    """
    _GK = 26.0;  _GD = 6.8
    _PK =  9.0;  _PD = 3.4

    def __init__(self, rx, ry, sign_x, grp, white, pupil, sqnt):
        self.rx, self.ry  = rx, ry
        self.sign_x       = sign_x
        self.grp          = grp
        self.white        = white
        self.pupil        = pupil
        self.sqnt         = sqnt
        self.gx = self.gy = self.gvx = self.gvy = self.gtx = self.gty = 0.0
        self.pax = self.pay = self.pvx = self.pvy = 0.0
        self._igx = self._igy = 0
        self._ipx = self._ipy = 0
        self._isq = 0
        self._iwr = EYE_R

    def look(self, tx, ty):
        d = math.sqrt(tx * tx + ty * ty)
        if d > GAZE_R:
            tx = tx * GAZE_R / d
            ty = ty * GAZE_R / d
        self.gtx, self.gty = tx, ty

    def update(self, dt):
        if dt > 0.05:
            dt = 0.05

        # Group travels only GAZE_FRAC of the target — the rest is covered by
        # the pupil offset, so the pupil is always visibly off-centre in the white
        gtx_grp = self.gtx * GAZE_FRAC
        gty_grp = self.gty * GAZE_FRAC
        self.gvx += ((gtx_grp - self.gx) * self._GK - self.gvx * self._GD) * dt
        self.gvy += ((gty_grp - self.gy) * self._GK - self.gvy * self._GD) * dt
        self.gx  += self.gvx * dt
        self.gy  += self.gvy * dt

        # Pupil spring targets the FULL gaze offset (in group-local space that
        # means the pupil target = full_target - group_target = (1-FRAC)*gtx)
        ptx = self.gtx * (1.0 - GAZE_FRAC)
        pty = self.gty * (1.0 - GAZE_FRAC)
        self.pvx += ((ptx - self.pax) * self._PK - self.pvx * self._PD) * dt
        self.pvy += ((pty - self.pay) * self._PK - self.pvy * self._PD) * dt
        self.pax += self.pvx * dt
        self.pay += self.pvy * dt

        igx = int(self.gx);  igy = int(self.gy)
        ipx = int(self.pax); ipy = int(self.pay)
        # Size tracks the live gaze position (gx), not the target — so it
        # animates gradually as the spring moves, not as a snap on look().
        # sign_x = -1 for left eye: negative gx (looking left) → shrink left eye
        # sign_x = +1 for right eye: positive gx (looking right) → shrink right eye
        # Looking left (negative gx): left eye grows (head turns toward us on that side),
        # right eye shrinks (receding). sign_x=-1 for left, +1 for right.
        # + sign means: left eye gx negative → +(-1)(negative) = positive → grows ✓
        iwr = max(EYE_R - EYE_SIZE_DELTA,
                  min(EYE_R + EYE_SIZE_DELTA,
                      int(EYE_R + self.sign_x * self.gx * EYE_SIZE_DELTA / GAZE_R)))

        changed = False
        if igx != self._igx or igy != self._igy:
            self._igx, self._igy = igx, igy
            self.grp.x = self.rx + igx
            self.grp.y = self.ry + igy
            changed = True
        if ipx != self._ipx or ipy != self._ipy:
            self._ipx, self._ipy = ipx, ipy
            self.pupil.x = ipx
            self.pupil.y = ipy
            changed = True
        if iwr != self._iwr:
            self._iwr = iwr
            self.white.radius = iwr
            changed = True
        return changed

    def set_squint(self, h):
        ih = int(max(0, min(h, EYE_R // 2)))
        if ih == self._isq:
            return False
        self._isq = ih
        self.sqnt.y = EYE_R - ih + 1
        return True


# Looking right (gx > 0): right eye shrinks, left eye grows.
# sign_x = +1 for left eye: gx positive → left grows; gx negative → left shrinks
# sign_x = -1 for right eye: gx positive → right shrinks; gx negative → right grows
left  = EyeState(L_REST_X, EYE_REST_Y, +1, l_grp, l_wh, l_pu, l_sq)
right = EyeState(R_REST_X, EYE_REST_Y, -1, r_grp, r_wh, r_pu, r_sq)

# ── Mood Definitions ──────────────────────────────────────────────────────────
MOODS = ("idle", "happy", "surprised", "thinking", "talking", "smirk")

MOOD_LABELS = {
    "idle":      "idle",
    "happy":     "smiling",
    "surprised": "surprised",
    "thinking":  "thinking",
    "talking":   "talking",
    "smirk":     "smirking",
}

def gaze_label(tx, ty):
    """Return a human-readable direction string for a gaze target."""
    ax = tx if tx >= 0 else -tx
    ay = ty if ty >= 0 else -ty
    if ax < 10 and ay < 10:
        return "looking forward"
    if ay > ax * 1.4:
        return "looking up" if ty < 0 else "looking down"
    if ax > ay * 1.4:
        return "looking left" if tx < 0 else "looking right"
    # Diagonal
    vstr = "up" if ty < 0 else "down"
    hstr = "left" if tx < 0 else "right"
    return "looking " + vstr + "-" + hstr

def apply_mood(m):
    left.set_squint(0);   right.set_squint(0)
    if m == "idle":
        left.look(0, 0);           right.look(0, 0)
    elif m == "happy":
        left.look(10, -20);        right.look(-10, -20)
        left.set_squint(36);       right.set_squint(36)
    elif m == "surprised":
        left.look(0, -55);         right.look(0, -55)
    elif m == "thinking":
        left.look(60, -30);        right.look(60, -30)
        right.set_squint(24)
    elif m == "talking":
        left.look(6, 14);          right.look(-6, 14)
    elif m == "smirk":
        left.look(-50, 20);        right.look(40, -20)
        left.set_squint(20)
    print("mood:", MOOD_LABELS.get(m, m))

# ── Animation State ───────────────────────────────────────────────────────────
mood          = "idle"
mood_timer    = 0.0
mood_duration = 4.0

wander_timer  = 0.0
wander_period = random.uniform(1.0, 2.5)

frame_timer   = 0.0

# ── Main Loop ─────────────────────────────────────────────────────────────────
t_prev = time.monotonic()
dirty  = True

while True:
    now = time.monotonic()
    dt  = now - t_prev
    if dt > 0.05:
        dt = 0.05
    t_prev = now
    frame_timer += dt

    if left.update(dt):   dirty = True
    if right.update(dt):  dirty = True

    # -- Idle wander
    wander_timer += dt
    if wander_timer >= wander_period and mood == "idle":
        wander_timer  = 0.0
        wander_period = random.uniform(0.8, 2.5)
        tx = random.uniform(-60, 60)
        ty = random.uniform(-45, 45)
        left.look(tx, ty)
        right.look(tx, ty)
        print(gaze_label(tx, ty))

    # -- Mood state machine
    mood_timer += dt
    if mood_timer >= mood_duration:
        mood_timer    = 0.0
        mood_duration = random.uniform(2.5, 6.5)
        new_mood = random.choice(MOODS)
        if new_mood != mood:
            mood = new_mood
            apply_mood(mood)
            dirty = True

    # -- Refresh gate
    if dirty and frame_timer >= FRAME_PERIOD:
        display.refresh()
        dirty       = False
        frame_timer = 0.0
