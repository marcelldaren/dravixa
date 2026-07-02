"""
================================================================
 DRIVER MONITORING SYSTEM v3.0 (Jetson Edition)
 Fatigue + Emotion Detection for DRAVIXA
================================================================
 Changes from v2.0:
   - Auto EAR calibration on startup (personal threshold)
   - Mood bridge → dravixa.py LLM pipeline via UDP port 5003
   - Wider blink rate tolerance
   - Confidence threshold raised for emotion stress flag
   - Session fatigue time extended to 3h
================================================================
"""

import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

os.environ['TF_CPP_MIN_LOG_LEVEL'] = '3'
os.environ['TF_ENABLE_ONEDNN_OPTS'] = '0'
os.environ['GLOG_minloglevel']      = '2'

print("[Init] Loading dependencies...")
sys.stdout.flush()

import cv2
print("[Init] OpenCV loaded:", cv2.__version__)
sys.stdout.flush()

import time
import threading
import numpy as np
import socket
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, Callable
from enum import Enum

import mediapipe as mp
print("[Init] MediaPipe loaded")
sys.stdout.flush()

# NOTE: Phone detection (YOLOv8n) runs as a SEPARATE process —
# phone_detect_node.py — communicating over UDP, not imported here.
# This avoids a numpy version conflict: ultralytics + plain pip
# opencv-python need numpy 2.x, but the custom CUDA-built OpenCV
# used by this main pipeline was compiled against numpy 1.x's C API.
# Same pattern as driveremo.py for emotion detection.


# ==========================================
# 1. CONFIGURATION
# ==========================================

class AlertLevel(Enum):
    NORMAL   = 0
    CAUTION  = 1
    WARNING  = 2
    CRITICAL = 3


@dataclass
class DriverConfig:
    # --- Eye Aspect Ratio (EAR) ---
    # EAR_THRESHOLD is set dynamically via calibration at startup.
    # The value here is the fallback if calibration fails.
    EAR_THRESHOLD:          float = 0.21
    EAR_THRESHOLD_HAPPY:    float = 0.16
    EAR_CONSECUTIVE_FRAMES: int   = 18
    EAR_CRITICAL_FRAMES:    int   = 72

    # --- Mouth Aspect Ratio (MAR) ---
    MAR_THRESHOLD:       float = 0.45
    YAWN_DURATION_SEC:   float = 1.5
    YAWN_COUNT_WINDOW:   int   = 60
    YAWN_WARNING_COUNT:  int   = 4

    # --- PERCLOS ---
    PERCLOS_WINDOW_SEC: int   = 60
    PERCLOS_WARNING:    float = 0.20
    PERCLOS_CRITICAL:   float = 0.35

    # --- Blink Rate (wider tolerance than v2) ---
    BLINK_RATE_LOW:  int = 15   # was 8
    BLINK_RATE_HIGH: int = 40   # was 30

    # --- Head Pose ---
    HEAD_YAW_THRESHOLD:       float = 30.0
    HEAD_PITCH_DOWN_THRESHOLD: float = 25.0
    HEAD_DISTRACTION_SEC:      float = 2.5

    # --- Phone Detection (separate process, phone_detect_node.py) ---
    # Detection itself runs in a standalone process over UDP (see
    # phone_detect_node.py) — these are just the client-side settings
    # for how often to send frames and how to interpret results.
    PHONE_SEND_PORT  = 5004   # -> phone_detect_node.py (JPEG frames)
    PHONE_LISTEN_PORT = 5005  # <- phone_detect_node.py (detection results)
    # Send a frame for phone detection every Nth processed frame — it's
    # a separate process with its own inference cost, so we don't need
    # to send every single frame to still catch sustained phone use
    # (only fast, momentary glances might be missed).
    PHONE_CHECK_EVERY_N_FRAMES: int = 2
    # Driver must be seen holding/looking at phone for this long
    # (consecutive detections where phone was found) before it counts
    # as a real distraction event, not a single false-positive frame.
    PHONE_DISTRACTION_SEC:   float = 1.5
    # If no result arrives from phone_detect_node.py within this many
    # seconds, treat the node as offline and stop blocking on it.
    PHONE_RESULT_TIMEOUT:    float = 2.0

    # --- Emotion ---
    EMOTION_CHECK_INTERVAL: float = 1.0
    # Raised confidence for stress flag (was 0.55 in v2, reduces false positives)
    STRESS_CONFIDENCE:      float = 0.70
    STRESS_EMOTIONS               = {"Anger", "Sad"}

    # --- Session ---
    # Start adding fatigue score after 3h (was 2h)
    FATIGUE_FROM_TIME_HOURS: float = 3.0

    # --- Camera (Jetson V4L2) ---
    CAMERA_INDEX:        int   = 0
    CAMERA_BACKEND:      int   = cv2.CAP_V4L2
    FRAME_WIDTH:         int   = 640
    FRAME_HEIGHT:        int   = 480
    TARGET_FPS:          int   = 30
    CAMERA_OPEN_TIMEOUT: float = 10.0

    # --- Reliability ---
    MAX_CONSECUTIVE_FAILURES: int = 15
    FRAME_SKIP:               int = 1

    # --- EAR Calibration ---
    CALIBRATION_SECONDS:  float = 8.0    # how long to collect samples
    CALIBRATION_STD_MULT: float = 2.5    # threshold = mean - (mult * std)
    CALIBRATION_MIN:      float = 0.15   # never go below this
    CALIBRATION_MAX:      float = 0.27   # never go above this (too permissive)


# ==========================================
# 2. MEDIAPIPE LANDMARKS
# ==========================================

RIGHT_EYE        = [33, 160, 158, 133, 153, 144]
LEFT_EYE         = [362, 385, 387, 263, 373, 380]
NOSE_TIP         = 1
CHIN             = 199
LEFT_EYE_CORNER  = 33
RIGHT_EYE_CORNER = 263
LEFT_MOUTH       = 61
RIGHT_MOUTH      = 291


# ==========================================
# 3. GEOMETRY
# ==========================================

