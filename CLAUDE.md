# Robot Alarm — Project Notes for Claude

## Hardware

| Component | Part |
|---|---|
| Microcontroller | Adafruit Qualia ESP32-S3 for TTL RGB-666 Displays |
| Display | Round RGB TTL TFT Display — 4" 720×720, No Touchscreen |
| Display driver IC | NV3052C (also labeled HD40015C40) |
| Connection | 40-pin RGB-666 TTL connector |

The Qualia board drives the display via a parallel dot-clock interface (not SPI/I2C for pixel data). The display IC **does** require an SPI-style init sequence, which the Qualia board sends through an I2C IO expander using `dotclockframebuffer.ioexpander_send_init_sequence`.

## CircuitPython Setup

- Use `adafruit_qualia` library — copy `adafruit_qualia/` into `/lib` on CIRCUITPY.
- Display driver lives in `adafruit_qualia/displays/round40.py` → class `Round40`.
- Init pattern:
  ```python
  from adafruit_qualia.displays.round40 import Round40
  displayio.release_displays()
  _drv = Round40()
  _drv.init()           # sends NV3052C init sequence, sets up DotClockFramebuffer
  display = _drv.display  # FramebufferDisplay, auto_refresh=True
  ```
- Timings: 16 MHz pixel clock, 720×720, hsync bp=44 fp=46 pw=2, vsync bp=16 fp=50 pw=16.

## Display Coordinate System

- Origin (0,0) is **top-left**.
- Center of the round display is at **(360, 360)**.
- The round display clips everything outside the 360px radius circle — shapes drawn
  in the corners will be physically invisible but still consume render time.

## Libraries Used (all built-in to CircuitPython firmware)

- `displayio` — scene graph, groups, palettes
- `vectorio` — hardware-accelerated vector shapes (Circle, Rectangle, Polygon)
- `dotclockframebuffer` — parallel display driver
- `framebufferio` — wraps DotClockFramebuffer into a displayio-compatible display

## Project Goal

Animated robot face that cycles through moods autonomously. Intended as a
decorative/ambient display (alarm clock robot, desk companion, etc.).

## Architecture — `code.py`

### Scene graph (z-order, bottom → top)

```
scene (displayio.Group)
  Circle  P_BG    r=362   full bleed background
  Circle  P_SEAM  r=338   outer face ring
  Circle  P_FACE  r=326   face fill
  Rectangle × 2   P_DETAIL   horizontal panel lines at ±198px from CY
  [Left eye]
    Circle  P_EYE   r=86   eye white
    Circle  P_IRIS  r=54   iris
    Circle  P_PUPIL r=27   pupil
    Rectangle P_LID        top eyelid
    Rectangle P_LID        bottom eyelid
  [Right eye]  (same structure)
  Polygon P_MOUTH          mouth (8-point, shape swapped per mood)
  Circle × 5  P_LED_*      LED indicator row
```

### EyeState class

Tracks each eye independently:
- `ox, oy` — current iris offset (float, interpolated)
- `tx, ty` — target iris offset
- `look(tx, ty)` — sets target, clamped to `EYE_R - IRIS_R - 6` px radius
- `update(dt)` — lerps current toward target at 7× per second
- `set_lid(h)` — sets eyelid coverage in px; **minimum is 1** (vectorio Rectangle
  requires height ≥ 1); at h=1 the lids sit in the face-colored margin and are invisible

### Eyelid mechanics

Two rectangles per eye, face-colored (P_LID must equal P_FACE color):
- **Top lid**: fixed y anchor at `ey - EYE_R - LID_M`; height grows downward
- **Bottom lid**: height grows upward; `blid.y = ey + EYE_R + LID_M - int(h)`

Both lids initialized at `height=1`, placed just outside the eye white in the margin —
effectively invisible until a blink starts.

### Mouth shapes

All mouth shapes padded to **8 points** so `mouth.points = new_pts` can swap freely
without changing the polygon object. Point coordinates are absolute screen pixels
(the polygon uses `x=0, y=0` as its origin offset).

Defined shapes: `neutral`, `happy`, `sad`, `surprised`, `talk_open`, `smirk`.

### Mood state machine

