import os
import sys
import time
import socket
import json
import cv2
import numpy as np
import urllib.request  # Pre-load to prevent crash

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

os.environ['ORT_LOGGING_LEVEL'] = '3'

try:
    from hsemotion_onnx.facial_emotions import HSEmotionRecognizer
except ImportError:
    print("ERROR: HSEmotion not installed.")
    sys.exit(1)

# ==========================================
# CONFIGURATION
# ==========================================

UDP_IP = "127.0.0.1"
LISTEN_PORT = 5002
TARGET_PORT = 5001

# HSEmotion raw labels → our 4 driving-relevant emotions + Neutral
# HSEmotion outputs: Anger, Contempt, Disgust, Fear, Happiness, Neutral, Sadness, Surprise
EMOTION_MAP = {
    "Anger":     "Anger",
    "Contempt":  "Anger",      # Contempt → remap to Anger (similar risk profile)
    "Disgust":   "Anger",      # Disgust  → remap to Anger
    "Fear":      "Sad",        # Fear     → remap to Sad (stress/anxiety)
    "Happiness": "Happiness",
    "Neutral":   "Neutral",
    "Sadness":   "Sad",
    "Surprise":  "Neutral",    # Surprise → Neutral (brief, not actionable)
}

# Minimum confidence to trust a prediction — below this → Neutral
CONFIDENCE_THRESHOLD = 0.45

# Happiness is genuinely harder to detect than negative emotions
# (smaller muscle movements, more variation between people).
# Use a lower threshold so mild smiles still get classified correctly.
HAPPINESS_CONFIDENCE_THRESHOLD = 0.30

# Temporal smoothing: keep last N predictions and vote
SMOOTHING_WINDOW = 5

# ==========================================
# UDP SOCKET SETUP
# ==========================================

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    sock.bind((UDP_IP, LISTEN_PORT))
    sock.settimeout(1.0)
except Exception as e:
    print(f"ERROR: Failed to bind to port {LISTEN_PORT}: {e}")
    sys.exit(1)

# ==========================================
# MODEL INIT
# ==========================================

print("\n" + "="*55)
print("  EMOTION NODE - Dravixa DMS")
print("="*55)
print("[Emotion Node] Loading HSEmotion ONNX model...")
sys.stdout.flush()

try:
    model = HSEmotionRecognizer(model_name='enet_b0_8_best_vgaf')
    print("[Emotion Node] Model loaded.")
except Exception as e:
    print(f"ERROR: Failed to load model: {e}")
    sys.exit(1)

# ==========================================
# TEMPORAL SMOOTHER
# ==========================================

class EmotionSmoother:
    """
    Majority-vote smoother over last N frames.
    Prevents single-frame flickers from triggering alerts.
    """
    def __init__(self, window=SMOOTHING_WINDOW):
        self.window = window
        self.history = []   # list of (emotion, confidence)

    def update(self, emotion: str, confidence: float) -> tuple:
        self.history.append((emotion, confidence))
        if len(self.history) > self.window:
            self.history.pop(0)

        # Count votes
        votes = {}
        conf_sum = {}
        for e, c in self.history:
            votes[e] = votes.get(e, 0) + 1
            conf_sum[e] = conf_sum.get(e, 0) + c

        # Winner = most votes; ties broken by confidence sum
        winner = max(votes, key=lambda e: (votes[e], conf_sum[e]))
        avg_conf = conf_sum[winner] / votes[winner]
        return winner, round(avg_conf, 4)

smoother = EmotionSmoother()

# ==========================================
# FPS TRACKER
# ==========================================

class FPSTracker:
    def __init__(self, window=30):
        self.timestamps = []
        self.window = window

    def tick(self):
        now = time.perf_counter()
        self.timestamps.append(now)
        if len(self.timestamps) > self.window:
            self.timestamps.pop(0)

    @property
    def fps(self):
        if len(self.timestamps) < 2:
            return 0.0
        elapsed = self.timestamps[-1] - self.timestamps[0]
        if elapsed == 0:
            return 0.0
        return (len(self.timestamps) - 1) / elapsed

fps_tracker = FPSTracker()

# ==========================================
# MAIN INFERENCE LOOP
# ==========================================

print(f"[Emotion Node] Listening on UDP {UDP_IP}:{LISTEN_PORT}")
print(f"[Emotion Node] Active emotions: Anger | Happiness | Sad | Neutral")
print(f"[Emotion Node] Confidence threshold: {CONFIDENCE_THRESHOLD}")
print(f"[Emotion Node] Smoothing window: {SMOOTHING_WINDOW} frames")
print("[Emotion Node] Press Ctrl+C to stop.\n")
sys.stdout.flush()

frames_processed = 0
frames_rejected = 0

try:
    while True:
        try:
            data, addr = sock.recvfrom(65507)
            start_time = time.perf_counter()

            # Decode JPEG bytes → OpenCV frame
            nparr = np.frombuffer(data, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if frame is None or frame.size == 0:
                frames_rejected += 1
                continue

            # Run inference
            emotion_label, emotion_scores = model.predict_emotions(frame, logits=False)
            raw_confidence = float(np.max(emotion_scores))

            # Map to our 4 driving emotions
            mapped_emotion = EMOTION_MAP.get(emotion_label, "Neutral")

            # Per-emotion confidence thresholds
            # Happiness uses a lower bar — mild smiles have lower raw confidence
            # but are still genuine and should not be collapsed to Neutral
            if mapped_emotion == "Happiness":
                threshold = HAPPINESS_CONFIDENCE_THRESHOLD
            else:
                threshold = CONFIDENCE_THRESHOLD

            # If confidence too low, default to Neutral (don't guess)
            if raw_confidence < threshold:
                mapped_emotion = "Neutral"

            # Temporal smoothing
            smoothed_emotion, smoothed_conf = smoother.update(mapped_emotion, raw_confidence)

            # Send result to main DMS
            result_payload = {
                "emotion": smoothed_emotion,
                "confidence": smoothed_conf,
                "raw_emotion": emotion_label,       # useful for debugging
                "raw_confidence": round(raw_confidence, 4),
            }
            result_json = json.dumps(result_payload)
            sock.sendto(result_json.encode('utf-8'), (UDP_IP, TARGET_PORT))

            # FPS and latency tracking
            fps_tracker.tick()
            inference_ms = (time.perf_counter() - start_time) * 1000
            frames_processed += 1

            sys.stdout.write(
                f"\r  Frames: {frames_processed:>5} | "
                f"FPS: {fps_tracker.fps:>5.1f} | "
                f"Raw: {emotion_label:<10} ({raw_confidence*100:>4.1f}%) | "
                f"→ {smoothed_emotion:<10} | "
                f"Latency: {inference_ms:>5.1f}ms | "
                f"Rejected: {frames_rejected}"
            )
            sys.stdout.flush()

        except socket.timeout:
            continue
        except json.JSONDecodeError as e:
            print(f"\nWARN: JSON error: {e}")
        except Exception as e:
            print(f"\nWARN: Unexpected error: {e}")

except KeyboardInterrupt:
    print("\n\nStopping Emotion Node...")

finally:
    sock.close()
    print(f"Emotion Node offline. Processed: {frames_processed} | Rejected: {frames_rejected}")