def euclidean_distance(p1, p2) -> float:
    return float(np.linalg.norm(np.array(p1) - np.array(p2)))

def calculate_ear(landmarks, eye_indices, w, h) -> float:
    try:
        pts = [(landmarks[i].x * w, landmarks[i].y * h) for i in eye_indices]
        v1  = euclidean_distance(pts[1], pts[5])
        v2  = euclidean_distance(pts[2], pts[4])
        hz  = euclidean_distance(pts[0], pts[3])
        return (v1 + v2) / (2.0 * hz) if hz > 0 else 0.0
    except Exception:
        return 0.0

def calculate_mar(landmarks, w, h) -> float:
    try:
        top   = (landmarks[13].x * w,  landmarks[13].y * h)
        bot   = (landmarks[14].x * w,  landmarks[14].y * h)
        left  = (landmarks[61].x * w,  landmarks[61].y * h)
        right = (landmarks[291].x * w, landmarks[291].y * h)
        return euclidean_distance(top, bot) / euclidean_distance(left, right) \
               if euclidean_distance(left, right) > 0 else 0.0
    except Exception:
        return 0.0

def estimate_head_pose(landmarks, w, h):
    try:
        model_pts = np.array([
            ( 0.0,    0.0,    0.0),
            ( 0.0, -330.0,  -65.0),
            (-225.0, 170.0, -135.0),
            ( 225.0, 170.0, -135.0),
            (-150.0,-150.0, -125.0),
            ( 150.0,-150.0, -125.0),
        ], dtype=np.float64)

        image_pts = np.array([
            (landmarks[NOSE_TIP].x * w,          landmarks[NOSE_TIP].y * h),
            (landmarks[CHIN].x * w,               landmarks[CHIN].y * h),
            (landmarks[LEFT_EYE_CORNER].x * w,    landmarks[LEFT_EYE_CORNER].y * h),
            (landmarks[RIGHT_EYE_CORNER].x * w,   landmarks[RIGHT_EYE_CORNER].y * h),
            (landmarks[LEFT_MOUTH].x * w,         landmarks[LEFT_MOUTH].y * h),
            (landmarks[RIGHT_MOUTH].x * w,        landmarks[RIGHT_MOUTH].y * h),
        ], dtype=np.float64)

        cam_mat = np.array([
            [w, 0, w / 2],
            [0, w, h / 2],
            [0, 0, 1],
        ], dtype=np.float64)

        ok, rvec, _ = cv2.solvePnP(
            model_pts, image_pts, cam_mat,
            np.zeros((4, 1)), flags=cv2.SOLVEPNP_ITERATIVE
        )
        if not ok:
            return 0.0, 0.0, 0.0

        rmat, _ = cv2.Rodrigues(rvec)
        sy = np.sqrt(rmat[0, 0]**2 + rmat[1, 0]**2)

        if sy > 1e-6:
            pitch = np.degrees(np.arctan2(-rmat[2, 0], sy))
            yaw   = np.degrees(np.arctan2(rmat[1, 0], rmat[0, 0]))
            roll  = np.degrees(np.arctan2(rmat[2, 1], rmat[2, 2]))
        else:
            pitch = np.degrees(np.arctan2(-rmat[2, 0], sy))
            yaw   = 0.0
            roll  = np.degrees(np.arctan2(-rmat[1, 2], rmat[1, 1]))

        return yaw, pitch, roll
    except Exception:
        return 0.0, 0.0, 0.0


# ==========================================
# 4. FPS TRACKER
# ==========================================

class FPSTracker:
    def __init__(self, window: int = 30):
        self.timestamps = deque(maxlen=window)

    def tick(self):
        self.timestamps.append(time.perf_counter())

    @property
    def fps(self) -> float:
        if len(self.timestamps) < 2:
            return 0.0
        elapsed = self.timestamps[-1] - self.timestamps[0]
        return (len(self.timestamps) - 1) / elapsed if elapsed > 0 else 0.0


# ==========================================
# 5. DRIVER STATE
# ==========================================

@dataclass
class DriverState:
    timestamp:          float      = field(default_factory=time.time)
    ear:                float      = 0.0
    mar:                float      = 0.0
    yaw:                float      = 0.0
    pitch:              float      = 0.0
    roll:               float      = 0.0
    perclos:            float      = 0.0
    blink_rate:         int        = 0
    yawn_count_1min:    int        = 0
    eyes_closed:        bool       = False
    is_yawning:         bool       = False
    head_distracted:    bool       = False
    head_nodding:       bool       = False
    phone_detected:     bool       = False
    phone_confidence:   float      = 0.0
    phone_box:          Optional[list] = None  # normalized [x1,y1,x2,y2], 0-1 range
    face_detected:      bool       = True
    emotion:            str        = "Neutral"
    emotion_confidence: float      = 0.0
    is_stressed:        bool       = False
    fatigue_score:      float      = 0.0
    alert_level:        AlertLevel = AlertLevel.NORMAL
    alert_message:      str        = ""

    def to_dict(self) -> dict:
        return {
            "timestamp":     self.timestamp,
            "ear":           round(self.ear, 3),
            "mar":           round(self.mar, 3),
            "head_yaw":      round(self.yaw, 1),
            "head_pitch":    round(self.pitch, 1),
            "phone_detected": self.phone_detected,
            "perclos":       round(self.perclos * 100, 1),
            "blink_rate":    self.blink_rate,
            "yawns_1min":    self.yawn_count_1min,
            "emotion":       self.emotion,
            "fatigue_score": round(self.fatigue_score, 1),
            "alert_level":   self.alert_level.name,
            "alert_message": self.alert_message,
            "face_detected": self.face_detected,
        }


# ==========================================
# 6. EAR CALIBRATION
# ==========================================

