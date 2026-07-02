# Architecture Deep Dive

For the next intern who wants to understand how things actually work.

---

## Process Architecture

Dravixa runs as **6 separate processes** that communicate over localhost UDP/TCP. This was a deliberate design choice — not over-engineering.

**Why separate processes?**

1. **numpy conflict**: `driveremo.py` and `phone_detect_node.py` both need libraries that conflict on numpy version with the main pipeline's custom CUDA-built OpenCV. Separate venvs solve this.
2. **Fault isolation**: if phone detection crashes, the rest of the system keeps running.
3. **CPU/GPU affinity**: IMU pinning allows distributing across Jetson's 6 cores.

---

## gemini_dravixa.py — Main Pipeline

This is the brain. Everything routes through here.

```
Audio capture (mic)
    ↓
Voice Activity Detection (energy threshold + silence timer)
    ↓
Wake word check (Whisper, throwaway transcription)
    ↓
Full transcription (Whisper, language detection)
    ↓
Language sanity check (_sanity_check_language)
    ↓
LLM routing decision:
    ├── Direct command? → bypass LLM, execute immediately
    │     (50+ keyword rules: AC, window, music, navigation...)
    └── Conversational? → Gemini API (or Qwen local)
           ↓
        Tool calls (weather, maps, Spotify, navigation...)
           ↓
        TTS response (edge-tts → Kokoro ONNX → sounddevice)
```

### Key design decisions

**Why two transcription passes for wake words?**
The first pass (wake check) uses `language=None` — Whisper guesses the language. If it hears "hello toyota" as "halo toyota" (Indonesian), that's fine for wake detection. The ring buffer is cleared immediately after wake, so the second pass (actual command) starts from clean audio. The language gets force-reset to English on wake regardless.

**Why `ring_buffer.clear()` on wake?**
The ring buffer holds ~1.5 seconds of pre-roll audio. Without clearing it, the tail of "...ota" from "hello toyota" gets prepended to your first command's recording, causing Whisper to see a blended clip that biases language detection toward Indonesian.

**Why `_sanity_check_language()` instead of just trusting Whisper?**
Whisper's language detection is occasionally wrong on short phrases (e.g., "play blinding lights" → Indonesian at 94% confidence). The sanity check uses marker word lists + Indonesian morphology patterns to override Whisper's guess when it conflicts with strong textual evidence.

---

## drivermonitor.py + driveremo.py — CV Pipeline

These two work together:

```
drivermonitor.py (main CV process)
    ├── MediaPipe FaceMesh → 468 landmarks
    │   ├── EAR (Eye Aspect Ratio) → blink/closure
    │   ├── MAR (Mouth Aspect Ratio) → yawning
    │   └── Head pose (6DoF) → distraction direction
    ├── PERCLOS (rolling 60s window of eye closure)
    ├── Blink rate tracker
    └── Fatigue score 0–100 → alert levels
          ↓
          UDP:5003 → gemini_dravixa.py (mood events)
          UDP:5002 → driveremo.py (face crops, 10fps)

driveremo.py (emotion subprocess, separate venv)
    ├── HSEmotion ONNX (8 classes)
    └── UDP:5001 → drivermonitor.py (emotion results)
```

**EAR auto-calibration:**
On startup, drivermonitor samples the driver's EAR for 30 frames to establish their personal baseline. The threshold is set at `baseline × 0.75` rather than a fixed value. This prevents false positives on drivers with naturally smaller eyes.

**Fatigue scoring:**
The score is additive and decays slowly over time. Multiple concurrent signals (e.g., PERCLOS + yawning + low blink rate) compound. A single microsleep (eyes closed 48+ frames) jumps the score directly to CRITICAL regardless of the accumulated total.

---

## phone_detect_node.py — Phone Detection

Runs in `phone_env` (separate Python venv) to avoid the numpy conflict.

```
drivermonitor.py
    └── every 2nd frame: resize to 416×416, JPEG encode
        └── UDP:5004 → phone_detect_node.py
                ├── YOLOv8s inference (CUDA, FP16)
                │   └── filter to COCO class 67 (cell phone)
                └── UDP:5005 → drivermonitor.py
                    └── {phone_detected, phone_confidence, phone_box}
```

