#!/usr/bin/env python3
"""
ttf_to_bdf.py  —  extract specific glyphs from a TTF into a monochrome BDF
                   suitable for CircuitPython's adafruit_bitmap_font library.

Dependencies:  pip install freetype-py
Usage:         python ttf_to_bdf.py MaterialIcons-Regular.ttf material_icons_48.bdf 48

Codepoints reference:  MaterialIcons-Regular.codepoints  (included in the
                        Material Icons download from github.com/google/material-design-icons)
"""

import sys
import math

try:
    import freetype
except ImportError:
    sys.exit("Missing dependency — run:  pip install freetype-py")

# ── Glyphs to include ─────────────────────────────────────────────────────────
# Keys become the STARTCHAR name; values are Unicode codepoints (decimal or hex).
# Verify codepoints against MaterialIcons-Regular.codepoints before running.
GLYPHS = {
    "alarm":                0xE855,   # alarm clock
    "alarm_off":            0xE856,   # alarm clock with slash
    "notifications_active": 0xE7F7,   # bell with waves (alarming)
    "notifications_paused": 0xE7F8,   # bell with pause bars (snoozed)
    "timer":                0xE425,   # timer / countdown  (auto-off enabled)
    "timer_off":            0xE426,   # timer with slash   (always-on)
}


def convert(ttf_path: str, bdf_path: str, px: int) -> None:
    face = freetype.Face(ttf_path)
    face.set_pixel_sizes(0, px)

    glyphs = []
    ascent = descent = 0

    for name, cp in GLYPHS.items():
        face.load_char(cp, freetype.FT_LOAD_RENDER | freetype.FT_LOAD_TARGET_MONO)
        g  = face.glyph
        bm = g.bitmap
        w, h  = bm.width, bm.rows
        pitch = abs(bm.pitch)
        buf   = bytes(bm.buffer)
        xoff  = g.bitmap_left
        yoff  = g.bitmap_top - h           # BDF y = bottom of bbox from baseline
        dw    = g.advance.x >> 6           # device advance width in pixels

        ascent  = max(ascent,  g.bitmap_top)
        descent = max(descent, h - g.bitmap_top)

        # BDF rows: ceil(width/8) bytes each, hex-encoded, MSB first
        bpr = math.ceil(w / 8)
        rows = []
        for r in range(h):
            chunk = buf[r * pitch : r * pitch + bpr]
            rows.append(chunk.hex().upper())

        glyphs.append((name, cp, w, h, xoff, yoff, dw, rows))

    total_h = ascent + descent

    with open(bdf_path, "w") as f:
        f.write(f"STARTFONT 2.1\n")
        f.write(f"FONT -custom-icons--{px}-0-72-72-c-0-iso10646-1\n")
        f.write(f"SIZE {px} 72 72\n")
        f.write(f"FONTBOUNDINGBOX {px} {total_h} 0 {-descent}\n")
        f.write(f"STARTPROPERTIES 2\n")
        f.write(f"FONT_ASCENT {ascent}\n")
        f.write(f"FONT_DESCENT {descent}\n")
        f.write(f"ENDPROPERTIES\n")
        f.write(f"CHARS {len(glyphs)}\n")

        for name, cp, w, h, xoff, yoff, dw, rows in glyphs:
            swidth = round(dw * 1000 / px)  # scalable width in 1/1000 of point size
            f.write(f"STARTCHAR {name}\n")
            f.write(f"ENCODING {cp}\n")
            f.write(f"SWIDTH {swidth} 0\n")
            f.write(f"DWIDTH {dw} 0\n")
            f.write(f"BBX {w} {h} {xoff} {yoff}\n")
            f.write(f"BITMAP\n")
            for row in rows:
                f.write(row + "\n")
            f.write(f"ENDCHAR\n")

        f.write(f"ENDFONT\n")

    print(f"Wrote {len(glyphs)} glyphs → {bdf_path}  (ascent={ascent} descent={descent} total={total_h}px)")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(f"Usage: {sys.argv[0]} font.ttf output.bdf pixel_size\n"
                 f"  e.g. {sys.argv[0]} MaterialIcons-Regular.ttf material_icons_48.bdf 48")
    convert(sys.argv[1], sys.argv[2], int(sys.argv[3]))
