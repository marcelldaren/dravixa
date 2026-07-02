"""
hmi_bridge.py — drop this next to dravixa_main.py
Call hmi_send() anywhere in dravixa_main.py to push live data to the HMI.

Usage in dravixa_main.py:
    from hmi_bridge import hmi_send

    # In stt_worker, after UI.you(transcription):
    hmi_send({"type":"chat", "text": transcription, "is_user": True})

    # In tts_worker, after UI.dravixa(text):
    hmi_send({"type":"chat", "text": text, "is_user": False})

    # In llm_agent_worker, when processing starts:
    hmi_send({"type":"status", "awake": True, "processing": True, "speaking": False})

    # In tts_worker, when speaking starts:
    hmi_send({"type":"status", "awake": True, "processing": False, "speaking": True})

    # After TTS finishes:
    hmi_send({"type":"status", "awake": True, "processing": False, "speaking": False})

    # In control_car_ac, after executing:
    hmi_send({"type":"vehicle", "ac_on": arduino_state['ac_on'], "temp": arduino_state['ac_temp']})

    # In spotify_play_pause, after action:
    hmi_send({"type":"spotify", "track": track_name, "artist": artist, "progress": 0.0})
"""

import socket
import json
import threading

HMI_HOST = "127.0.0.1"
HMI_PORT = 19999
_lock    = threading.Lock()

def hmi_send(pkt: dict) -> bool:
    """
    Send a JSON packet to the HMI. Non-blocking, thread-safe.
    Returns True if sent successfully, False if HMI is not running.
    """
    try:
        with _lock:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.2)
            s.connect((HMI_HOST, HMI_PORT))
            s.sendall(json.dumps(pkt).encode("utf-8"))
            s.close()
        return True
    except Exception:
        return False  # HMI not running — silent fail, dravixa still works


def hmi_send_async(pkt: dict):
    """Fire-and-forget version — won't block the calling thread at all."""
    threading.Thread(target=hmi_send, args=(pkt,), daemon=True).start()
