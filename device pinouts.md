Female USB-C:
* V
* -D
* +D
* G

Male USB-C:
* G
* D+
* D-
* V

ESP32 S2 Mini
* Left (Bottom): 
    * 39 / 40
    * 37 / 38
    * 35 / 36
    * 33 / 34
    * 18 / 21
    * 16 / 17
    * GND / GND
    * VBUS / 15

* Right (Top): 
    * 1 / EN
    * 2 / 3
    * 4 / 5
    * 6 / 7
    * 8 / 9
    * 10 / 11
    * 13 / 12
    * 14 / 3V3

SD Card Reader:
* 3V3
* CS
* MOSI
* CLK
* MISO
* GND

IR Breakout:
* VCC (3.3 - 5v)
* GND
* OUT

TLV320DAC3100 DAC
* Left:
    * VIN
    * GND
    * SCL
    * SDA
    * DIN
    * WSEL
    * BCK
    * MCLK
    * SPK-
    * SPK+

* Right:
    * BIAS
    * AIN1
    * AIN2
    * IO
    * RST
    * HPR
    * MIC

Qualia
* Stemma QT Connector:
    * white
    * yellow
    * red
    * black
* Pins:
    * Reset
    * Boot0
    * TX0
    * SCK
    * MISO
    * MOSI
    * A1
    * A0
    * 3.3V
    * GND
* 3 PIN JST
    * A0 ?
    * 5V ?
    * GND ?


```
  USBC ──┬──► Qualia  USBC ─► JST ─► NeoPixels 5V & GND
         ├──► S2 Mini USBC pin
```
  
```
  G (GND) ──┬──► Qualia GND
            ├──► S2 Mini GND
            ├──► DAC GND
            ├──► SD Card GND
            ├──► 4 pin plug ┬──► IR Breakout GND
                            ├──► Button
```

```      
Qualia TC0 → UART TX → S2 Mini
```

```
ESP32 S2 Mini
    3V3 ──┬──► SD card 3V3
          ├──► DAC VIN
          ├──► 4 pin plug ─► IR Breakout VCC
```