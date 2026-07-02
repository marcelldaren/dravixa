# Hardware Wiring

---

## Jetson Orin Nano 40-Pin Header

```
Physical pin layout (standard Raspberry Pi numbering):

Left column (odd)     Right column (even)
  1 → 3.3V              2 → 5V
  3 → I2C1_SDA          4 → 5V
  5 → I2C1_SCL          6 → GND ◄─── use this for ESP32 GND
  7 → GPIO              8 → UART1_TX ◄─── to ESP32 GPIO39 (RX)
  9 → GND              10 → UART1_RX ◄─── to ESP32 GPIO38 (TX)
 11 → UART1_RTS        12 → ...
 ...

Pin 1 is identified by a SQUARE solder pad (all others are round).
Count from pin 1 downward. Even pins are on the RIGHT column.
```

**To find pin 1:** Look for the square pad on the 40-pin header. That's pin 1 (3.3V). Pin 8 is the 4th pin down on the right side. Pin 10 is the 5th pin down on the right side.

---

## ESP32-S3 AMOLED (Waveshare) ↔ Jetson

| Jetson 40-pin | Signal | ESP32 GPIO |
|---|---|---|
| Pin 8 | UART TX (Jetson sends) | GPIO39 (RX) |
| Pin 10 | UART RX (Jetson receives) | GPIO38 (TX) |
| Pin 6 | GND | GND |

**⚠️ Important:** TX → RX and RX → TX. They cross. Jetson TX goes to ESP32 RX.

**Baud rate:** 115200  
**Device node:** `/dev/ttyTHS1`  
**Enable with:** `sudo /opt/nvidia/jetson-io/jetson-io.py` → Configure 40-pin header → uarta (8,10)

---

## Arduino ↔ Jetson

| Connection | Details |
|---|---|
| Interface | USB (CH341 USB-serial chip) |
| Device node | `/dev/ttyCH341USB0` (may vary — check `ls /dev/ttyUSB*`) |
| Baud rate | 115200 |
| Protocol | ASCII commands: `ON`, `OFF`, `F`, `H`, `B`, `SHAKE`, `SLEEP`, `READY` |

Arduino controls:
- AC relay (ON/OFF)
- Window motor (scissor mechanism)
- Servo positions (F=fan, H=high, B=body)

---

## ReSpeaker 4-Mic Array ↔ Jetson

| Connection | Details |
|---|---|
| Interface | USB |
| Typical device index | 0 (verify with `arecord -l`) |
| USB IDs | Vendor: 2886, Product: 0018 |
| Channels | 6 (4 mics + 2 processed) — use channel 0 for primary |
| Sample rate | 16000 Hz |

**DOA filter:** The ReSpeaker's Direction of Arrival filtering is configured to accept voice from 0°–140° (driver's seat direction). Sound from the speaker side is rejected.

---

## Camera (Logitech Brio 500) ↔ Jetson

| Connection | Details |
|---|---|
| Interface | USB |
| Device node | `/dev/video0` (usually) |
| Resolution used | 640×480 @ 30fps for CV |
| Mount position | Driver-facing, dashboard level |

---

## Full Wiring Diagram (Text)

```
                    ┌─────────────────────────┐
                    │   JETSON ORIN NANO       │
                    │                          │
  ReSpeaker 4-Mic ──┤ USB                     │
  Logitech Brio  ───┤ USB                     │
  Arduino        ───┤ USB (CH341)              │
                    │                          │
                    │ 40-pin header:           │
                    │  Pin 8  (TX) ────────────┼──► ESP32 GPIO39 (RX)
                    │  Pin 10 (RX) ────────────┼──◄ ESP32 GPIO38 (TX)
                    │  Pin 6  (GND)────────────┼──► ESP32 GND
                    │                          │
                    │ HDMI ────────────────────┼──► HMI Monitor (PyQt5)
                    └─────────────────────────┘

  Arduino:
    Digital pin 2 ──► AC relay
    Digital pin 3 ──► Window motor (PWM)
    Digital pin 4 ──► Servo signal

  ESP32-S3 AMOLED:
    Built-in AMOLED ──► Robot face display (LVGL)
    I2C (SDA=47, SCL=48) ──► QMI8658C IMU
    Wi-Fi ──► Firebase (face template polling)
    USB (separate, for flashing only) ──► Laptop
```

---

## Power Notes

- Jetson Orin Nano: 19V DC barrel jack (use official power supply, min 45W)
- ESP32-S3 AMOLED: powered via USB from Jetson or separate 5V
- Arduino: powered via USB from Jetson
- ReSpeaker: powered via USB from Jetson
- All USB devices: check total power budget (Jetson USB provides ~2A total)

If Jetson USB power is insufficient (devices dropping), use a powered USB hub.
