# Troubleshooting Guide

Real issues we hit during development, and how we fixed them.

---

## Audio / ReSpeaker

### "ERROR: ReSpeaker not found in PyAudio device list"

**Cause:** USB permissions or device index changed after reboot.

**Fix:**
```bash
# Add udev rule (permanent fix)
echo 'SUBSYSTEM=="usb", ATTR{idVendor}=="2886", ATTR{idProduct}=="0018", MODE="0666"' \
  | sudo tee /etc/udev/rules.d/99-respeaker.rules
sudo udevadm control --reload-rules && sudo udevadm trigger

# Then find the new device index
python3 -c "import pyaudio; p=pyaudio.PyAudio(); [print(i, p.get_device_info_by_index(i)['name']) for i in range(p.get_device_count())]" 2>/dev/null | grep -i respeaker
# Update TTS_OUTPUT_DEVICE in gemini_dravixa.py to match
```

### ALSA errors flooding the terminal

```
Expression 'AlsaOpen' failed in 'src/hostapi/alsa/pa_linux_alsa.c'
```

**Cause:** `TTS_OUTPUT_DEVICE` is set to wrong index (e.g. still pointing to old index 24 when ReSpeaker moved to 0 after a reboot).

**Fix:** Find ReSpeaker index (above) and update `TTS_OUTPUT_DEVICE` in `gemini_dravixa.py`.

### TTS plays through wrong speaker / no sound

**Fix:** Check `TTS_OUTPUT_DEVICE` index and also run:
```bash
pactl list sinks short         # list PulseAudio sinks
pactl set-default-sink alsa_output.usb-SEEED_ReSpeaker_4_Mic_Array__UAC1.0_...
```

---

## Whisper / STT

### STT running on CPU instead of GPU

**Symptom:** Very slow transcription (>3s for short phrases).

**Fix:** Check CUDA is available and Whisper is loading on GPU:
```bash
python3 -c "import torch; print(torch.cuda.is_available())"
# Should print True
```

If False, reinstall torch from the Jetson-specific index:
```bash
pip install torch==2.8.0 --index-url https://pypi.jetson-ai-lab.io/jp6/cu126
```

### "Hello Toyota" detected as "Halo Toyota" switching to Indonesian

**Cause:** The pre-roll audio buffer (`ring_buffer`) retains the tail of the wake phrase and bleeds into the next recording, confusing Whisper's language detection.

**Fix:** Already applied in `gemini_dravixa.py` — `ring_buffer.clear()` is called at the moment of wake word detection. If it recurs, verify the fix is in your version.

### Short commands like "Play blinding lights" detected as Indonesian

**Cause:** Whisper sometimes mislabels short English commands as Indonesian. The `_sanity_check_language()` function catches this by checking marker words.

**Fix:** Add the command verb to `en_markers` in `gemini_dravixa.py`:
```python
en_markers = {..., "play", "turn", "open", "stop", ...}
```

---

## Phone Detection (phone_detect_node.py)

### CUDA not available in phone_env

**Symptom:** `torch.cuda.is_available()` returns `False` in phone_env.

**Fix:** Generic pip torch doesn't work on Jetson ARM64. Must use the Jetson-specific build:
```bash
source phone_env/bin/activate
pip uninstall torch torchvision -y
pip install torch==2.8.0 torchvision==0.23.0 --index-url https://pypi.jetson-ai-lab.io/jp6/cu126
pip install nvidia-cudss-cu12   # required for torch 2.8 on Jetson
```

### `ImportError: libcudss.so.0: cannot open shared object file`

**Cause:** torch 2.8+ on Jetson requires `libcudss` which isn't included in JetPack by default.

**Fix:**
```bash
pip install nvidia-cudss-cu12
```

### Model stays on CPU (slow, ~1.4 FPS)

**Cause:** `YOLO('yolov8s.pt')` loads on CPU by default even when CUDA is available.

**Fix:** Already applied in `phone_detect_node.py`:
```python
model = YOLO(_model_path)
if torch.cuda.is_available():
    model.to('cuda')
```

### Phone not being detected even when held up