def calibrate_ear(config: DriverConfig) -> float:
    """
    Open camera, collect EAR samples for CALIBRATION_SECONDS with eyes open,
    then set threshold = mean - (CALIBRATION_STD_MULT * std).
    Returns the calibrated threshold, clamped to [CALIBRATION_MIN, CALIBRATION_MAX].
    Falls back to config.EAR_THRESHOLD if camera or face not found.
    """
    print("\n" + "="*50)
    print(" EAR CALIBRATION")
    print(" Keep your eyes OPEN normally.")
    print(f" Collecting for {config.CALIBRATION_SECONDS:.0f} seconds...")
    print("="*50)
    sys.stdout.flush()

    mp_face_mesh = mp.solutions.face_mesh
    face_mesh    = mp_face_mesh.FaceMesh(
        max_num_faces=1,
        refine_landmarks=True,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    cap = cv2.VideoCapture(config.CAMERA_INDEX, config.CAMERA_BACKEND)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  config.FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FRAME_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS,          config.TARGET_FPS)
    cap.set(cv2.CAP_PROP_BUFFERSIZE,   1)

    if not cap.isOpened():
        print("[Calibration] WARN: Camera not available — using default EAR threshold.")
        face_mesh.close()
        return config.EAR_THRESHOLD

    ears      = []
    t0        = time.time()
    countdown = int(config.CALIBRATION_SECONDS)

    while time.time() - t0 < config.CALIBRATION_SECONDS:
        ret, frame = cap.read()
        if not ret:
            continue

        elapsed   = time.time() - t0
        remaining = max(0, config.CALIBRATION_SECONDS - elapsed)

        h, w = frame.shape[:2]
        rgb  = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        res  = face_mesh.process(rgb)

        ear_val = None
        if res.multi_face_landmarks:
            lm      = res.multi_face_landmarks[0].landmark
            l_ear   = calculate_ear(lm, LEFT_EYE,  w, h)
            r_ear   = calculate_ear(lm, RIGHT_EYE, w, h)
            ear_val = (l_ear + r_ear) / 2.0
            ears.append(ear_val)

        ear_str = f"{ear_val:.3f}" if ear_val is not None else "---"
        sys.stdout.write(
            f"\r  [{len(ears):>3} samples] EAR: {ear_str}"
            f"  |  {remaining:.1f}s remaining   "
        )
        sys.stdout.flush()

    cap.release()
    face_mesh.close()
    print()

    if len(ears) < 20:
        print(f"[Calibration] Not enough samples ({len(ears)}) — using default {config.EAR_THRESHOLD:.3f}")
        return config.EAR_THRESHOLD

    mean      = float(np.mean(ears))
    std       = float(np.std(ears))
    threshold = mean - config.CALIBRATION_STD_MULT * std
    threshold = float(np.clip(threshold, config.CALIBRATION_MIN, config.CALIBRATION_MAX))

    print(f"[Calibration] Your EAR: mean={mean:.3f}  std={std:.3f}")
    print(f"[Calibration] Threshold set to: {threshold:.3f}  (fallback was {config.EAR_THRESHOLD:.3f})")
    print("="*50 + "\n")
    sys.stdout.flush()

    return threshold


# ==========================================
# 7. MAIN DRIVER MONITOR
# ==========================================

