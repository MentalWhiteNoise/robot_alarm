# Robot Alarm — Wiring Plan

## Architecture

Two-board design:

- **Qualia ESP32-S3** — display + NeoPixels + UART TX only
- **Wemos S2 Mini (ESP32-S2)** — SD card, TLV320DAC3100 DAC, button, IR sensor, UART RX
- **Link** — UART: Qualia TC0 (TX) → S2 Mini RX. Text commands, 9600 baud.
- **Power** — USB-C breakout → VBUS to both boards + NeoPixels

---

## Power Distribution

Use the **female** USB-C socket as the inlet (charger plugs in here).
Solder wires to **V and G only** — D+ and D- are not needed and cannot be used for programming.

```
Female USB-C socket
  V (VBUS 5V) ──┬──► Qualia  VBUS/VIN
                ├──► S2 Mini VBUS pin
                └──► NeoPixels 5V
  G (GND)     ──┴──► all boards + NeoPixels GND (common ground)
```

Each board regulates its own 3V3 internally. Single cable in — everything powered.

> **CC resistors:** Many USB-C chargers won't deliver power without 5.1kΩ pull-downs on CC1/CC2.
> Before wiring everything up, test: plug in a charger and measure V→G with a multimeter.
> If you get ~5V the board has them populated. If nothing, add 5.1kΩ from CC1 and CC2 to GND.

> **Programming:** Each board must be programmed via its own USB-C port. The power breakout
> carries power only — no data routing. Leave an access point in the enclosure for each board's
> USB-C port, or plan to disassemble when you need to update code.

---

## Qualia Pin Allocation

| Pin | Assigned to |
|---|---|
| JST 3-pin / A0 | NeoPixels data |
| TC0 / TX | UART TX → S2 Mini GPIO 16 |
| A1 | UART RX ← S2 Mini GPIO 17 |
| STEMMA QT, SPI header | spare |

## S2 Mini Pin Allocation

| Function | Pin | Location |
|---|---|---|
| SD card SCK | 36 | Left, row 3 |
| SD card MOSI | 35 | Left, row 3 |
| SD card MISO | 37 | Left, row 2 |
| SD card CS | 34 | Left, row 4 |
| DAC BCLK (I2S) | 39 | Left, row 1 |
| DAC LRCLK (I2S) | 40 | Left, row 1 |
| DAC DIN (I2S) | 33 | Left, row 4 |
| DAC SDA (I2C) | 8 | Right, row 5 |
| DAC SCL (I2C) | 9 | Right, row 5 |
| DAC RST | 7 | Right, row 4 |
| Button | 10 | Right, row 6 |
| IR sensor | 11 | Right, row 6 |
| UART RX from Qualia | 16 | Left, row 6 |
| UART TX to Qualia (optional) | 17 | Left, row 6 |

Free pins: 1, 2, 3, 4, 5, 6, 12, 13, 14, 15, 18, 21, 38

---

## Status

- [x] Power distribution — USB-C breakout wired
- [x] NeoPixels — wired & tested
- [x] S2 Mini: SD card — wired & tested
- [x] S2 Mini: TLV320DAC3100 DAC — wired & tested
- [x] S2 Mini: Button — wired & tested
- [x] S2 Mini: IR sensor — wired & tested
- [x] UART link — wired & tested

---

## NeoPixels (Qualia side)

### Wiring

| NeoPixel | Connects to |
|---|---|
| 5V | USB-C breakout VBUS |
| GND | common GND |
| DIN | Qualia JST connector (A0) |

**Library:** `neopixel` (built into CircuitPython)

---

## SD Card (S2 Mini)

### Wiring

| SD Card board | S2 Mini |
|---|---|
| 3V3 | 3V3 |
| GND | GND |
| MOSI | GPIO 35 |
| MISO | GPIO 37 |
| SCK | GPIO 36 |
| CS | GPIO 34 |

### Test Code (CircuitPython on S2 Mini)

```python
import os, busio, board, sdcardio, storage

spi = busio.SPI(board.IO36, board.IO35, board.IO37)  # SCK, MOSI, MISO
sd  = sdcardio.SDCard(spi, board.IO34)
vfs = storage.VfsFat(sd)
storage.mount(vfs, "/sd")
print("SD mounted:", os.listdir("/sd"))
```

---

## TLV320DAC3100 DAC (S2 Mini)

I2S audio + I2C control — both come from the S2 Mini.

### Wiring

| DAC pin | Connects to | Notes |
|---|---|---|
| VIN | USB-C breakout VBUS (5V) | Class-D amp requires 5V supply |
| GND | GND | |
| SCL | GPIO 9 | I2C control |
| SDA | GPIO 8 | I2C control |
| DIN | GPIO 33 | I2S data |
| WSEL | GPIO 40 | I2S word select (LRCLK) |
| BCK | GPIO 39 | I2S bit clock (BCLK) |
| MCLK | — | Leave unconnected; configure DAC PLL to derive from BCK |
| SPK- | speaker − | Class-D amp output |
| SPK+ | speaker + | Class-D amp output |
| BIAS | — | Headphone bias; leave unconnected if not using headphones |
| AIN1 | — | Analog input; leave unconnected |
| AIN2 | — | Analog input; leave unconnected |
| IO | GND | ADDR0 — sets I2C address 0x18 |
| RST | GPIO 7 | Toggled low→high at startup for proper init |
| HPR | — | Headphone right out; leave unconnected |
| MIC | — | Mic bias; leave unconnected |

**I2C address:** 0x18 (IO/ADDR0 → GND)

### Notes

- DAC registers must be initialized over I2C before audio plays
- Audio playback: `audiobusio.I2SOut` + `audiocore.WaveFile`
- WAV files on SD card: 16-bit, 44100 Hz, mono

### Test Code

_To be written after wiring._

---

## Button (S2 Mini)

- Pull-up, wire to GND when pressed
- Single press within `SNOOZE_DISMISS_WINDOW` → `SNOOZE`
- Double press → `DISMISS`
- Sends command to Qualia over UART

**GPIO pin:** 10

---

## IR Sensor (S2 Mini)

- Signal goes low when motion detected
- Sends `MOTION` to Qualia over UART

**GPIO pin:** 11

---

## UART Command Link

| Wire | From | To |
|---|---|---|
| TX | Qualia TC0 | S2 Mini GPIO 16 (RX) |
| TX | S2 Mini GPIO 17 | Qualia A1 (used as UART RX) |
| GND | shared | shared |

> Both boards at 3V3 logic — no level shifting needed.
> **Baud rate:** 9600

### Commands: Qualia → S2 Mini

| Command | Effect |
|---|---|
| `PLAY WAKE\n` | Start wake ramp sound |
| `PLAY ALARM\n` | Start alarm sound |
| `PLAY SNOOZE\n` | Start snooze sound |
| `PLAY AWAKE\n` | Start post-alarm sound |
| `STOP\n` | Stop playback |
| `VOL 0.75\n` | Set volume 0.0–1.0 |

### Commands: S2 Mini → Qualia

| Command | Effect |
|---|---|
| `SNOOZE\n` | Button single-press |
| `DISMISS\n` | Button double-press |
| `MOTION\n` | IR sensor triggered |