1. Lower `PHONE_CONFIDENCE` threshold to 0.25 for testing
2. Check what the model actually sees: `debug_last_frame.jpg` is saved in the dravixa folder each frame
3. Make sure `drivermonitor.py` is running and sending frames (phone node shows `Frames: 0` if no frames arriving)

---

## Robot Face (ESP32 UART)

### Robot face doesn't react to W/C commands

Step-by-step debug:
```bash
# 1. Check port exists
ls -la /dev/ttyTHS1

# 2. Test loopback (jumper pin 8 → pin 10)
python3 -c "
import serial, time
s = serial.Serial('/dev/ttyTHS1', 115200, timeout=2)
s.write(b'W\n'); s.flush(); time.sleep(0.3)
print('Loopback:', repr(s.read(10)))
s.close()
"
# Expected: b'W\n'
# If b'': UART not configured on pins 8/10 — run jetson-io.py

# 3. Check ESP32 receives (via USB serial monitor on laptop)
# Should log: I (xxxxx) robot_face: [UART] face cmd 'W'
```

### `/dev/ttyTHS1` doesn't exist after reboot

**Cause:** jetson-io.py config didn't save or didn't apply.

**Fix:**
```bash
sudo /opt/nvidia/jetson-io/jetson-io.py
# → Configure Jetson 40pin Header
# → select uarta (8,10) — must show [*] under uarta column
# → Back → Save and reboot
```

Verify after reboot:
```bash
dmesg | grep ttyTHS
# Should show: 3100000.serial: ttyTHS1 at MMIO ...
```

### TX pin reads 0V (JetPack 7 regression)

**Symptom:** Loopback test fails even with jumper wire, pin 8 measures 0V instead of 3.3V idle.

**Cause:** Known regression in JetPack 7 / L4T R39.2 where UART1 TX pad doesn't drive.

**Fix:** Check your JetPack version with `cat /etc/nv_tegra_release`. If on R36.x you should not hit this. If on R39.x, check NVIDIA forums for the specific patch.

---

## Spotify

### "Spotify Connect device 'dravixa' not found"

**Fix:**
1. Make sure `spotifyd` is running: `ps aux | grep spotifyd`
2. Open Spotify on your phone → it should show "dravixa" as a Connect device
3. Wait 5–10 seconds after spotifyd starts for it to register on the network

### Spotify token expired

```bash
# Delete cached token and re-authenticate
rm ~/.cache/spotipy/*
python3 gemini_dravixa.py
# Will open a browser for OAuth — complete the login
```

---

## Gemini API

### Gemini 400 error / `functionCall/functionResponse` sequencing error

**Cause:** Consecutive model turns in chat history (two "assistant" messages in a row) or orphaned tool-response entries at the start of history after trimming.

**Fix:** Already handled in `gemini_dravixa.py` — the history trim uses a while-loop that strips any leading orphaned "tool" or "assistant+tool_calls" entries, not just a simple `pop()`.

If it recurs, check `MAX_HISTORY` — lowering it too much causes more orphaning. Keep at 12+.

### LLM not routing commands correctly

**Fix:** Check `FORCE_LANGUAGE_TRIGGERS` and the Japanese romaji keyword hints in the system prompt. For a new command type, add a keyword hint like:
```python
# in system prompt:
"# 'netra' = navigate"
```

---

## PyQt5 HMI

### HMI window doesn't open / black screen

```bash
# Check display is set
echo $DISPLAY   # should be :0 or :1
export DISPLAY=:0

# Check PyQt5 is installed in dravixa_env
python3 -c "import PyQt5; print('OK')"
```

### HMI can't connect to gemini_dravixa.py (TCP 19999)

**Fix:** Start `gemini_dravixa.py` before `dravixa_hmi.py`. The HMI is a client — it connects to the main script's TCP server on port 19999.

---

## General

### Process already using a UDP port on startup

```bash
sudo lsof -i :5001   # or 5002, 5003, 5004, 5005
# kill the offending process
kill -9 <PID>
```

### All processes at once (clean restart)

```bash
bash stop_dravixa.sh   # kills all 6 processes
sleep 2
bash launch_dravixa.sh
```
