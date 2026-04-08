# code.py – Robot Face Animations
# Hardware: Adafruit Qualia ESP32-S3 + Round RGB TTL TFT Display 4" 720x720
# Requires: adafruit_qualia library in /lib on CIRCUITPY drive

import displayio
import time
import math
import random
import vectorio

from adafruit_qualia.displays.round40 import Round40

# ── Display Init ──────────────────────────────────────────────────────────────
displayio.release_displays()
_drv = Round40()
_drv.init()
display = _drv.display
display.auto_refresh = False

_t = _drv._timings
_H = (_t["width"]  + _t["hsync_back_porch"]  + _t["hsync_front_porch"]  + _t["hsync_pulse_width"])
_V = (_t["height"] + _t["vsync_back_porch"]  + _t["vsync_front_porch"]  + _t["vsync_pulse_width"])
FRAME_PERIOD = ((_H * _V) / _t["frequency"]) * 2   # ~0.082 s ≈ 12 fps

W, H = 720, 720
CX, CY = W // 2, H // 2

# ── Palettes ──────────────────────────────────────────────────────────────────
def pal(color):
    p = displayio.Palette(1)
    p[0] = color
    return p

P_BG   = pal(0x000000)
# Off-white (0xAA = 170, slightly above safe VCOM ceiling of 0x88).
# Chosen as a compromise: bright enough to read as white, dim enough that the
# brief erase-artifact flash (pupil erases → eye white exposed → pupil redrawn)
# is less jarring than with 0xDDDDDD.  Lower to 0x888888 if any flicker appears.
P_EYE  = pal(0xAAAAAA)
P_DARK = pal(0x000000)   # pupil + lid

# ── Eye Geometry ──────────────────────────────────────────────────────────────
# Eyes are intentionally DIFFERENT from each other for personality.
# Left:  wide horizontal oval  — relaxed, laid-back
# Right: taller oval           — alert, curious
# Polygon points are jittered randomly at startup → organic, never perfectly round.
#
# Why PUPIL_R = 30 (not larger):
#   dirty-region area ∝ radius².  At PUPIL_R=75 (previous), the dirty region was
#   ~6× bigger than at PUPIL_R=30, giving the DMA scanner far more opportunity to
#   catch the framebuffer mid-erase.  Smaller pupil = smaller window = less artifact.
L_RX, L_RY = 90, 68      # left eye polygon x / y half-axis
R_RX, R_RY = 74, 88      # right eye polygon x / y half-axis

PUPIL_R = 30              # pupil radius (~33–40% of eye diameter — matches reference)
HILIT_R = 10              # gloss highlight radius (static, zero dirty-region cost)
LID_M   = 10              # lid margin beyond eye top
CLOSE_H = 2 * (max(L_RY, R_RY) + LID_M)   # single-lid sweep covers tallest eye

# Highlight: fixed upper-left of each eye white — pupil drifts underneath it
L_HOX, L_HOY = -30, -24   # left eye highlight offset from centre
R_HOX, R_HOY = -22, -32   # right eye highlight offset (different = personality)

EYE_LX = CX - 150
EYE_RX = CX + 150
EYE_Y  = CY - 56

# Pre-compute unit-circle angles for 12-point polygon (no per-frame trig).
_N = 12
_CS = [(math.cos(-math.pi / 2 + 2 * math.pi * i / _N),
        math.sin(-math.pi / 2 + 2 * math.pi * i / _N))
       for i in range(_N)]

def make_eye_pts(cx, cy, rx, ry, jitter=5):
    """Organic cartoon eye: arched top, flattened bottom, per-point random jitter.

    Called ONCE per eye at startup.  The polygon is never rewritten during
    animation, so it contributes zero dirty-region cost at runtime.
    Jitter randomises each point independently → shape is irregular and unique
    every boot (left and right eyes will also differ from each other).
    """
    pts = []
    for c, s in _CS:
        ey = ry * s
        if ey > 0:
            ey *= 0.78                         # flatten bottom half
        jx = random.randint(-jitter, jitter)
        jy = random.randint(-jitter, jitter)
        pts.append((int(cx + rx * c) + jx, int(cy + ey) + jy))
    return pts

# ── Scene ─────────────────────────────────────────────────────────────────────
scene = displayio.Group()
display.root_group = scene
scene.append(vectorio.Circle(pixel_shader=P_BG, radius=362, x=CX, y=CY))

# ── Eye Builder ───────────────────────────────────────────────────────────────
def build_eye(ex, ey, rx, ry, hox, hoy):
    """Shapes per eye (z-order bottom → top):
         poly   — organic polygon white (STATIC after init)
         pupil  — dark circle, spring physics (ONLY shape updated per frame)
         hilit  — small white dot, fixed position (STATIC)
         lid    — black rectangle, top-down blink sweep
    """
    poly  = vectorio.Polygon(pixel_shader=P_EYE,
                             points=make_eye_pts(ex, ey, rx, ry), x=0, y=0)
    pupil = vectorio.Circle(pixel_shader=P_DARK, radius=PUPIL_R, x=ex, y=ey)
    hilit = vectorio.Circle(pixel_shader=P_EYE,  radius=HILIT_R,
                            x=ex + hox, y=ey + hoy)
    lid   = vectorio.Rectangle(pixel_shader=P_DARK,
                               x=ex - rx - LID_M, y=ey - ry - LID_M,
                               width=(rx + LID_M) * 2, height=1)
    for s in (poly, pupil, hilit, lid):
        scene.append(s)
    return pupil, lid

