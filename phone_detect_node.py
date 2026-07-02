"""
================================================================
 PHONE DETECTION NODE — Dravixa DMS
 Standalone YOLOv8n process (separate venv from main pipeline)
================================================================
 Runs independently from drivermonitor.py / dravixa_gemini.py
 because ultralytics + the custom CUDA-built OpenCV in the main
 venv have a numpy version conflict (custom cv2 built against
 numpy 1.x C API, kokoro-onnx/main pipeline requires numpy 2.x).
 Running this as its own process with its own venv (numpy <2.0
 + plain pip opencv-python) sidesteps the conflict entirely —
 same pattern as driveremo.py for emotion detection.

 Communication (UDP, localhost only):
   LISTEN_PORT 5004  <- drivermonitor.py sends JPEG frames here
   SEND_PORT   5005  -> drivermonitor.py receives detection results
================================================================
"""

import os
import sys
import time
import socket
import json

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

os.environ['ORT_LOGGING_LEVEL'] = '3'

import numpy as np
import cv2  # plain pip opencv-python in THIS venv, fine here — not the
            # custom CUDA build used by the main dravixa_env

try:
    import torch
    from ultralytics import YOLO
except ImportError:
    print("ERROR: ultralytics/torch not installed in this venv.")
    print("Run: pip install ultralytics opencv-python 'numpy<2.0.0'")
    sys.exit(1)

# ==========================================
# CONFIGURATION
# ==========================================

UDP_IP       = "127.0.0.1"
LISTEN_PORT  = 5004   # <- drivermonitor.py sends JPEG frames here
TARGET_PORT  = 5005   # -> drivermonitor.py receives results here

MODEL_BASE = "yolov8s"   # n=nano (fastest, least accurate), s=small (good
                          # balance), m=medium, l=large, x=extra-large.
                          # s is ~2x slower than n but noticeably more
                          # accurate — reasonable tradeoff on Orin Nano.

MODEL_PATH        = f"{MODEL_BASE}.engine"   # TensorRT-optimized — far
                                              # faster on Jetson than raw
                                              # .pt (PyTorch). Run the
                                              # export step once first:
                                              #   model.export(format='engine', half=True, device=0)
                                              # Falls back to {MODEL_BASE}.pt
                                              # automatically if the
                                              # .engine file doesn't exist.
PHONE_CLASS_ID    = 67     # COCO class 67 = "cell phone"
PHONE_CONFIDENCE  = 0.25   # lowered temporarily for debugging — raise back to 0.45 once working

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
print("  PHONE DETECTION NODE - Dravixa DMS")
print("="*55)

_model_path = MODEL_PATH
if not os.path.exists(_model_path):
    print(f"[Phone Node] {_model_path} not found — falling back to "
          f"{MODEL_BASE}.pt (raw PyTorch, slower). To get the TensorRT "
          f"speedup, run once: python3 -c \"from ultralytics import YOLO; "
          f"YOLO('{MODEL_BASE}.pt').export(format='engine', half=True, device=0)\"")
    _model_path = f"{MODEL_BASE}.pt"

print(f"[Phone Node] Loading {MODEL_BASE} ({_model_path})...")
sys.stdout.flush()

try:
    model = YOLO(_model_path)

    # Ultralytics does NOT automatically move the model to GPU on load —
    # passing device=0 to inference calls handles per-call data placement,
    # but the model weights themselves stay on CPU unless explicitly
    # moved. This was the actual cause of slow (~5 FPS) inference even
    # though torch.cuda.is_available() returned True.
    if torch.cuda.is_available():
        model.to('cuda')
        actual_device = next(model.model.parameters()).device
        print(f"[Phone Node] Model loaded on device: {actual_device}")
        if 'cuda' not in str(actual_device):
            print("[Phone Node] WARNING: model.to('cuda') did not take "
                  "effect — still running on CPU, inference will be slow.")
    else:
        print("[Phone Node] WARNING: CUDA not available — running on CPU, "
              "inference will be slow (~1-5 FPS).")

    # Warmup — the very first inference call on a fresh CUDA context pays
    # a one-time cost (context init, kernel cache miss) that would
    # otherwise show up as an artificially slow first few frames once
    # real traffic starts. Run a couple dummy inferences now instead.
    print("[Phone Node] Warming up GPU...")
    _dummy = np.zeros((416, 416, 3), dtype=np.uint8)
    for _ in range(3):
        model(_dummy, verbose=False, half=True, device=0)
    print("[Phone Node] Warmup complete.")
except Exception as e:
    print(f"ERROR: Failed to load model: {e}")
    sys.exit(1)

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
        return (len(self.timestamps) - 1) / elapsed if elapsed > 0 else 0.0

fps_tracker = FPSTracker()

# ==========================================
# MAIN INFERENCE LOOP
# ==========================================

print(f"[Phone Node] Listening on UDP {UDP_IP}:{LISTEN_PORT}")
print(f"[Phone Node] Sending results to UDP {UDP_IP}:{TARGET_PORT}")
print(f"[Phone Node] Detecting: cell phone (COCO class {PHONE_CLASS_ID})")
print(f"[Phone Node] Confidence threshold: {PHONE_CONFIDENCE}")
print("[Phone Node] Press Ctrl+C to stop.\n")
sys.stdout.flush()

frames_processed = 0
frames_rejected  = 0

try:
    while True:
        try:
            data, addr = sock.recvfrom(65507)
            start_time = time.perf_counter()

            nparr = np.frombuffer(data, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

            if frame is None or frame.size == 0:
                frames_rejected += 1
                continue

            results = model(
                frame, verbose=False,
                conf=PHONE_CONFIDENCE,
                classes=[PHONE_CLASS_ID],
                half=True,    # FP16 — roughly 2x faster on Jetson's iGPU
                device=0,     # explicit GPU, skip any device-resolution overhead
            )

            found     = False
            best_conf = 0.0
            best_box  = None  # normalized [x1, y1, x2, y2], 0-1 range
            fh, fw    = frame.shape[:2]

            for r in results:
                if r.boxes is None or len(r.boxes) == 0:
                    continue
                for box in r.boxes:
                    conf = float(box.conf[0])
                    if conf > best_conf:
                        best_conf = conf
                        x1, y1, x2, y2 = map(float, box.xyxy[0])
                        # Normalize to 0-1 so drivermonitor.py can scale
                        # to ITS OWN frame resolution, which may differ
                        # from the 416x416 we resized down to for sending.
                        best_box = [x1 / fw, y1 / fh, x2 / fw, y2 / fh]
                    found = True

            result_payload = {
                "phone_detected":   found,
                "phone_confidence": round(best_conf, 4),
                "phone_box":        [round(c, 4) for c in best_box] if best_box else None,
            }
            result_json = json.dumps(result_payload)
            sock.sendto(result_json.encode('utf-8'), (UDP_IP, TARGET_PORT))

            fps_tracker.tick()
            inference_ms = (time.perf_counter() - start_time) * 1000
            frames_processed += 1

            sys.stdout.write(
                f"\r  Frames: {frames_processed:>5} | "
                f"FPS: {fps_tracker.fps:>5.1f} | "
                f"Phone: {'YES (' + str(round(best_conf*100)) + '%)' if found else 'no ':<12} | "
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
    print("\n\nStopping Phone Detection Node...")

finally:
    sock.close()
    print(f"Phone Node offline. Processed: {frames_processed} | Rejected: {frames_rejected}")
