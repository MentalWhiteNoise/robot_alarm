import board, neopixel, time

NUM_PIXELS = 19
pixels = neopixel.NeoPixel(board.A0, NUM_PIXELS, brightness=0.2,
                           pixel_order=neopixel.GRBW, auto_write=False)

# Chase: light each pixel in order to confirm count and wiring
print("Chase test...")
for i in range(NUM_PIXELS):
    pixels.fill((0, 0, 0, 0))
    pixels[i] = (0, 128, 255, 0)
    pixels.show()
    time.sleep(0.15)

time.sleep(0.5)

# Ring groups: center, inner ring, outer ring
print("Ring groups...")
CENTER = [0]
INNER  = list(range(1, 7))
OUTER  = list(range(7, 19))

for ring, color in [(CENTER, (255, 0, 0, 0)), (INNER, (0, 255, 0, 0)), (OUTER, (0, 0, 255, 0))]:
    pixels.fill((0, 0, 0, 0))
    for i in ring:
        pixels[i] = color
    pixels.show()
    time.sleep(1.0)

# White channel
print("White channel...")
pixels.fill((0, 0, 0, 200))
pixels.show()
time.sleep(2.0)

# All on
print("All on...")
pixels.fill((60, 60, 60, 60))
pixels.show()
time.sleep(2.0)

pixels.fill((0, 0, 0, 0))
pixels.show()
print("Done")