class DriverMonitor:
    def __init__(
        self,
        config:         Optional[DriverConfig]  = None,
        alert_callback: Optional[Callable]       = None,
        show_display:   bool                     = False,
        skip_calibration: bool                   = False,
    ):
        self.config         = config or DriverConfig()
        self.alert_callback = alert_callback
        self.show_display   = show_display

        # ── EAR calibration on startup ────────────────────────
        if skip_calibration:
            print(f"[Monitor] Skipping calibration — EAR threshold: {self.config.EAR_THRESHOLD:.3f}")
        else:
            calibrated = calibrate_ear(self.config)
            self.config.EAR_THRESHOLD       = calibrated
            # Happy threshold stays proportional
            self.config.EAR_THRESHOLD_HAPPY = max(
                self.config.CALIBRATION_MIN,
                calibrated - 0.05
            )

        # ── UDP IPC ───────────────────────────────────────────
        # Receive emotion results from driveremo.py on port 5001
        # Send mood events to dravixa.py on port 5003
        # Send/receive phone detection frames+results on ports 5004/5005
        self.UDP_IP      = "127.0.0.1"
        self.SEND_PORT   = 5002   # → driveremo.py (face crops)
        self.LISTEN_PORT = 5001   # ← driveremo.py (emotion results)
        self.MOOD_PORT   = 5003   # → dravixa.py   (mood/alert events)

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind((self.UDP_IP, self.LISTEN_PORT))
            self.sock.settimeout(0.5)
        except Exception as e:
            print(f"WARN: Could not bind UDP port {self.LISTEN_PORT}: {e}")

        self.mood_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # ── Phone detection IPC (separate process, separate venv) ──
        # phone_detect_node.py runs standalone with its own numpy/
        # opencv to avoid the version conflict with this pipeline's
        # custom CUDA-built OpenCV. We send it JPEG frames and listen
        # for JSON detection results, same pattern as driveremo.py.
        self.phone_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.phone_sock.bind((self.UDP_IP, self.config.PHONE_LISTEN_PORT))
            self.phone_sock.settimeout(0.1)
        except Exception as e:
            print(f"WARN: Could not bind phone detection port "
                  f"{self.config.PHONE_LISTEN_PORT}: {e}")
        self.phone_result_thread_running = False

        # ── MediaPipe ─────────────────────────────────────────
        self.mp_face_mesh = mp.solutions.face_mesh
        self.face_mesh    = self.mp_face_mesh.FaceMesh(
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

        # ── Phone detection state ──────────────────────────────
        self._frame_count_for_phone = 0
        self._phone_seen_since      = 0  # timestamp phone first seen, 0 = not seeing it
        self._last_phone_result     = {"phone_detected": False, "phone_confidence": 0.0, "phone_box": None}
        self._last_phone_result_time = 0  # for staleness/timeout detection

        # ── State ─────────────────────────────────────────────
        self.session_start = time.time()
        self.current_state = DriverState()
        self.state_lock    = threading.Lock()

        # ── Buffers ───────────────────────────────────────────
        self.ear_history    = deque(maxlen=self.config.TARGET_FPS * 3)
        self.mar_history    = deque(maxlen=self.config.TARGET_FPS * 2)
        self.perclos_buffer = deque(
            maxlen=self.config.TARGET_FPS * self.config.PERCLOS_WINDOW_SEC
        )

        # ── Blink / Yawn ──────────────────────────────────────
        self.blink_timestamps  = deque(maxlen=100)
        self.eye_closed_frames = 0
        self.was_eye_closed    = False
        self.yawn_timestamps   = deque(maxlen=20)
        self.yawn_start_time   = 0
        self.in_yawn           = False

        # ── Head ──────────────────────────────────────────────
        self.distraction_start = 0
        self.nodding_start     = 0

        # ── Emotion IPC ───────────────────────────────────────
        self.last_emotion_check     = 0
        self.emotion_result         = {"emotion": "Neutral", "confidence": 0.0}
        self.emotion_thread_running = False

        # ── FPS ───────────────────────────────────────────────
        self.fps_tracker   = FPSTracker(window=30)
        self.process_fps   = FPSTracker(window=30)
        self.frame_counter = 0

        # ── Alert cooldowns ───────────────────────────────────
        self.running          = False
        self.capture_thread   = None
        self.last_alert_level = AlertLevel.NORMAL
        self.last_alert_time  = {
            AlertLevel.CAUTION:  0,
            AlertLevel.WARNING:  0,
            AlertLevel.CRITICAL: 0,
        }
        self.ALERT_COOLDOWN = {
            AlertLevel.CAUTION:  45,
            AlertLevel.WARNING:  30,
            AlertLevel.CRITICAL: 5,
        }

        # ── Mood bridge cooldowns (→ dravixa.py) ─────────────
        self._mood_last = {
            "Sad":    0,
            "Anger":  0,
            "fatigue_warning":  0,
            "fatigue_critical": 0,
            "phone_detected":   0,
        }
        self._MOOD_COOLDOWN = {
            "Sad":    120,   # 2 min
            "Anger":  120,
            "fatigue_warning":  180,  # 3 min
            "fatigue_critical": 10,
            "phone_detected":   30,   # 30 sec — frequent enough to keep nudging, not spammy
        }

    # ------------------------------------------
    # UDP receiver — emotion results from driveremo.py
    # ------------------------------------------
    def _udp_receiver_worker(self):
        while self.emotion_thread_running:
            try:
                data, _ = self.sock.recvfrom(1024)
                result  = json.loads(data.decode('utf-8'))
                self.emotion_result = {
                    "emotion":    result.get("emotion",    "Neutral"),
                    "confidence": float(result.get("confidence", 0.0)),
                }
            except socket.timeout:
                continue
            except Exception:
                pass

    # ------------------------------------------
    # Send mood event → dravixa.py UDP 5003
    # ------------------------------------------
    def _send_mood_event(self, event_type: str, payload: dict):
        try:
            pkt = json.dumps({"type": event_type, **payload}).encode()
            self.mood_sock.sendto(pkt, (self.UDP_IP, self.MOOD_PORT))
        except Exception:
            pass

    # ------------------------------------------
    # Phone detection — UDP client to phone_detect_node.py
    # ------------------------------------------
    def _phone_result_receiver_worker(self):
        """Background thread: listen for JSON detection results from
        phone_detect_node.py and cache the latest one. Same pattern as
        _udp_receiver_worker() for emotion results."""
        while self.phone_result_thread_running:
            try:
                data, _ = self.phone_sock.recvfrom(1024)
                result  = json.loads(data.decode('utf-8'))
                self._last_phone_result = {
                    "phone_detected":   bool(result.get("phone_detected", False)),
                    "phone_confidence": float(result.get("phone_confidence", 0.0)),
                    "phone_box":        result.get("phone_box"),  # normalized [x1,y1,x2,y2] or None
                }
                self._last_phone_result_time = time.time()
            except socket.timeout:
                continue
            except Exception:
                pass

    def _send_frame_for_phone_detection(self, frame):
        """Encode and send a frame to phone_detect_node.py for inference.
        Fire-and-forget — the result arrives asynchronously via the
        receiver thread above, picked up on a later call to _process_frame."""
        try:
            small = cv2.resize(frame, (416, 416))  # keep payload small/fast
            _, buf = cv2.imencode('.jpg', small, [cv2.IMWRITE_JPEG_QUALITY, 75])
            self.phone_sock.sendto(buf.tobytes(),
                                    (self.UDP_IP, self.config.PHONE_SEND_PORT))
        except Exception:
            pass

    def _get_phone_result(self) -> tuple:
        """Return (detected, confidence, box) from the latest result
        received from phone_detect_node.py. box is a normalized
        [x1,y1,x2,y2] list (0-1 range) or None. If no result has arrived
        recently (node not running, or still starting up), returns
        (False, 0.0, None) rather than blocking — phone detection
        degrades gracefully if the separate process isn't up."""
        if time.time() - self._last_phone_result_time > self.config.PHONE_RESULT_TIMEOUT:
            return False, 0.0, None
        return (self._last_phone_result["phone_detected"],
                self._last_phone_result["phone_confidence"],
                self._last_phone_result.get("phone_box"))

    # ------------------------------------------
    # Fatigue scoring (fixed thresholds)
    # ------------------------------------------
    def _calculate_fatigue_score(self, state: DriverState) -> float:
        score = 0.0

        # PERCLOS
        if state.perclos >= self.config.PERCLOS_CRITICAL:   score += 40
        elif state.perclos >= self.config.PERCLOS_WARNING:  score += 25
        elif state.perclos >= 0.10:                         score += 10

        # Yawning
        if state.yawn_count_1min >= self.config.YAWN_WARNING_COUNT: score += 20
        elif state.yawn_count_1min >= 2:                             score += 10
        elif state.yawn_count_1min >= 1:                             score += 5

        # Eye closure duration
        if self.eye_closed_frames >= self.config.EAR_CRITICAL_FRAMES:      score += 20
        elif self.eye_closed_frames >= self.config.EAR_CONSECUTIVE_FRAMES: score += 12

        if state.head_distracted: score += 5

        # Phone use is a more serious distraction than a quick head turn —
        # weighted higher in the fatigue/distraction score.
        if state.phone_detected: score += 15

        # Session duration (now starts at 3h)
        hours = (time.time() - self.session_start) / 3600
        if hours >= 4:                                           score += 10
        elif hours >= self.config.FATIGUE_FROM_TIME_HOURS:      score += 5

        # Emotion stress (only if confident)
        if state.is_stressed: score += 8

        # Abnormal blink rate (wider range now)
        if (state.blink_rate < self.config.BLINK_RATE_LOW or
                state.blink_rate > self.config.BLINK_RATE_HIGH):
            score += 5

        return min(score, 100.0)

    # ------------------------------------------
    # Alert determination
    # ------------------------------------------
    def _determine_alert_level(self, state: DriverState) -> tuple:
        if self.eye_closed_frames >= self.config.EAR_CRITICAL_FRAMES:
            return AlertLevel.CRITICAL, "WAKE UP! Eyes closed too long!"

        if state.fatigue_score >= 75:
            return AlertLevel.CRITICAL, "Severely fatigued. Pull over NOW."

        if state.fatigue_score >= 50:
            return AlertLevel.WARNING, "You look tired. Find a rest stop soon."

        if state.yawn_count_1min >= self.config.YAWN_WARNING_COUNT:
            return AlertLevel.WARNING, f"Yawned {state.yawn_count_1min}x. Consider a break."

        # Phone use ranks above general fatigue/head-distraction CAUTION —
        # it's a more direct, immediate safety risk than a quick head turn.
        if state.phone_detected:
            return AlertLevel.WARNING, "Phone detected. Please keep your eyes on the road."

        if state.fatigue_score >= 30:
            return AlertLevel.CAUTION, "Signs of fatigue. Stay alert."

        if state.head_distracted:
            return AlertLevel.CAUTION, "Eyes on the road."

        # Emotion alerts — higher confidence threshold (0.70) to reduce false positives
        if state.emotion == "Anger" and state.emotion_confidence > self.config.STRESS_CONFIDENCE:
            return AlertLevel.CAUTION, "You seem angry. Take a breath."
        if state.emotion == "Sad" and state.emotion_confidence > self.config.STRESS_CONFIDENCE:
            return AlertLevel.CAUTION, "You seem distressed. Drive carefully."

        if state.perclos >= self.config.PERCLOS_WARNING:
            return AlertLevel.CAUTION, "Eyes closing often. Stay focused."

        return AlertLevel.NORMAL, ""

    # ------------------------------------------
    # Mood bridge — fires events to dravixa.py
    # ------------------------------------------
    def _check_mood_bridge(self, state: DriverState):
        now     = time.time()
        emotion = state.emotion
        conf    = state.emotion_confidence
        level   = state.alert_level
        fatigue = state.fatigue_score

        # CRITICAL fatigue
        if level == AlertLevel.CRITICAL:
            key = "fatigue_critical"
            if now - self._mood_last[key] > self._MOOD_COOLDOWN[key]:
                self._mood_last[key] = now
                self._send_mood_event("driver_alert", {
                    "alert_level": "CRITICAL",
                    "message":     state.alert_message,
                    "fatigue":     fatigue,
                    "emotion":     emotion,
                })

        # WARNING fatigue
        elif level == AlertLevel.WARNING and fatigue >= 50:
            key = "fatigue_warning"
            if now - self._mood_last[key] > self._MOOD_COOLDOWN[key]:
                self._mood_last[key] = now
                self._send_mood_event("driver_alert", {
                    "alert_level": "WARNING",
                    "message":     state.alert_message,
                    "fatigue":     fatigue,
                    "emotion":     emotion,
                })

        # Phone detected — separate from the general fatigue WARNING path
        # above since phone use can fire at WARNING level without fatigue
        # actually being >= 50 (it's its own distraction signal).
        if state.phone_detected:
            key = "phone_detected"
            if now - self._mood_last[key] > self._MOOD_COOLDOWN[key]:
                self._mood_last[key] = now
                self._send_mood_event("driver_alert", {
                    "alert_level": "WARNING",
                    "message":     "Phone detected. Please keep your eyes on the road.",
                    "fatigue":     fatigue,
                    "emotion":     emotion,
                })

        # SAD mood
        if emotion == "Sad" and conf > self.config.STRESS_CONFIDENCE:
            key = "Sad"
            if now - self._mood_last[key] > self._MOOD_COOLDOWN[key]:
                self._mood_last[key] = now
                self._send_mood_event("driver_mood", {
                    "emotion":    "Sad",
                    "confidence": conf,
                    "fatigue":    fatigue,
                })

        # ANGRY mood
        elif emotion == "Anger" and conf > self.config.STRESS_CONFIDENCE:
            key = "Anger"
            if now - self._mood_last[key] > self._MOOD_COOLDOWN[key]:
                self._mood_last[key] = now
                self._send_mood_event("driver_mood", {
                    "emotion":    "Anger",
                    "confidence": conf,
                    "fatigue":    fatigue,
                })

    # ------------------------------------------
    # Frame processing
    # ------------------------------------------
    def _process_frame(self, frame) -> DriverState:
        state = DriverState()
        h, w  = frame.shape[:2]
        rgb   = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        res   = self.face_mesh.process(rgb)

        if not res.multi_face_landmarks:
            state.face_detected = False
            state.alert_level   = AlertLevel.CAUTION
            state.alert_message = "Face not detected."
            return state

        lm = res.multi_face_landmarks[0].landmark

        # EAR
        left_ear  = calculate_ear(lm, LEFT_EYE,  w, h)
        right_ear = calculate_ear(lm, RIGHT_EYE, w, h)
        ear       = (left_ear + right_ear) / 2.0
        state.ear = ear
        self.ear_history.append(ear)
        smoothed_ear = float(np.mean(list(self.ear_history)[-5:])) if self.ear_history else ear

        current_emotion    = self.emotion_result.get("emotion",    "Neutral")
        current_conf       = self.emotion_result.get("confidence", 0.0)
        is_happy_confident = (current_emotion == "Happiness" and current_conf >= 0.45)

        ear_threshold = (self.config.EAR_THRESHOLD_HAPPY
                         if is_happy_confident
                         else self.config.EAR_THRESHOLD)

        eyes_closed   = smoothed_ear < ear_threshold
        state.eyes_closed = eyes_closed

        if eyes_closed:
            if not is_happy_confident:
                self.eye_closed_frames += 1
        else:
            if self.was_eye_closed and 2 <= self.eye_closed_frames <= 10:
                self.blink_timestamps.append(time.time())
            self.eye_closed_frames = 0
        self.was_eye_closed = eyes_closed

        self.perclos_buffer.append(1 if eyes_closed else 0)
        state.perclos = (sum(self.perclos_buffer) / len(self.perclos_buffer)
                         if self.perclos_buffer else 0.0)

        now = time.time()
        state.blink_rate = sum(1 for t in self.blink_timestamps if now - t <= 60)

        # MAR
        mar = calculate_mar(lm, w, h)
        state.mar = mar
        self.mar_history.append(mar)
        smoothed_mar = float(np.mean(list(self.mar_history)[-5:])) if self.mar_history else mar
        mouth_open   = smoothed_mar > self.config.MAR_THRESHOLD

        if mouth_open and not self.in_yawn:
            self.yawn_start_time = now
            self.in_yawn         = True
        elif mouth_open and self.in_yawn:
            if now - self.yawn_start_time >= self.config.YAWN_DURATION_SEC:
                if not self.yawn_timestamps or now - self.yawn_timestamps[-1] > 3:
                    self.yawn_timestamps.append(now)
                    state.is_yawning = True
        elif not mouth_open:
            self.in_yawn = False
        state.yawn_count_1min = sum(1 for t in self.yawn_timestamps if now - t <= 60)

        # Head pose
        yaw, pitch, roll = estimate_head_pose(lm, w, h)
        state.yaw   = yaw
        state.pitch = pitch
        state.roll  = roll

        if abs(yaw) > self.config.HEAD_YAW_THRESHOLD:
            if self.distraction_start == 0:
                self.distraction_start = now
            elif now - self.distraction_start >= self.config.HEAD_DISTRACTION_SEC:
                state.head_distracted = True
        else:
            self.distraction_start = 0

        # Phone detection — send a frame to phone_detect_node.py every
        # Nth frame (async, fire-and-forget), and read whatever the most
        # recent result is. This never blocks _process_frame waiting on
        # the separate process, so a slow/offline phone node can't stall
        # the main MediaPipe pipeline.
        self._frame_count_for_phone += 1
        if self._frame_count_for_phone % self.config.PHONE_CHECK_EVERY_N_FRAMES == 0:
            self._send_frame_for_phone_detection(frame)

        found, conf, box = self._get_phone_result()

        if found:
            if self._phone_seen_since == 0:
                self._phone_seen_since = now
            if now - self._phone_seen_since >= self.config.PHONE_DISTRACTION_SEC:
                state.phone_detected   = True
                state.phone_confidence = conf
                state.phone_box        = box
        else:
            self._phone_seen_since = 0

        # Send face crop → emotion node
        if now - self.last_emotion_check >= self.config.EMOTION_CHECK_INTERVAL:
            self.last_emotion_check = now
            x_min = int(min(lm_.x for lm_ in lm) * w)
            x_max = int(max(lm_.x for lm_ in lm) * w)
            y_min = int(min(lm_.y for lm_ in lm) * h)
            y_max = int(max(lm_.y for lm_ in lm) * h)
            pad   = 30
            y1, y2 = max(0, y_min - pad), min(h, y_max + pad)
            x1, x2 = max(0, x_min - pad), min(w, x_max + pad)
            face_crop = frame[y1:y2, x1:x2]
            if face_crop.size != 0:
                face_crop = cv2.resize(face_crop, (224, 224))
                _, buf = cv2.imencode('.jpg', face_crop,
                                      [cv2.IMWRITE_JPEG_QUALITY, 80])
                try:
                    self.sock.sendto(buf.tobytes(), (self.UDP_IP, self.SEND_PORT))
                except Exception:
                    pass

        # Apply emotion result — with raised confidence for stress flag
        state.emotion            = self.emotion_result["emotion"]
        state.emotion_confidence = self.emotion_result["confidence"]
        state.is_stressed        = (
            state.emotion in self.config.STRESS_EMOTIONS and
            state.emotion_confidence >= self.config.STRESS_CONFIDENCE
        )

        state.fatigue_score                    = self._calculate_fatigue_score(state)
        state.alert_level, state.alert_message = self._determine_alert_level(state)
        return state

    # ------------------------------------------
    # HUD drawing
    # ------------------------------------------
    def _draw_debug(self, frame, state: DriverState, fps: float):
        h, w = frame.shape[:2]

        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (w, 220), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.45, frame, 0.55, 0, frame)

        col = (255, 255, 255)
        if   state.alert_level == AlertLevel.CRITICAL: col = (0,   0,   255)
        elif state.alert_level == AlertLevel.WARNING:  col = (0, 165,   255)
        elif state.alert_level == AlertLevel.CAUTION:  col = (0, 255,   255)

        font  = cv2.FONT_HERSHEY_SIMPLEX
        small = 0.55
        lh    = 25

        cv2.putText(frame, f"EAR:     {state.ear:.3f} (thr:{self.config.EAR_THRESHOLD:.3f})",
                    (10, lh*1), font, small, col, 2)
        cv2.putText(frame, f"MAR:     {state.mar:.3f}",            (10, lh*2), font, small, col, 2)
        cv2.putText(frame, f"PERCLOS: {state.perclos*100:.1f}%",   (10, lh*3), font, small, col, 2)
        cv2.putText(frame, f"Yawns:   {state.yawn_count_1min}/min",(10, lh*4), font, small, col, 2)
        cv2.putText(frame, f"Blinks:  {state.blink_rate}/min",     (10, lh*5), font, small, col, 2)
        cv2.putText(frame,
                    f"Head Y/P:{state.yaw:.0f}/{state.pitch:.0f}deg",
                    (10, lh*6), font, small, col, 2)
        cv2.putText(frame,
                    f"Emotion: {state.emotion} ({state.emotion_confidence*100:.0f}%)",
                    (10, lh*7), font, small, col, 2)
        phone_col = (0, 0, 255) if state.phone_detected else (255, 255, 255)
        cv2.putText(frame,
                    f"Phone:   {'DETECTED ' + str(round(state.phone_confidence*100)) + '%' if state.phone_detected else 'no'}",
                    (10, lh*8), font, small, phone_col, 2)

        # Draw the actual bounding box on the live frame — phone_box is
        # normalized [x1,y1,x2,y2] (0-1 range) from phone_detect_node.py,
        # scaled here to this frame's real resolution since the node
        # runs inference on a resized-down 416x416 copy.
        if state.phone_detected and state.phone_box:
            bx1, by1, bx2, by2 = state.phone_box
            x1, y1 = int(bx1 * w), int(by1 * h)
            x2, y2 = int(bx2 * w), int(by2 * h)
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
            label = f"phone {state.phone_confidence*100:.0f}%"
            label_size, _ = cv2.getTextSize(label, font, 0.55, 2)
            label_y = max(y1 - 8, label_size[1] + 4)
            cv2.rectangle(frame, (x1, label_y - label_size[1] - 6),
                          (x1 + label_size[0] + 6, label_y + 4), (0, 0, 255), -1)
            cv2.putText(frame, label, (x1 + 3, label_y), font, 0.55, (255, 255, 255), 2)

        fps_col = ((0,255,0) if fps >= 25 else
                   (0,165,255) if fps >= 15 else (0,0,255))
        cv2.putText(frame, f"FPS: {fps:.1f}", (w-110, lh*1), font, small, fps_col, 2)

        bar_w = int((state.fatigue_score / 100) * (w - 20))
        bar_c = ((0,255,0) if state.fatigue_score < 30 else
                 (0,255,255) if state.fatigue_score < 50 else
                 (0,165,255) if state.fatigue_score < 75 else (0,0,255))
        cv2.rectangle(frame, (10, h-40), (10+bar_w, h-20), bar_c, -1)
        cv2.rectangle(frame, (10, h-40), (w-10,     h-20), (128,128,128), 1)
        cv2.putText(frame, f"Fatigue: {state.fatigue_score:.0f}/100",
                    (10, h-48), font, small, (255,255,255), 2)

        if state.alert_message:
            if state.alert_level == AlertLevel.CRITICAL:
                if int(time.time() * 2) % 2 == 0:
                    cv2.rectangle(frame, (0, h-75), (w, h-55), (0,0,200), -1)
            cv2.putText(frame, state.alert_message,
                        (10, h-58), font, 0.6, col, 2)
        return frame

    # ------------------------------------------
    # Alert firing
    # ------------------------------------------
    def _handle_alert(self, state: DriverState):
        level = state.alert_level
        now   = time.time()
        if level == AlertLevel.NORMAL:
            self.last_alert_level = AlertLevel.NORMAL
            return
        cooldown  = self.ALERT_COOLDOWN.get(level, 30)
        last      = self.last_alert_time.get(level, 0)
        escalated = level.value > self.last_alert_level.value
        if escalated or (now - last >= cooldown):
            if self.alert_callback:
                try:
                    self.alert_callback(state)
                except Exception as e:
                    print(f"WARN: Alert callback error: {e}")
            self.last_alert_time[level] = now
            self.last_alert_level       = level

    # ------------------------------------------
    # Capture loop
    # ------------------------------------------
    def _capture_loop(self):
        print(f"[Monitor] Opening camera {self.config.CAMERA_INDEX} (V4L2)...")
        sys.stdout.flush()

        cap = cv2.VideoCapture(self.config.CAMERA_INDEX, self.config.CAMERA_BACKEND)
        if not cap.isOpened():
            print(f"ERROR: Camera {self.config.CAMERA_INDEX} won't open.")
            self.running = False
            return

        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  self.config.FRAME_WIDTH)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.FRAME_HEIGHT)
        cap.set(cv2.CAP_PROP_FPS,          self.config.TARGET_FPS)
        cap.set(cv2.CAP_PROP_BUFFERSIZE,   1)

        print("[Monitor] Warming up camera...")
        sys.stdout.flush()
        warmup_ok = False
        t0        = time.time()
        while time.time() - t0 < self.config.CAMERA_OPEN_TIMEOUT:
            ret, frame = cap.read()
            if ret and frame is not None:
                h, w = frame.shape[:2]
                print(f"[Monitor] Camera ready: {w}x{h} @ {self.config.TARGET_FPS}fps")
                warmup_ok = True
                break
            time.sleep(0.1)

        if not warmup_ok:
            print("ERROR: Camera opened but no frames received.")
            cap.release()
            self.running = False
            return

        print("[Monitor] Running. Press 'q' in window to quit.")
        sys.stdout.flush()

        frame_time        = 1.0 / self.config.TARGET_FPS
        consecutive_fails = 0

        while self.running:
            loop_start = time.perf_counter()
            ret, frame = cap.read()
            if not ret:
                consecutive_fails += 1
                if consecutive_fails >= self.config.MAX_CONSECUTIVE_FAILURES:
                    print("\nERROR: Too many frame failures — stopping.")
                    break
                time.sleep(0.05)
                continue
            consecutive_fails = 0
            self.fps_tracker.tick()

            self.frame_counter += 1
            if self.frame_counter % max(1, self.config.FRAME_SKIP) != 0:
                continue

            frame = cv2.flip(frame, 1)
            try:
                state = self._process_frame(frame)
                self.process_fps.tick()
            except Exception as e:
                print(f"\nWARN: Frame processing error: {e}")
                continue

            with self.state_lock:
                self.current_state = state

            self._handle_alert(state)
            self._check_mood_bridge(state)    # ← send mood/alert events to dravixa.py

            if self.show_display:
                debug = self._draw_debug(frame, state, self.fps_tracker.fps)
                cv2.imshow("Dravixa DMS", debug)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    self.running = False
                    break

            if int(time.time()) % 2 == 0 and self.frame_counter % 60 == 0:
                sys.stdout.write(
                    f"\r[DMS] FPS:{self.fps_tracker.fps:>5.1f} | "
                    f"EAR:{state.ear:.3f}(thr:{self.config.EAR_THRESHOLD:.3f}) | "
                    f"PERCLOS:{state.perclos*100:.1f}% | "
                    f"Fatigue:{state.fatigue_score:.0f}/100 | "
                    f"Emotion:{state.emotion:<10} | "
                    f"{state.alert_level.name:<8}"
                )
                sys.stdout.flush()

            elapsed = time.perf_counter() - loop_start
            if elapsed < frame_time:
                time.sleep(frame_time - elapsed)

        cap.release()
        if self.show_display:
            cv2.destroyAllWindows()
        print("\n[Monitor] Camera released.")

    # ------------------------------------------
    # Public API
    # ------------------------------------------
    def start(self):
        if self.running:
            print("WARN: Already running.")
            return
        print("Starting Dravixa Driver Monitor...")
        self.running       = True
        self.session_start = time.time()

        self.emotion_thread_running = True
        threading.Thread(target=self._udp_receiver_worker, daemon=True).start()

        self.phone_result_thread_running = True
        threading.Thread(target=self._phone_result_receiver_worker, daemon=True).start()

        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.capture_thread.start()
        print("Driver Monitor running.")

    def stop(self):
        print("Stopping Driver Monitor...")
        self.running                = False
        self.emotion_thread_running = False
        self.phone_result_thread_running = False
        if self.capture_thread:
            self.capture_thread.join(timeout=3)
        try:
            self.sock.close()
            self.mood_sock.close()
            self.phone_sock.close()
        except Exception:
            pass
        print("Driver Monitor stopped.")

    def get_state(self) -> DriverState:
        with self.state_lock:
            return self.current_state

    def get_summary(self) -> dict:
        with self.state_lock:
            state = self.current_state
        return {
            "session_hours": round((time.time() - self.session_start) / 3600, 2),
            "total_yawns":   len(self.yawn_timestamps),
            "total_blinks":  len(self.blink_timestamps),
            "avg_fps":       round(self.fps_tracker.fps, 1),
            "ear_threshold": round(self.config.EAR_THRESHOLD, 3),
            "current_state": state.to_dict(),
        }