l_pu, l_tl = build_eye(EYE_LX, EYE_Y, L_RX, L_RY, L_HOX, L_HOY)
r_pu, r_tl = build_eye(EYE_RX, EYE_Y, R_RX, R_RY, R_HOX, R_HOY)

# ── Eye State ─────────────────────────────────────────────────────────────────
class EyeState:
    """Spring-physics pupil.  Only pupil.x / pupil.y are written per frame.

    travel_r — maximum pupil offset in any direction (derived from eye size).
               Left and right eyes differ slightly, giving each a different feel.
    Spring:  _K=28, _DMP=5.8 → ~0.5× critical damping → clear overshoot + bounce.
    """
    _K   = 28.0
    _DMP = 5.8

    def __init__(self, cx, cy, travel_r, pupil, tlid):
        self.cx, self.cy  = cx, cy
        self._tr          = travel_r
        self.pupil        = pupil
        self.tlid         = tlid
        self.ox = self.oy = 0.0
        self.vx = self.vy = 0.0
        self.tx = self.ty = 0.0
        self._iix = self._iiy = 0
        self._ih  = 1

    def look(self, tx, ty):
        d = math.sqrt(tx * tx + ty * ty)
        if d > self._tr:
            tx, ty = tx * self._tr / d, ty * self._tr / d
        self.tx, self.ty = tx, ty

    def update(self, dt):
        dt = min(dt, 0.05)
        self.vx += ((self.tx - self.ox) * self._K - self.vx * self._DMP) * dt
        self.vy += ((self.ty - self.oy) * self._K - self.vy * self._DMP) * dt
        self.ox += self.vx * dt
        self.oy += self.vy * dt
        iix = int(self.ox);  iiy = int(self.oy)
        if iix == self._iix and iiy == self._iiy:
            return False
        self._iix, self._iiy = iix, iiy
        self.pupil.x = self.cx + iix
        self.pupil.y = self.cy + iiy
        return True

    def set_lid(self, h):
        ih = int(max(1, min(h, float(CLOSE_H))))
        if ih == self._ih:
            return False
        self._ih = ih
        self.tlid.height = ih
        return True


# Travel range = min eye radius − pupil radius − safety margin.
# Left:  min(90,68)−30−8 = 30 px    Right: min(74,88)−30−8 = 36 px
# Different ranges → each eye has its own movement feel.
left  = EyeState(EYE_LX, EYE_Y, min(L_RX, L_RY) - PUPIL_R - 8, l_pu, l_tl)
right = EyeState(EYE_RX, EYE_Y, min(R_RX, R_RY) - PUPIL_R - 8, r_pu, r_tl)

# ── Mood Definitions ──────────────────────────────────────────────────────────
MOODS = ("idle", "happy", "surprised", "thinking", "talking", "smirk")

def apply_mood(m):
    left.look(0, 0);  right.look(0, 0)
    if m == "happy":
        left.look(6, -18);    right.look(-6, -18)
    elif m == "surprised":
        left.look(0, -26);    right.look(0, -26)
    elif m == "thinking":
        left.look(24, -16);   right.look(24, -16)
    elif m == "talking":
        left.look(4, 4);      right.look(-4, 4)
    elif m == "smirk":
        left.look(-14, 16);   right.look(22, -12)

# ── Animation State ───────────────────────────────────────────────────────────
mood          = "idle"
mood_timer    = 0.0
mood_duration = 4.0

blink_progress = -1.0
blink_timer    = 0.0
blink_period   = random.uniform(3.0, 6.0)

wander_timer  = 0.0
wander_period = random.uniform(1.0, 2.5)

frame_timer   = 0.0

# ── Main Loop ─────────────────────────────────────────────────────────────────
t_prev = time.monotonic()
dirty  = True

while True:
    now = time.monotonic()
    dt  = min(now - t_prev, 0.05)
    t_prev = now
    frame_timer += dt

    if left.update(dt):   dirty = True
    if right.update(dt):  dirty = True

    # -- Blink
    blink_timer += dt
    if blink_timer >= blink_period and blink_progress < 0:
        blink_progress = 0.0
        blink_timer    = 0.0
        blink_period   = random.uniform(3.0, 6.0)

    if blink_progress >= 0:
        blink_progress += dt * 3.5
        if blink_progress >= 1.0:
            blink_progress = -1.0
            if left.set_lid(1):   dirty = True
            if right.set_lid(1):  dirty = True
        else:
            t2 = blink_progress if blink_progress < 0.5 else 1.0 - blink_progress
            h  = t2 * 2.0 * CLOSE_H
            if left.set_lid(h):   dirty = True
            if right.set_lid(h):  dirty = True

    # -- Idle wander
    wander_timer += dt
    if wander_timer >= wander_period and mood == "idle":
        wander_timer  = 0.0
        wander_period = random.uniform(0.8, 2.5)
        left.look(random.uniform(-22, 22), random.uniform(-16, 16))
        right.look(left.tx, left.ty)

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