**Why YOLOv8s instead of YOLOv8n?**
YOLOv8n (nano) at 62ms/frame on Jetson Orin Nano via CUDA was fast enough, but accuracy on partially-visible/angled phones was insufficient. YOLOv8s (small) improved detection meaningfully.

**Why fire-and-forget, async?**
Phone detection doesn't need to block the main MediaPipe loop. Results are cached and read on the next frame. If the phone detection node goes down, `_get_phone_result()` times out at 2.0s and returns `(False, 0.0, None)` — degrading gracefully.

---

## Robot Face System

Three separate communication channels:

```
1. Firebase (Wi-Fi) — idle face customization
   Flutter App → Firebase → ESP32 polls every 10s → renders chosen face

2. UART (GPIO pins 8/10) — event-driven expressions
   gemini_dravixa.py → /dev/ttyTHS1 → ESP32 GPIO39 (RX)
   Commands: 'W' = warning, 'C' = confused (holds 3s, then auto-reverts)

3. IMU (QMI8658C on I2C) — reactive expressions
   ESP32 samples accelerometer at 50Hz
   az < -4.0g → BRAKE face (shocked, squinting, O-mouth)
   az > +3.0g → ACCEL face (excited, speed lines, "ZOOM!")
```

**Priority stack on the ESP32:**
IMU reactions (BRAKE/ACCEL) override UART commands.
UART commands (W/C) override the Firebase idle face.
Firebase idle face is the default.

---

## Language Detection Logic

```python
def _sanity_check_language(text, whisper_lang):
    # 1. Script detection (instant, high confidence)
    if contains_kana_or_kanji(text):
        return "ja", True

    # 2. Marker word counting
    words = set(text.lower().split())
    id_hits = count_intersection(words, id_markers)   # yang, saya, bisa...
    en_hits = count_intersection(words, en_markers)   # the, play, turn...
    ja_hits = count_intersection(words, ja_markers)   # desu, kudasai...

    if max(id_hits, en_hits, ja_hits) == 0:
        # 3. Morphology fallback (no marker words at all)
        # Indonesian has distinctive prefixes/suffixes never found in English
        if no_indonesian_morphology(text):
            return "en", False   # short English command, default
        return whisper_lang, False

    # 4. Winner takes all, 2+ hits = strong signal
    return winner, (max_hits >= 2)
```

---

## HMI Architecture

```
gemini_dravixa.py ──TCP:19999──► dravixa_hmi.py
                                      │
                    ┌─────────────────┼──────────────────┐
                    │                 │                  │
              Dashboard tab      Chat / AI tab     Map tab
              (HoloGauge,        (chat history,   (QWebEngine)
               CarVisual,         DravixaAvatar,
               ADAS viz)          AIWave)
```

Packet types sent over TCP:
- `{"type": "chat", "text": "...", "is_user": bool}`
- `{"type": "status", "awake": bool, "speaking": bool, "processing": bool}`
- `{"type": "vehicle", "speed": N, "rpm": N}`
- `{"type": "alert", "alert_level": "WARNING|CRITICAL", "message": "..."}`
- `{"type": "navigate", "destination": "..."}`
- `{"type": "spotify", "track": "...", "artist": "..."}`

---

## Deployment Checklist

Before exhibition/demo:

- [ ] `spotifyd` is running and "dravixa" appears as Spotify Connect device on phone
- [ ] `ls /dev/ttyTHS1` exists
- [ ] `ls /dev/ttyCH341USB0` exists (or correct Arduino port)
- [ ] ReSpeaker device index confirmed (`TTS_OUTPUT_DEVICE` set correctly)
- [ ] Camera visible: `ls /dev/video*`
- [ ] All 6 processes start cleanly (no errors in first 30 seconds)
- [ ] EAR calibration completes (drivermonitor logs "EAR baseline set")
- [ ] Test wake word: "Hello Toyota" → robot says "Hey! I'm listening"
- [ ] Test robot face: `python3 -c "import serial; s=serial.Serial('/dev/ttyTHS1',115200); import time; time.sleep(1); s.write(b'W\n'); s.close()"`
- [ ] Test phone detection: hold phone in front of camera → drivermonitor should log phone alert