`MOODS = ("idle", "happy", "surprised", "thinking", "talking", "smirk")`

Randomly selects a new mood every 2.5–6.5 seconds. Each mood sets:
- `mouth.points` via `mouth_pts(shape)`
- `left.look(tx, ty)` / `right.look(tx, ty)` for gaze direction

### Timers in the main loop

| Timer | Period | Effect |
|---|---|---|
| `blink_timer` | 2.5–5.0 s random | triggers one blink cycle |
| `wander_timer` | 0.8–2.5 s random | new random pupil target (idle only) |
| `mood_timer` | 2.5–6.5 s random | picks a new mood |
| `talk_timer` | 0.15 s | toggles mouth open/closed during "talking" |
| `led_timer` | 0.25 s | advances LED chase light |

### Known CircuitPython quirks

- `vectorio.Rectangle` requires `width >= 1` AND `height >= 1` — never pass 0.
- `vectorio.Polygon.points` is settable and can change point count freely
  (tested on CircuitPython 9.x).
- `vectorio` shapes each hold a reference to a `displayio.Palette`; sharing palettes
  between shapes of the same color is fine and saves memory.
- `display.root_group = scene` must be called after the scene is populated enough to
  not show a blank frame on startup.
- **Always use `display.auto_refresh = False` + `display.refresh()` at the end of the
  main loop.** Auto-refresh causes each individual property change (`.x`, `.y`,
  `.height`, `.pixel_shader`) to immediately flush a partial update to the framebuffer
  while the display controller is mid-scan — heavy flickering and vertical-line
  artifacts result.
- **Guard every shape write behind an integer-change check.** Even with a single
  `refresh()`, the DMA scanner continuously reads the framebuffer; if the render pass
  writes many dirty regions the scanner catches up mid-write.  Only assign `.x`, `.y`,
  `.height` when the integer pixel value actually changed — see `EyeState._iix/_iiy`
  and `_ih` guards.  Idle frames then produce zero dirty regions.
- **Gate refresh on both dirty flag AND FRAME_PERIOD elapsed.** Only call
  `display.refresh()` when (a) something actually changed and (b) enough time has
  passed since the last refresh.  `FRAME_PERIOD` is calculated from the dot-clock
  timings: `(H_total * V_total) / pixel_clock`.  At 16 MHz this is ~41 ms (~24 fps).
  The code currently uses **2× FRAME_PERIOD (~82 ms, ~12 fps)** to give the DMA
  extra uncontested time and reduce mid-erase flash around moving shapes.
  `EyeState.update()` and `set_lid()` both return `True` when they actually wrote
  to a shape, so the caller can set `dirty = True` only on real changes.
- **Pixel clock note:** 16 MHz (the Round40 default) should be fine — do NOT lower it
  as it just slows the frame rate without fixing the root cause.  The fix is the
  FRAME_PERIOD gate above.
- **VCOM flicker on bright colors** is a hardware characteristic of this specific
  display unit — not fixable in software.  The NV3052C alternates pixel voltage
  polarity each frame to prevent LCD burn-in; high-luminance colors near the VCOM
  reference voltage show a faint beat.  Exhaustively tested all timing polarity
  combinations (pclk_active_high, de_idle_high, hsync/vsync idle levels, porch
  values) — none eliminate it.  `de_idle_high=True` removed the color-specific flicker
  but introduced blanking-interval white lines — not usable.
  **Safe color ceiling (no visible flicker):** keep RGB components below ~0x88.
  Current palette stays well within this: darkest `0x050A14`, brightest `0x7AAACE`
  (eye white) and `0x0088DD` (iris/mouth).  Do not add any near-white, bright yellow,
  bright cyan, or bright magenta to the scene.

## Future Ideas

- Alarm clock mode: pull time from RTC or network, show clock on display, trigger
  an animation/sound at alarm time.
- Sound: Qualia has a built-in I2S amp header — could add a speaker for robot sounds.
- Sensors: PIR or distance sensor to trigger reactions when someone walks by.
- More moods: `angry`, `sleepy`, `confused`, `wink`.
- Boot animation: sweep in from black, eyes open dramatically.
- Screensaver: after N minutes idle, dim display or show starfield/matrix effect.