# ==========================================
# 8. INTEGRATION HELPERS
# ==========================================

def demo_callback(state: DriverState):
    emoji = {
        AlertLevel.NORMAL:   "OK",
        AlertLevel.CAUTION:  "CAUTION",
        AlertLevel.WARNING:  "WARNING",
        AlertLevel.CRITICAL: "CRITICAL",
    }
    print(f"\n[ALERT] {emoji[state.alert_level]}: {state.alert_message}")
    print(f"  Fatigue:{state.fatigue_score:.0f}/100 | "
          f"PERCLOS:{state.perclos*100:.1f}% | "
          f"Yawns:{state.yawn_count_1min} | "
          f"Emotion:{state.emotion}")


def integrate_with_dravixa(tts_queue):
    """
    Simple drop-in callback for Dravixa TTS queue.
    Speaks on WARNING+ or CAUTION with high fatigue.
    Note: mood bridge (music) is handled internally via UDP 5003.
    """
    last_spoken = [0]
    last_level  = [AlertLevel.NORMAL]

    def callback(state: DriverState):
        now = time.time()
        if now - last_spoken[0] < 20 and state.alert_level == last_level[0]:
            return
        if state.alert_level.value >= AlertLevel.WARNING.value:
            tts_queue.put(state.alert_message)
            last_spoken[0] = now
            last_level[0]  = state.alert_level
        elif state.alert_level == AlertLevel.CAUTION and state.fatigue_score >= 30:
            tts_queue.put(state.alert_message)
            last_spoken[0] = now
            last_level[0]  = state.alert_level

    return callback


