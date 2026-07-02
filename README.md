# DRAVIXA 🤖
**Dynamic Robot Assistant for Vehicle Intelligence & Xperience Analysis**

> Toyota Software Academy — IDEA INNOVATION (IN CAR)  
> Tim BINUS | BINUS University | 2025–2026

---

## What is Dravixa?

Dravixa is an AI-powered in-car companion robot that proactively monitors and engages the driver. Unlike passive infotainment systems that only respond when commanded, Dravixa detects driver fatigue, mood, and context — then acts on it.

Built on a **Jetson Orin Nano**, it combines real-time computer vision, multilingual voice conversation, live API integration, and a 3D-printed robot companion with an animated AMOLED face.

---

## System Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     DRAVIXA SYSTEM                          │
│                                                             │
│  ReSpeaker 4-Mic ──► Whisper STT ──► Gemini/Qwen LLM      │
│                                           │                 │
│  USB Webcam ──► MediaPipe ──► Fatigue    ──► Voice Reply    │
│                    │          Mood            Edge-TTS      │
│                    └──► YOLOv8 ──► Phone Detection         │
│                                                             │
│  Arduino ──► AC / Window Control                            │
│  ESP32-S3 AMOLED ──► Robot Face (18 emotion states)        │
│  PyQt5 HMI ──► Dashboard Display                           │
│  Flutter App ──► Firebase ──► Robot Face Customization     │
└─────────────────────────────────────────────────────────────┘
```

---

## Hardware Requirements

| Component | Spec | Notes |
|---|---|---|
| Main compute | NVIDIA Jetson Orin Nano 8GB | JetPack 6.x (R36.5.0) |
| Microphone | ReSpeaker 4-Mic Array (UAC1.0) | USB, DOA filtering |
| Camera | Logitech Brio 500 (USB Webcam) | Driver monitoring |
| Robot face | ESP32-S3 + AMOLED (Waveshare) | UART to Jetson |
| Car control | Arduino Uno/Nano | AC + window servo |
| HMI screen | Any HDMI monitor | PyQt5 dashboard |
| Robot body | 3D-printed casing | Custom design |

---

## Software Architecture

```
dravixa/
├── gemini_dravixa.py       # Main AI pipeline (LLM + STT + TTS + tools)
├── drivermonitor.py        # Computer vision (fatigue, mood, phone detection)
├── driveremo.py            # Emotion detection subprocess (HSEmotion ONNX)
├── phone_detect_node.py    # YOLOv8 phone detection (separate venv)
├── dravixa_hmi.py          # PyQt5 dashboard HMI
├── dravixa_avatar.py       # Animated robot face widget (NOMI-style)
├── car_visual.py           # BYD-style car visualization widget
├── robot_face.c            # ESP32-S3 firmware (LVGL + Firebase + IMU)
├── launch_dravixa.sh       # Launch all 6 processes
├── stop_dravixa.sh         # Kill all processes
└── docs/
    ├── SETUP.md            # Installation guide
    ├── ARCHITECTURE.md     # Technical deep-dive
    ├── WIRING.md           # Hardware wiring diagrams
    └── TROUBLESHOOTING.md  # Common issues & fixes
```

---

## Quick Start

### 1. Clone & Setup

```bash
git clone https://github.com/YOUR_USERNAME/dravixa.git
cd dravixa
python3 -m venv dravixa_env
source dravixa_env/bin/activate
pip install -r requirements.txt
```

### 2. Configure API Keys

```bash
cp config.example.env config.env
nano config.env   # fill in your API keys
```

Required keys:
- `GEMINI_API_KEY` — Google Gemini AI
- `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`
- `OWM_API_KEY` — OpenWeatherMap
- `FIREBASE_PROJECT` + `FIREBASE_API_KEY` (for robot face)

### 3. Setup Phone Detection (Separate Venv)

```bash
python3 -m venv phone_env
source phone_env/bin/activate
pip install torch==2.8.0 torchvision==0.23.0 --index-url https://pypi.jetson-ai-lab.io/jp6/cu126
pip install nvidia-cudss-cu12
pip install "numpy<2.0.0"
pip install ultralytics --no-deps
pip install opencv-python pyyaml pandas matplotlib seaborn tqdm
deactivate
```

### 4. Configure Hardware Ports

Edit `gemini_dravixa.py`:
```python
ARDUINO_PORT    = "/dev/ttyCH341USB0"   # Arduino USB-serial
FACE_BOARD_PORT = "/dev/ttyTHS1"        # ESP32 UART (Jetson GPIO pins 8/10)
TTS_OUTPUT_DEVICE = 0                   # ReSpeaker device index (check with arecord -l)
```

### 5. Enable Jetson GPIO UART

```bash
sudo /opt/nvidia/jetson-io/jetson-io.py
# → Configure 40-pin header → enable uarta (8,10) → Save and reboot
```

### 6. Launch

```bash
bash launch_dravixa.sh
```

Or manually (6 terminals in order):

```bash
# Terminal 1
spotifyd --no-daemon

