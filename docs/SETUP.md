# Setup Guide

Complete step-by-step installation for a fresh Jetson Orin Nano running JetPack 6.x.

---

## Prerequisites

| Requirement | Version |
|---|---|
| OS | Ubuntu 22.04 (JetPack 6.x, R36.5.0) |
| CUDA | 12.6 |
| Python | 3.10 |
| JetPack | 6.x (`cat /etc/nv_tegra_release` to verify) |

---

## Step 1 — System Dependencies

```bash
sudo apt update && sudo apt install -y \
  python3-pip python3-venv python3-dev \
  portaudio19-dev libsndfile1-dev \
  libusb-1.0-0-dev \
  npm nodejs \
  ffmpeg \
  libgl1-mesa-glx \
  libglib2.0-0
```

---

## Step 2 — Main Python Environment

```bash
cd ~/dravixa
python3 -m venv dravixa_env
source dravixa_env/bin/activate
pip install --upgrade pip

pip install \
  faster-whisper \
  pyaudio \
  sounddevice \
  soundfile \
  numpy \
  opencv-python \
  mediapipe \
  edge-tts \
  requests \
  pyserial \
  spotipy \
  flask \
  websockets \
  PyQt5 \
  scipy \
  onnxruntime-gpu \
  huggingface_hub \
  kokoro-onnx \
  usb \
  cachetools
```

---

## Step 3 — Phone Detection Environment (Separate Venv)

This MUST be separate from `dravixa_env` due to a numpy/OpenCV version conflict.

```bash
python3 -m venv phone_env
source phone_env/bin/activate

# Install Jetson-specific PyTorch (NOT generic pip torch)
pip install torch==2.8.0 torchvision==0.23.0 \
  --index-url https://pypi.jetson-ai-lab.io/jp6/cu126

# Fix missing libcudss (required by torch 2.8 on Jetson)
pip install nvidia-cudss-cu12

# Verify CUDA works BEFORE installing anything else
python3 -c "import torch; print('CUDA:', torch.cuda.is_available())"
# Must print: CUDA: True

# Pin numpy to avoid conflict with Jetson-built OpenCV
pip install "numpy<2.0.0"

# Install ultralytics WITHOUT pulling its own torch/opencv
pip install ultralytics --no-deps
pip install opencv-python pyyaml pandas matplotlib seaborn tqdm psutil

deactivate
```

---

## Step 4 — Spotifyd

```bash
# Download the ARM64 binary from the spotifyd GitHub releases
wget https://github.com/Spotifyd/spotifyd/releases/latest/download/spotifyd-linux-aarch64-slim.tar.gz
tar xf spotifyd-linux-aarch64-slim.tar.gz
sudo mv spotifyd /usr/local/bin/

# Create config
mkdir -p ~/.config/spotifyd
cat > ~/.config/spotifyd/spotifyd.conf << 'EOF'
[global]
username = "YOUR_SPOTIFY_USERNAME"
password = "YOUR_SPOTIFY_PASSWORD"
backend = "alsa"
device_name = "dravixa"
bitrate = 160
device_type = "speaker"
EOF
```

---

## Step 5 — ReSpeaker USB Permissions

Add udev rule so you don't need sudo every time:

```bash
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="2886", ATTR{idProduct}=="0018", MODE="0666"' \
  | sudo tee /etc/udev/rules.d/99-respeaker.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Find your ReSpeaker device index (needed for config):
```bash
python3 -c "import pyaudio; p=pyaudio.PyAudio(); [print(i, p.get_device_info_by_index(i)['name']) for i in range(p.get_device_count())]" 2>/dev/null | grep -i respeaker
```
Note the index number — set it as `TTS_OUTPUT_DEVICE` in `gemini_dravixa.py`.

---

## Step 6 — Enable Jetson GPIO UART (for Robot Face)

```bash
sudo /opt/nvidia/jetson-io/jetson-io.py
```

Navigate: **Configure Jetson 40pin Header** → select `uarta (8,10)` → **Back** → **Save and reboot**

After reboot, verify:
```bash
ls /dev/ttyTHS1   # should exist
dmesg | grep ttyTHS
```

Add yourself to dialout group:
```bash
sudo usermod -a -G dialout $USER
# log out and back in
```

---

## Step 7 — Arduino Setup

Install Arduino IDE on a separate laptop/PC, flash the AC/window control sketch to the Arduino Uno/Nano, then connect via USB.

Find the serial port:
```bash
ls /dev/ttyCH341USB* /dev/ttyUSB* /dev/ttyACM* 2>/dev/null
```

Set `ARDUINO_PORT` in `gemini_dravixa.py` to match.

---

## Step 8 — ESP32-S3 Robot Face Firmware

Flash `robot_face.c` using ESP-IDF on a separate laptop:

```bash
cd robot_face_firmware
idf.py set-target esp32s3
idf.py build
idf.py -p /dev/ttyUSB0 flash monitor
```

After flashing, the ESP32 connects to Wi-Fi (configure SSID/password in `robot_face.c`), polls Firebase, and listens for UART commands on GPIO38/39.

---

## Step 9 — API Keys

Copy the example config and fill in your own keys:

```bash
cp config.example.env config.env
```

| Key | Where to get it |
|---|---|
| `GEMINI_API_KEY` | console.cloud.google.com → Gemini API |
| `SPOTIFY_CLIENT_ID/SECRET` | developer.spotify.com → Dashboard |
| `OWM_API_KEY` | openweathermap.org → API keys |
| `ORS_API_KEY` | openrouteservice.org |
| `FIREBASE_API_KEY` | Firebase Console → Project Settings |

---

## Step 10 — First Run

```bash
source dravixa_env/bin/activate
python3 gemini_dravixa.py
```

Expected startup output:
```
Loading Whisper STT...
STT: GPU CUDA float16                    ✅
Loading Kokoro TTS with CUDA...          ✅
Spotify: connected (token cached)        ✅
Arduino: connected on /dev/ttyCH341USB0  ✅
Face board: connected on /dev/ttyTHS1    ✅
Mood bridge: listening on port 5003      ✅
```

If ReSpeaker fails, re-check Step 5 and verify device index.

---

## Hardware Wiring Quick Reference

```
Jetson 40-pin Header:
  Pin 8  (TX)  ──► ESP32 GPIO39 (RX)
  Pin 10 (RX)  ──► ESP32 GPIO38 (TX)
  Pin 6  (GND) ──► ESP32 GND

Arduino:
  USB → Jetson USB port → /dev/ttyCH341USB0

ReSpeaker:
  USB → Jetson USB port → device index 0 (usually)

Camera (Brio 500):
  USB → Jetson USB port → /dev/video0
```

See [WIRING.md](WIRING.md) for full diagrams.