# ==========================================
# 9. ENTRYPOINT
# ==========================================

if __name__ == "__main__":
    print("\n" + "="*60)
    print(" DRAVIXA DRIVER MONITOR v3.0 — Jetson Edition")
    print("="*60)
    print(" 1. Run 'python3 driveremo.py' in Terminal 1 first")
    print(" 2. This script calibrates EAR on startup automatically")
    print(" 3. Press 'q' in video window to quit / Ctrl+C in terminal")
    print("="*60 + "\n")
    sys.stdout.flush()

    config = DriverConfig(
        CAMERA_INDEX   = 0,
        CAMERA_BACKEND = cv2.CAP_V4L2,
        FRAME_WIDTH    = 640,
        FRAME_HEIGHT   = 480,
        TARGET_FPS     = 30,
    )

    monitor = DriverMonitor(
        config         = config,
        alert_callback = demo_callback,
        show_display   = True,
        # skip_calibration=True  # uncomment to skip during dev
    )

    try:
        monitor.start()
        last_summary = time.time()
        while monitor.running:
            time.sleep(1)
            if time.time() - last_summary >= 15:
                s = monitor.get_summary()
                print(f"\n[Summary] {s['session_hours']}h | "
                      f"FPS:{s['avg_fps']} | "
                      f"EAR_thr:{s['ear_threshold']} | "
                      f"Yawns:{s['total_yawns']} | "
                      f"Fatigue:{s['current_state']['fatigue_score']}/100")
                last_summary = time.time()
    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        monitor.stop()
        print("\nFinal Summary:")
        print(json.dumps(monitor.get_summary(), indent=2))