# Terminal 2
source dravixa_env/bin/activate && python3 driveremo.py

# Terminal 3 — wait 10s for EAR calibration
source dravixa_env/bin/activate && python3 drivermonitor.py

# Terminal 4
source dravixa_env/bin/activate && python3 gemini_dravixa.py

# Terminal 5
source dravixa_env/bin/activate && python3 dravixa_hmi.py

# Terminal 6
source phone_env/bin/activate && python3 phone_detect_node.py
```

---

## Key Features

### 🎤 Multilingual Voice AI
- Speech-to-text: `faster-whisper` (GPU, float16)
- Languages: English, Indonesian, Japanese (auto-detect)
- TTS: `edge-tts` via Kokoro ONNX (CUDA)
- LLM: Google Gemini API (cloud) or Qwen (local offline fallback)

### 👁 Driver Monitoring (Computer Vision)
- **Fatigue detection**: PERCLOS (eye closure ratio over 60s), yawn counting, head nodding
- **Mood detection**: HSEmotion ONNX (8 emotion classes from face crop)
- **Phone distraction**: YOLOv8s (COCO class 67, separate process/venv)
- Auto-calibration: measures each driver's baseline EAR on startup

### 🤖 Robot Companion
- 18 emotion states on AMOLED face (LVGL v8, FreeRTOS)
- IMU-reactive (hard brake/acceleration → instant expression change)
- Firebase-synced: driver customizes face via Flutter app → syncs over Wi-Fi
- UART-controlled from Jetson: `W` = warning face, `C` = confused face

### 🚗 Vehicle Control
- AC on/off, fan speed
- Window up/down (scissor mechanism simulation)
- Controlled via Arduino over serial

### 📱 Live API Integration
- Maps & navigation: ORS + OpenStreetMap
- Weather: OpenWeatherMap
- Spotify: `spotifyd` (Spotify Connect daemon) + Web API
- Flood alerts, fuel stations, POI search

---

## UDP Communication Map

All inter-process communication uses localhost UDP:

| Port | From → To | Data |
|---|---|---|
| 5001 | `driveremo.py` → `drivermonitor.py` | Emotion results (JSON) |
| 5002 | `drivermonitor.py` → `driveremo.py` | Face crop frames (JPEG) |
| 5003 | `drivermonitor.py` → `gemini_dravixa.py` | Mood/alert events (JSON) |
| 5004 | `drivermonitor.py` → `phone_detect_node.py` | Camera frames (JPEG) |
| 5005 | `phone_detect_node.py` → `drivermonitor.py` | Detection results (JSON) |
| 19999 | `gemini_dravixa.py` → `dravixa_hmi.py` | HMI packets (JSON/TCP) |

---

## Wake Words

Say any of these to activate Dravixa:

| Language | Trigger |
|---|---|
| English | "Hello Toyota", "Hey Toyota", "Hi Toyota" |
| Indonesian | "Halo Toyota", "Hai Toyota" |
| Japanese | "Toyota-san" |

After activation, speak in any of the 3 languages — Dravixa auto-detects and responds in the same language.

---

## Testing Individual Components

```bash
# Test robot face UART commands
python3 -c "import serial,time; s=serial.Serial('/dev/ttyTHS1',115200); time.sleep(1); s.write(b'W\n'); s.close()"

# Test Spotify
python3 -c "from gemini_dravixa import spotify_play_playlist; spotify_play_playlist('test')"

# Test fatigue alert to HMI
python3 -c "
import socket, json
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.sendto(json.dumps({'type':'driver_alert','alert_level':'WARNING','message':'Test alert'}).encode(), ('127.0.0.1', 5003))
"

# Check audio devices
python3 -c "import pyaudio; p=pyaudio.PyAudio(); [print(i, p.get_device_info_by_index(i)['name']) for i in range(p.get_device_count())]"
```

---

## Team

| Name | Role |
|---|---|
| Marcell Darren Febriyan | AI Engineer — CV, LLM, STT/TTS, HMI, API |
| Matthew Ricardo Gunawan | Flutter App, Firebase, Robot Face Design |
| Mark Alexander Ierwanto | 3D Modeling, Dashboard UI, System Integration |

---

## Acknowledgements

- Toyota Software Academy & TMMIN for the program
- BINUS University supervisors and mentors
- NVIDIA Jetson AI Lab for JetPack 6 support
- Ultralytics, MediaPipe, HSEmotion, Kokoro TTS communities

---

## License

Internal project — Toyota Software Academy 2025–2026.  
For continuation by future Toyota internship batches.
