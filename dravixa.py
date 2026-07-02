"""
================================================================
 DRAVIXA v2.5 - Jetson Orin Nano Edition
 AI Driving Assistant for Toyota
================================================================
 Changes v2.5:
   - Full Spotify: search, genre/mood, now playing, like/save
   - Auto volume duck when Dravixa speaks
   - All nearby places via Google Maps API
   - Bot emotes (READY/CONFUSED/SHAKE/DANCE/NOD/SLEEP)
   - Arduino combined (AC relay + stepper window + servo bot)
   - DOA speaker rejection
   - Improved wake word detection
   - HMI bridge integration
================================================================
"""

# ── Silence ALL backend noise ─────────────────────────────────
import os, sys
import socket
os.environ["KMP_DUPLICATE_LIB_OK"]  = "TRUE"
os.environ["ONNX_PROVIDER"]         = "CUDAExecutionProvider"
os.environ["ORT_LOGGING_LEVEL"]     = "3"
os.environ["TF_CPP_MIN_LOG_LEVEL"]  = "3"
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"
os.environ["GLOG_minloglevel"]      = "2"
os.environ["OLLAMA_DEBUG"]          = "0"
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

import re, time, wave, json, queue, threading, requests, usb.core
import pyaudio, numpy as np, sounddevice as sd, scipy.signal
from datetime import datetime
from collections import deque
from tuning import Tuning
from faster_whisper import WhisperModel
from kokoro_onnx import Kokoro
import onnxruntime as _ort

try:
    import serial
    import serial.tools.list_ports
    _serial_ok = True
except ImportError:
    _serial_ok = False

# ─────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────
RESPEAKER_INPUT_DEVICE = 0
TTS_OUTPUT_DEVICE      = 0

GMAPS_KEY      = "AIzaSyCKh_E_tmWl_05LywLZlmx2Kjy0M_gXxi0"
OWM_API_KEY    = "af19ff126aae69998ef4b40a54c6b13d"
TOMTOM_API_KEY = "zZJXQwbJOUv5zsxeNMRXAdPY9TnrQaFD"
ORS_API_KEY    = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImE4ODY2NDk0OTI3ZjRlNmFiODFlMmY3MjdjMzUzMzgwIiwiaCI6Im11cm11cjY0In0="
DEFAULT_CITY   = "Tangerang"

MANUAL_LOCATION = {"lat": -6.1781, "lng": 106.6300, "city": "Tangerang"}
ARDUINO_PORT    = "/dev/ttyCH341USB0"

WAKE_WORDS = [
    "hello toyota", "hey toyota", "hi toyota", "toyota",
    "okay toyota", "ok toyota", "yo toyota",
    "dravixa", "hey dravixa", "hi dravixa", "hello dravixa",
    "dravix", "travix", "travis", "gravix", "dravika", "drafika",
    "halo toyota", "hai toyota", "hei toyota",
    "halo dravixa", "hai dravixa",
    "oyota", "avixa", "atixa", "ravix",
]

SLEEP_TIMEOUT          = 60
SILENCE_DURATION       = 0.8
MAX_RECORDING_DURATION = 15
MIN_VOICE_CHUNKS       = 5
MIN_WAKE_CHUNKS        = 2
BARGE_IN_GRACE_PERIOD  = 1.5
BARGE_IN_MIN_CHUNKS    = 15
DOA_ACCEPT_MIN         = 0
DOA_ACCEPT_MAX         = 140

# ─────────────────────────────────────────────────────────────
# UI LOGGER
# ─────────────────────────────────────────────────────────────
class UI:
    RESET  = "\033[0m"; BOLD  = "\033[1m"; GREEN = "\033[92m"
    YELLOW = "\033[93m"; CYAN  = "\033[96m"; RED  = "\033[91m"
    DIM    = "\033[2m";  BLUE  = "\033[94m"

    @staticmethod
    def banner(location, device_idx):
        print("\n" + "="*60)
        print(f"  DRAVIXA v2.5  |  Jetson Orin Nano  |  {location}")
        print(f"  Wake: 'Hello Toyota'  or  'Dravixa'")
        print(f"  Mic [{device_idx}]  |  Speaker [{TTS_OUTPUT_DEVICE}]")
        print("="*60 + "\n")

    @staticmethod
    def status(msg):
        print(f"{UI.DIM}[{datetime.now().strftime('%H:%M:%S')}] {msg}{UI.RESET}")

    @staticmethod
    def wake():
        print(f"\n{UI.GREEN}{UI.BOLD}  AWAKE - listening...{UI.RESET}")

    @staticmethod
    def sleep():
        print(f"{UI.DIM}  Sleeping. Say 'Hello Toyota' to wake.{UI.RESET}\n")

    @staticmethod
    def recording():
        print(f"{UI.CYAN}  Recording...{UI.RESET}", end="", flush=True)

    @staticmethod
    def you(text):
        print(f"\r{UI.YELLOW}  YOU     >> {text}{UI.RESET}")

    @staticmethod
    def dravixa(text):
        print(f"{UI.GREEN}  DRAVIXA >> {text}{UI.RESET}")

    @staticmethod
    def latency(stt, llm, tts):
        total = stt + llm + tts
        print(f"{UI.DIM}  Latency: STT {stt:.0f}ms | LLM {llm:.0f}ms | TTS {tts:.0f}ms | Total {total:.0f}ms{UI.RESET}")

    @staticmethod
    def tool(name, result):
        print(f"{UI.BLUE}  [{name}] {str(result)[:80]}{UI.RESET}")

    @staticmethod
    def error(msg):
        print(f"{UI.RED}  ERROR: {msg}{UI.RESET}")

    @staticmethod
    def info(msg):
        print(f"{UI.DIM}  {msg}{UI.RESET}")

# ─────────────────────────────────────────────────────────────
# STATE & QUEUES
# ─────────────────────────────────────────────────────────────
audio_processing_queue = queue.Queue()
agent_task_queue       = queue.Queue()
tts_queue              = queue.Queue()
latency = {"stt": 0.0, "llm": 0.0, "tts": 0.0}

state = {
    "is_speaking":       False,
    "is_awake":          False,
    "is_processing":     False,
    "interrupt_flag":    False,
    "last_interaction":  time.time(),
    "session_start":     time.time(),
    "last_fatigue_warn": 0,
    "driver_location":   {"lat": -6.2088, "lng": 106.8456, "city": "Jakarta"},
    "current_dest":      None,
}

chat_history = []
MAX_HISTORY  = 4

# ─────────────────────────────────────────────────────────────
# HMI BRIDGE
# ─────────────────────────────────────────────────────────────
def hmi_send(pkt: dict):
    try:
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.2)
        s.connect(("127.0.0.1", 19999))
        s.sendall(json.dumps(pkt).encode())
        s.close()
    except Exception:
        pass

def hmi(pkt: dict):
    threading.Thread(target=hmi_send, args=(pkt,), daemon=True).start()

# ─────────────────────────────────────────────────────────────
# MODEL LOADING
# ─────────────────────────────────────────────────────────────
UI.info("Loading Whisper STT...")
try:
    stt_model = WhisperModel("base.en", device="cuda", compute_type="float16")
    UI.info("STT: GPU CUDA float16")
except Exception as e:
    stt_model = WhisperModel("base.en", device="cpu", compute_type="int8")
    UI.info(f"STT: CPU int8 (GPU failed: {e})")

UI.info("Loading Kokoro TTS with CUDA...")
try:
    _tts_sess = _ort.InferenceSession(
        "kokoro-v1.0.onnx",
        providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    tts_model = Kokoro.from_session(_tts_sess, "voices-v1.0.bin")
    UI.info("TTS: GPU CUDAExecutionProvider")
except Exception as e:
    UI.error(f"TTS load failed: {e}"); sys.exit(1)

# ─────────────────────────────────────────────────────────────
# SPOTIFY
# ─────────────────────────────────────────────────────────────
_sp = None
_spotify_vol_before_duck = 60
_spotify_ducked          = False

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth as _SpotifyOAuth
    _sp = spotipy.Spotify(auth_manager=_SpotifyOAuth(
    	client_id="1ef7ec97d28e48efacd3771cb3a0fea2",
    	client_secret="15736ab0b7974500a72779f632647b6a",
    	redirect_uri="http://127.0.0.1:8888/callback",
    	scope=(
    	    "user-read-playback-state user-modify-playback-state "
    	    "playlist-read-private user-read-currently-playing "
    	    "user-library-modify user-library-read"
    	),
    	open_browser=False,
    	cache_path="/home/dravixa/dravixa/.cache",
    ))
    UI.info("Spotify: connected (token cached)")
except Exception as e:
    _sp = None
    UI.info(f"Spotify: unavailable ({e})")
def _get_sp():
    """Always get fresh Spotify client."""
    global _sp
    if not _sp:
        return None
    try:
        # Force token refresh if needed
        auth = _sp._auth_manager
        if auth:
            token = auth.get_cached_token()
            if token and auth.is_token_expired(token):
                token = auth.refresh_access_token(token["refresh_token"])
                _sp = spotipy.Spotify(auth=token["access_token"])
        return _sp
    except Exception:
        return _sp

# ── Basic controls ────────────────────────────────────────────
def spotify_play_pause(action="toggle"):
    if not _sp: return "Spotify not connected."
    _sp_refresh()  # ← add this
    try:
        pb = _sp.current_playback()
        if action == "pause" or (action == "toggle" and pb and pb["is_playing"]):
            _sp.pause_playback(); return "Music paused."
        _sp.start_playback(); return "Music playing."
    except Exception as e: return f"Spotify error: {e}"
def spotify_next_track():
    if not _sp: return "Spotify not connected."
    _sp_refresh()  # ← add this
    try:
        _sp.next_track()
        time.sleep(0.8)
        pb = _sp.current_playback()
        if pb and pb.get("item"):
            name    = pb["item"]["name"]
            artists = ", ".join(a["name"] for a in pb["item"]["artists"])
            return f"Playing {name} by {artists}."
        return "Skipped to next track."
    except Exception as e: return f"Spotify error: {e}"

def spotify_previous_track():
    if not _sp: return "Spotify not connected."
    try:
        _sp.previous_track()
        time.sleep(0.5)
        _sp.previous_track()  # call twice to skip to actual previous
        time.sleep(0.8)
        pb = _sp.current_playback()
        if pb and pb.get("item"):
            name    = pb["item"]["name"]
            artists = ", ".join(a["name"] for a in pb["item"]["artists"])
            return f"Playing {name} by {artists}."
        return "Previous track."
    except Exception as e: return f"Spotify error: {e}"

def spotify_play_playlist(name):
    if not _sp: return "Spotify not connected."
    try:
        for pl in _sp.current_user_playlists(limit=50)["items"]:
            if name.lower() in pl["name"].lower():
                _sp.start_playback(context_uri=pl["uri"])
                return f"Playing playlist: {pl['name']}."
        return f"No playlist matching '{name}'."
    except Exception as e: return f"Spotify error: {e}"

def spotify_set_volume(vol):
    if not _sp: return "Spotify not connected."
    try:
        _sp.volume(max(0, min(100, int(vol))))
        return f"Volume set to {vol}%."
    except Exception as e: return f"Spotify error: {e}"

# ── Search and play ───────────────────────────────────────────
def spotify_search_play(query, search_type="track"):
    sp = _get_sp()
    if not _sp: return "Spotify not connected."
    _sp_refresh()  # ← add this
    try:
        results = _sp.search(q=query, type=search_type, limit=1)
        items   = results.get(f"{search_type}s", {}).get("items", [])
        if not items: return f"Can't find '{query}' on Spotify."
        item = items[0]
        if search_type == "track":
            _sp.start_playback(uris=[item["uri"]])
            artists = ", ".join(a["name"] for a in item["artists"])
            hmi({"type": "spotify", "track": item["name"], "artist": artists})
            return f"Playing {item['name']} by {artists}."
        elif search_type == "artist":
            top  = _sp.artist_top_tracks(item["id"])["tracks"]
            if not top: return f"No tracks for {item['name']}."
            _sp.start_playback(uris=[t["uri"] for t in top[:10]])
            hmi({"type": "spotify", "track": top[0]["name"], "artist": item["name"]})
            return f"Playing top tracks by {item['name']}."
        elif search_type == "album":
            _sp.start_playback(context_uri=item["uri"])
            artists = ", ".join(a["name"] for a in item["artists"])
            hmi({"type": "spotify", "track": item["name"], "artist": artists})
            return f"Playing album {item['name']} by {artists}."
        elif search_type == "playlist":
            _sp.start_playback(context_uri=item["uri"])
            hmi({"type": "spotify", "track": item["name"], "artist": "Playlist"})
            return f"Playing playlist {item['name']}."
    except Exception as e: return f"Spotify search error: {e}"

# ── Genre / mood ──────────────────────────────────────────────
def spotify_play_genre(genre):
    if not _sp: return "Spotify not connected."
    genre_seeds = {
        "relaxing":   ["chill", "ambient"],
        "chill":      ["chill", "acoustic"],
        "energetic":  ["workout", "dance"],
        "workout":    ["workout", "electronic"],
        "driving":    ["road-trip", "pop"],
        "focus":      ["study", "classical"],
        "happy":      ["happy", "pop"],
        "sad":        ["sad", "acoustic"],
        "romantic":   ["romance", "r-n-b"],
        "jazz":       ["jazz"],
        "rock":       ["rock"],
        "pop":        ["pop"],
        "classical":  ["classical"],
        "indie":      ["indie"],
        "hip hop":    ["hip-hop"],
        "hiphop":     ["hip-hop"],
        "electronic": ["electronic", "dance"],
        "sleep":      ["sleep", "ambient"],
    }
    seeds = None
    for key, val in genre_seeds.items():
        if key in genre.lower():
            seeds = val; break
    if not seeds:
        return spotify_search_play(genre, "playlist")
    try:
        recs   = _sp.recommendations(seed_genres=seeds[:2], limit=20)
        tracks = recs.get("tracks", [])
        if not tracks: return spotify_search_play(genre, "playlist")
        _sp.start_playback(uris=[t["uri"] for t in tracks])
        first   = tracks[0]
        artists = ", ".join(a["name"] for a in first["artists"])
        hmi({"type": "spotify", "track": first["name"], "artist": artists})
        return f"Playing {genre} music. Starting with {first['name']}."
    except Exception as e: return f"Spotify genre error: {e}"

# ── Now playing ───────────────────────────────────────────────
def spotify_now_playing():
    if not _sp: return "Spotify not connected."
    try:
        pb = _sp.current_playback()
        if not pb or not pb.get("item"):
            return "Nothing playing on Spotify."
        item    = pb["item"]
        artists = ", ".join(a["name"] for a in item["artists"])
        pos_s   = (pb.get("progress_ms", 0) or 0) // 1000
        dur_s   = item["duration_ms"] // 1000
        hmi({
            "type":     "spotify",
            "track":    item["name"],
            "artist":   artists,
            "progress": (pb.get("progress_ms", 0) or 0) / item["duration_ms"],
        })
        return (f"Now playing: {item['name']} by {artists}. "
                f"{pos_s // 60}:{pos_s % 60:02d} of "
                f"{dur_s // 60}:{dur_s % 60:02d}.")
    except Exception as e: return f"Spotify error: {e}"

def spotify_who_sings():
    if not _sp: return "Spotify not connected."
    try:
        pb = _sp.current_playback()
        if not pb or not pb.get("item"): return "Nothing playing right now."
        item    = pb["item"]
        artists = ", ".join(a["name"] for a in item["artists"])
        return f"This is {item['name']} by {artists}."
    except Exception as e: return f"Spotify error: {e}"

# ── Like / save ───────────────────────────────────────────────
def spotify_like_song():
    if not _sp: return "Spotify not connected."
    try:
        pb = _sp.current_playback()
        if not pb or not pb.get("item"): return "Nothing playing to save."
        track_id = pb["item"]["id"]
        name     = pb["item"]["name"]
        saved    = _sp.current_user_saved_tracks_contains([track_id])
        if saved and saved[0]: return f"{name} is already in your Liked Songs."
        _sp.current_user_saved_tracks_add([track_id])
        return f"Saved {name} to your Liked Songs."
    except Exception as e: return f"Spotify error: {e}"

def spotify_unlike_song():
    if not _sp: return "Spotify not connected."
    try:
        pb = _sp.current_playback()
        if not pb or not pb.get("item"): return "Nothing playing."
        track_id = pb["item"]["id"]
        name     = pb["item"]["name"]
        _sp.current_user_saved_tracks_delete([track_id])
        return f"Removed {name} from Liked Songs."
    except Exception as e: return f"Spotify error: {e}"

# ── Volume duck ───────────────────────────────────────────────
def spotify_duck():
    global _spotify_vol_before_duck, _spotify_ducked
    if not _sp or _spotify_ducked: return
    try:
        pb = _sp.current_playback()
        if pb and pb.get("is_playing"):
            dev = pb.get("device", {})
            # Only duck if device supports volume control
            if not dev.get("volume_percent") is None and dev.get("type") != "Smartphone":
                _spotify_vol_before_duck = dev.get("volume_percent", 60)
                _sp.volume(max(10, _spotify_vol_before_duck - 40))
                _spotify_ducked = True
    except Exception:
        pass  # Silent fail — volume control not supported on this device

def spotify_unduck():
    global _spotify_ducked
    if not _sp or not _spotify_ducked: return
    try:
        _sp.volume(_spotify_vol_before_duck)
        _spotify_ducked = False
    except Exception:
        _spotify_ducked = False  # Reset flag even if volume call fails
def _sp_refresh():
    """Refresh Spotify token if expired."""
    global _sp
    if not _sp: return False
    try:
        # This forces a token refresh if needed
        _sp.current_user()
        return True
    except Exception:
        try:
            import spotipy
            from spotipy.oauth2 import SpotifyOAuth as _SpotifyOAuth
            _sp = spotipy.Spotify(auth_manager=_SpotifyOAuth(
                client_id="1ef7ec97d28e48efacd3771cb3a0fea2",
                client_secret="15736ab0b7974500a72779f632647b6a",
                redirect_uri="http://127.0.0.1:8888/callback",
                scope=(
                    "user-read-playback-state user-modify-playback-state "
                    "playlist-read-private user-read-currently-playing "
                    "user-library-modify user-library-read"
                ),
                open_browser=False,
                cache_path="/home/dravixa/dravixa/.cache",
            ))
            UI.info("Spotify: token refreshed")
            return True
        except Exception as e:
            UI.error(f"Spotify refresh failed: {e}")
            return False
     
def spotify_transfer_to_jetson():
    """Auto transfer playback to Jetson/spotifyd on startup."""
    if not _sp: return
    try:
        time.sleep(5)  # wait for spotifyd to register
        devices = _sp.devices()
        for dev in devices.get("devices", []):
            if "dravixa" in dev["name"].lower():
                _sp.transfer_playback(dev["id"], force_play=False)
                UI.info(f"Spotify transferred to: {dev['name']}")
                return
        UI.info("Dravixa Spotify device not found — is spotifyd running?")
    except Exception as e:
        UI.info(f"Spotify transfer: {e}")
def spotify_token_keeper():
    """Keep Spotify token alive — refresh every 30 min."""
    while True:
        time.sleep(1800)
        global _sp
        if _sp:
            try:
                _sp.current_user()
            except Exception:
                try:
                    import spotipy
                    from spotipy.oauth2 import SpotifyOAuth as _SpotifyOAuth
                    _sp = spotipy.Spotify(auth_manager=_SpotifyOAuth(
                        client_id="1ef7ec97d28e48efacd3771cb3a0fea2",
                        client_secret="15736ab0b7974500a72779f632647b6a",
                        redirect_uri="http://127.0.0.1:8888/callback",
                        scope=(
                            "user-read-playback-state user-modify-playback-state "
                            "playlist-read-private user-read-currently-playing "
                            "user-library-modify user-library-read"
                        ),
                        open_browser=False,
                        cache_path="/home/dravixa/dravixa/.cache",
                    ))
                    UI.info("Spotify: token refreshed")
                except Exception as e:
                    UI.error(f"Spotify refresh failed: {e}")

# ─────────────────────────────────────────────────────────────
# TOOLS — Location & Navigation
# ─────────────────────────────────────────────────────────────
def get_current_location():
    if MANUAL_LOCATION: return MANUAL_LOCATION
    try:
        r = requests.get("http://ip-api.com/json/", timeout=5).json()
        if r.get("status") == "success":
            return {"lat": r["lat"], "lng": r["lon"], "city": r.get("city", DEFAULT_CITY)}
    except Exception: pass
    return state["driver_location"]

def get_weather(city):
    try:
        r = requests.get("https://api.openweathermap.org/data/2.5/weather",
            params={"q": city, "appid": OWM_API_KEY, "units": "metric", "lang": "en"}, timeout=5)
        if r.status_code != 200: return f"Can't get weather for {city}."
        d = r.json()
        return (f"{d['name']}: {round(d['main']['temp'])}°C, "
                f"{d['weather'][0]['description']}, "
                f"humidity {d['main']['humidity']}%, wind {round(d['wind']['speed'])} m/s")
    except Exception: return "Weather service unavailable."

def get_traffic(origin=None, destination=None):
    lat, lng = state["driver_location"]["lat"], state["driver_location"]["lng"]
    try:
        r = requests.get("https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json",
            params={"key": TOMTOM_API_KEY, "point": f"{lat},{lng}"}, timeout=5)
        if r.status_code != 200: raise Exception()
        d     = r.json()["flowSegmentData"]
        ratio = d["currentSpeed"] / d["freeFlowSpeed"] if d["freeFlowSpeed"] > 0 else 1.0
        if ratio < 0.4: return f"Heavy traffic. Moving at {d['currentSpeed']} km/h."
        if ratio < 0.7: return f"Moderate traffic. Speed {d['currentSpeed']} km/h."
        return f"Traffic flowing well at {d['currentSpeed']} km/h."
    except Exception:
        hour = datetime.now().hour
        if 7 <= hour <= 10:  return "Morning rush hour. Heavy traffic on Sudirman, Gatot Subroto."
        if 16 <= hour <= 20: return "Evening rush hour. Macet on main roads."
        return "Traffic is relatively light."

def get_directions(destination):
    lat, lng = state["driver_location"]["lat"], state["driver_location"]["lng"]
    try:
        geo = requests.get("https://nominatim.openstreetmap.org/search",
            params={"q": f"{destination}, Jakarta, Indonesia", "format": "json", "limit": 1},
            headers={"User-Agent": "Dravixa/2.5"}, timeout=5).json()
        if not geo: return f"Can't find {destination}."
        dlat, dlng = float(geo[0]["lat"]), float(geo[0]["lon"])
        route = requests.get("https://api.openrouteservice.org/v2/directions/driving-car",
            params={"start": f"{lng},{lat}", "end": f"{dlng},{dlat}"},
            headers={"Authorization": ORS_API_KEY}, timeout=5).json()
        s       = route["features"][0]["properties"]["summary"]
        dist_km = round(s["distance"] / 1000, 1)
        dur_min = round(s["duration"] / 60)
        state["current_dest"] = destination
        hmi({
            "type": "navigate", "destination": destination,
            "dest_lat": dlat, "dest_lng": dlng,
            "origin_lat": lat, "origin_lng": lng,
            "distance_km": dist_km, "duration_min": dur_min,
        })
        return f"{destination} is {dist_km} km, about {dur_min} min."
    except Exception:
        state["current_dest"] = destination
        hmi({"type": "navigate", "destination": destination,
             "origin_lat": lat, "origin_lng": lng,
             "distance_km": "?", "duration_min": "?"})
        return f"Navigation to {destination} set. Use Waze for turn-by-turn."

def get_flood_alert():
    try:
        r = requests.get("https://peringatandini.bmkg.go.id/api/notification", timeout=5)
        if r.status_code == 200 and r.json():
            return "Flood warning active. Avoid Grogol, Kampung Melayu, Cipinang."
        w = get_weather(state["driver_location"].get("city", DEFAULT_CITY))
        if "rain" in w.lower() or "thunderstorm" in w.lower():
            return "Heavy rain detected. Flood risk in low areas. Drive carefully."
        return "No active flood alerts."
    except Exception: return "Flood service unavailable."

# ─────────────────────────────────────────────────────────────
# TOOLS — Nearby Places (Google Maps REST API)
# ─────────────────────────────────────────────────────────────
def _search_nearby_google(place_type, lat, lng, radius=2000, max_results=8):
    try:
        r = requests.get(
            "https://maps.googleapis.com/maps/api/place/nearbysearch/json",
            params={"location": f"{lat},{lng}", "radius": radius,
                    "type": place_type, "key": GMAPS_KEY},
            timeout=8)
        data   = r.json()
        status = data.get("status", "")
        if status == "OK":
            places = []
            for p in data.get("results", [])[:max_results]:
                loc = p["geometry"]["location"]
                places.append({
                    "name":     p.get("name", "Unknown"),
                    "lat":      loc["lat"],
                    "lng":      loc["lng"],
                    "vicinity": p.get("vicinity", ""),
                    "rating":   p.get("rating", 0),
                })
            return places, None
        elif status == "ZERO_RESULTS":
            return [], "No results found."
        elif status == "REQUEST_DENIED":
            return [], f"API denied: {data.get('error_message','Check billing')}"
        else:
            return [], f"Places API: {status}"
    except Exception as e:
        return [], str(e)

def get_fuel_stations(location=None):
    lat, lng = state["driver_location"]["lat"], state["driver_location"]["lng"]
    places, error = _search_nearby_google("gas_station", lat, lng)
    hmi({"type": "navigate", "places": "gas_station",
         "origin_lat": lat, "origin_lng": lng,
         "markers": places if not error else []})
    if error or not places: return "Showing fuel stations on your map."
    names = [p["name"] for p in places[:2]]
    return f"Found {len(places)} stations. Nearest: {', '.join(names)}."

def find_nearby(place_type, label):
    lat, lng = state["driver_location"]["lat"], state["driver_location"]["lng"]
    places, error = _search_nearby_google(place_type, lat, lng)
    if error:
        UI.info(f"Places API error: {error}")
        hmi({"type": "navigate", "places": place_type,
             "origin_lat": lat, "origin_lng": lng, "markers": []})
        return f"Showing nearby {label} on your map."
    if not places:
        hmi({"type": "navigate", "places": place_type,
             "origin_lat": lat, "origin_lng": lng, "markers": []})
        return f"No {label} found within 2km."
    hmi({"type": "navigate", "places": place_type,
         "origin_lat": lat, "origin_lng": lng, "markers": places})
    names = [p["name"] for p in places[:2]]
    return f"Found {len(places)} {label} nearby. Nearest: {', '.join(names)}."

def navigate_to_nearest(place_type, label):
    lat, lng = state["driver_location"]["lat"], state["driver_location"]["lng"]
    places, error = _search_nearby_google(place_type, lat, lng, radius=3000, max_results=1)
    if error or not places:
        UI.info(f"navigate_to_nearest fallback: {error}")
        return get_directions(label)
    nearest = places[0]
    dlat, dlng, name = nearest["lat"], nearest["lng"], nearest["name"]
    try:
        route   = requests.get(
            "https://api.openrouteservice.org/v2/directions/driving-car",
            params={"start": f"{lng},{lat}", "end": f"{dlng},{dlat}"},
            headers={"Authorization": ORS_API_KEY}, timeout=5).json()
        s       = route["features"][0]["properties"]["summary"]
        dist_km = round(s["distance"] / 1000, 1)
        dur_min = round(s["duration"] / 60)
    except Exception:
        import math
        dist_km = round(math.sqrt((dlat-lat)**2 + (dlng-lng)**2) * 111, 1)
        dur_min = round(dist_km / 0.4)
    state["current_dest"] = name
    hmi({"type": "navigate", "destination": name,
         "dest_lat": dlat, "dest_lng": dlng,
         "origin_lat": lat, "origin_lng": lng,
         "distance_km": dist_km, "duration_min": dur_min,
         "markers": places})
    return f"{name} is {dist_km} km, about {dur_min} min."

def emergency_sos(type="general"):
    return {
        "police":    "Calling Police: 110",
        "ambulance": "Calling Ambulance: 119",
        "fire":      "Calling Fire Dept: 113",
        "general":   "Calling Emergency: 112",
    }.get(type, "Calling Emergency: 112")

def get_driver_status():
    h = (time.time() - state["session_start"]) / 3600
    if h >= 2:   return f"You've been driving {h:.1f} hours. Time for a break!"
    if h >= 1.5: return f"Driving {h:.1f} hours. Consider a short break soon."
    return f"Driving {h:.1f} hours. You're good to keep going."

# ─────────────────────────────────────────────────────────────
# WHATSAPP
# ─────────────────────────────────────────────────────────────
whatsapp_state = {"driver": None, "enabled": False, "seen_ids": set(), "pending": []}

def init_whatsapp():
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service
        opts = Options()
        opts.binary_location = "/usr/bin/chromium-browser"
        opts.add_argument(f"--user-data-dir={os.path.abspath('whatsapp_profile')}")
        opts.add_argument("--profile-directory=Default")
        for arg in ["--no-sandbox","--disable-dev-shm-usage","--disable-gpu",
                    "--disable-extensions","--no-first-run","--remote-debugging-port=9222",
                    "--window-size=1280,800"]:
            opts.add_argument(arg)
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)
        drv = webdriver.Chrome(service=Service("/usr/bin/chromedriver"), options=opts)
        drv.get("https://web.whatsapp.com")
        whatsapp_state["driver"]  = drv
        whatsapp_state["enabled"] = True
        UI.info("WhatsApp: browser opened, scan QR if prompted")
        return True
    except Exception as e:
        UI.info(f"WhatsApp: unavailable ({e})")
        return False

def read_whatsapp_messages():
    if not whatsapp_state["enabled"] or not whatsapp_state["driver"]:
        return "WhatsApp not connected."
    if whatsapp_state["pending"]:
        s, t = whatsapp_state["pending"].pop(0)
        return f"Message from {s}: {t}"
    try:
        from selenium.webdriver.common.by import By
        drv    = whatsapp_state["driver"]
        badges = drv.find_elements(By.CSS_SELECTOR,
            "span[data-testid='icon-unread-count'], span[aria-label*='unread']")
        if not badges: return "No new WhatsApp messages."
        row = badges[0]
        for _ in range(5):
            try: row = row.find_element(By.XPATH, "..")
            except Exception: break
        row.click(); time.sleep(1.5)
        try:
            el     = drv.find_element(By.CSS_SELECTOR, "header span[dir='auto'][title]")
            sender = el.text or el.get_attribute("title") or "Someone"
        except Exception: sender = "Someone"
        try:
            msgs = drv.find_elements(By.CSS_SELECTOR, "div.message-in span.selectable-text")
            if msgs:
                txt = msgs[-1].text.strip()
                key = f"{sender}:{txt}"
                if key not in whatsapp_state["seen_ids"]:
                    whatsapp_state["seen_ids"].add(key)
                    return f"Message from {sender}: {txt[:120]}"
                return f"No new messages. Last from {sender}: {txt[:80]}"
        except Exception: pass
        return f"Opened chat with {sender} but couldn't read it."
    except Exception: return "Can't read WhatsApp right now."

def whatsapp_monitor_worker():
    time.sleep(20)
    if not whatsapp_state["enabled"] or not whatsapp_state["driver"]: return
    seen = set()
    session_dt = datetime.fromtimestamp(state["session_start"])

    while True:
        if not whatsapp_state["driver"] or not whatsapp_state["enabled"]:
            time.sleep(10)
            continue
        try:
            drv = whatsapp_state["driver"]
            raw = drv.execute_script("""
                try {
                    var els = document.querySelectorAll('[data-pre-plain-text]');
                    var results = [];
                    for (var i = 0; i < els.length; i++) {
                        var pre = els[i].getAttribute('data-pre-plain-text') || '';
                        var txt = (els[i].innerText || '').trim().split('\\n').pop();
                        if (txt && txt.length > 0 && txt.length < 500)
                            results.push(pre + '|||' + txt);
                    }
                    return results.join('~~~');
                } catch(e) { return ''; }
            """) or ""

            if raw and "|||" in raw:
                for entry in raw.split("~~~"):
                    if "|||" not in entry: continue
                    pre, txt = entry.split("|||", 1)
                    txt = txt.strip()
                    key = pre + txt
                    if not pre or not txt or key in seen:
                        seen.add(key); continue
                    seen.add(key)
                    import re as _re
                    m = _re.search(r'\] (.+?): ?$', pre)
                    sender = m.group(1) if m else "Someone"
                    msg = f"New WhatsApp from {sender}: {txt[:120]}"
                    tts_queue.put(msg)
                    hmi({"type": "chat", "text": msg, "is_user": False})
        except Exception:
            pass
        time.sleep(3)

# ─────────────────────────────────────────────────────────────
# ARDUINO
# ─────────────────────────────────────────────────────────────
arduino_state = {
    "ser": None, "connected": False,
    "ac_on": False, "ac_temp": 24, "window_angle": 0,
}

def init_arduino(port=None, baud=115200):
    if not _serial_ok: return False
    try:
        if port is None:
            for p in serial.tools.list_ports.comports():
                if any(k in (p.description or "").lower()
                       for k in ("arduino","ch340","cp210","uart","usb serial")):
                    port = p.device; break
            if port is None:
                ports = serial.tools.list_ports.comports()
                if ports: port = ports[0].device
        if port is None: UI.info("Arduino: no port found"); return False
        ser = serial.Serial(port, baud, timeout=3, rtscts=False, dsrdtr=False)
        time.sleep(2)
        startup = ser.readline().decode(errors="ignore").strip()
        ser.flushInput()
        arduino_state["ser"]       = ser
        arduino_state["connected"] = True
        UI.info(f"Arduino: {port} ({startup})")
        return True
    except Exception as e:
        UI.info(f"Arduino: failed ({e})"); return False

def _arduino_send(cmd: str) -> str:
    UI.info(f"Arduino << {cmd}")
    if not arduino_state["connected"]: return "DISCONNECTED"
    try:
        arduino_state["ser"].write(f"{cmd}\n".encode())
        arduino_state["ser"].flush()
        time.sleep(0.1)
        resp = arduino_state["ser"].readline().decode(errors="ignore").strip()
        UI.info(f"Arduino >> {resp}")
        return resp
    except Exception as e:
        arduino_state["connected"] = False
        return f"ERROR:{e}"

def control_car_ac(action, temperature=None):
    if action == "status":
        return f"AC {'on' if arduino_state['ac_on'] else 'off'}, {arduino_state['ac_temp']}°C."
    if not arduino_state["connected"]:
        ac_state = "on" if action == "on" else "off"
        temp_str = f", {arduino_state['ac_temp']}°C" if action == "on" else ""
        return f"AC {ac_state}{temp_str}. (Arduino not connected)"
    if action == "on":
        _arduino_send("ON")
        arduino_state["ac_on"] = True
        if temperature:
            arduino_state["ac_temp"] = max(16, min(30, int(temperature)))
        hmi({"type": "vehicle", "ac_on": True, "temp": arduino_state["ac_temp"]})
        return f"AC on, {arduino_state['ac_temp']}°C."
    elif action == "off":
        _arduino_send("OFF")
        arduino_state["ac_on"] = False
        hmi({"type": "vehicle", "ac_on": False, "temp": arduino_state["ac_temp"]})
        return "AC turned off."
    return "AC command unclear."

def control_car_window(position):
    if not arduino_state["connected"]:
        return f"Window {position}. (Arduino not connected)"
    pos = position.lower().strip()
    if any(k in pos for k in ["open", "buka", "down", "turun"]):
        _arduino_send("F")
        arduino_state["window_angle"] = 180
        return "Window opening."
    elif any(k in pos for k in ["half", "halfway", "setengah"]):
        _arduino_send("H")
        arduino_state["window_angle"] = 90
        return "Window halfway."
    elif any(k in pos for k in ["close", "shut", "up", "tutup", "naik"]):
        _arduino_send("B")
        arduino_state["window_angle"] = 0
        return "Window closing."
    else:
        return "Say open, close, or half."

def control_bot_emote(emote):
    emote = emote.lower().strip()
    cmd_map = {
        "ready":    ("READY",    "Standing by."),
        "confused": ("CONFUSED", "Hmm, let me think..."),
        "shake":    ("SHAKE",    "Shaking head."),
        "nod":      ("NOD",      "Got it!"),
        "dance":    ("DANCE",    "Let's go!"),
        "sleep":    ("SLEEP",    "Going to sleep..."),
    }
    if emote not in cmd_map: return "Unknown emote."
    cmd, response = cmd_map[emote]
    if not arduino_state["connected"]: return f"{response} (Arduino not connected)"
    _arduino_send(cmd)
    return response

# ─────────────────────────────────────────────────────────────
# TOOL SCHEMAS
# ─────────────────────────────────────────────────────────────
tools_schema = [
    # Bot emote
    {"type":"function","function":{
        "name":"control_bot_emote",
        "description":"Control robot emotion/movement. Call when user says: dance, shake, confused, nod, sleep, standby, robot move.",
        "parameters":{"type":"object","properties":{
            "emote":{"type":"string","enum":["ready","confused","shake","dance","nod","sleep"]}
        },"required":["emote"]}}},

    # Navigation
    {"type":"function","function":{
        "name":"get_directions",
        "description":"Navigate to a specific named destination. Call when user says: take me to, drive to, navigate to, go to, pergi ke.",
        "parameters":{"type":"object","properties":{"destination":{"type":"string"}},"required":["destination"]}}},
    {"type":"function","function":{
        "name":"get_traffic",
        "description":"Get traffic status. Call when user asks about traffic, macet, congestion.",
        "parameters":{"type":"object","properties":{"origin":{"type":"string"},"destination":{"type":"string"}}}}},
    {"type":"function","function":{
        "name":"get_flood_alert",
        "description":"Check flood warnings. Call when user mentions banjir, flood, heavy rain.",
        "parameters":{"type":"object","properties":{}}}},

    # Navigate to nearest
    {"type":"function","function":{
        "name":"navigate_to_nearest_grocery",
        "description":"Navigate to nearest grocery. Call when user says: take me to nearest grocery, indomaret, alfamart.",
        "parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{
        "name":"navigate_to_nearest_restaurant",
        "description":"Navigate to nearest restaurant. Call when user says: take me to nearest restaurant, warung, eat.",
        "parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{
        "name":"navigate_to_nearest_fuel",
        "description":"Navigate to nearest fuel station. Call when user says: take me to nearest gas, SPBU, Pertamina.",
        "parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{
        "name":"navigate_to_nearest_atm",
        "description":"Navigate to nearest ATM. Call when user says: take me to nearest ATM.",
        "parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{
        "name":"navigate_to_nearest_pharmacy",
        "description":"Navigate to nearest pharmacy. Call when user says: take me to nearest pharmacy, apotek.",
        "parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{
        "name":"navigate_to_nearest_parking",
        "description":"Navigate to nearest parking. Call when user says: take me to nearest parking, parkir.",
        "parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{
        "name":"navigate_to_nearest_fastfood",
        "description":"Navigate to nearest fast food. Call when user says: nearest McDonald's, KFC, burger, pizza, fast food.",
        "parameters":{"type":"object","properties":{"name":{"type":"string"}}}}},

    # Show nearby
    {"type":"function","function":{
        "name":"get_fuel_stations",
        "description":"Show fuel stations on map. Call when user says: where is gas station, find gas, bensin dimana, SPBU.",
        "parameters":{"type":"object","properties":{"location":{"type":"string"}}}}},
    {"type":"function","function":{
        "name":"find_nearby_grocery",
        "description":"Show grocery stores on map. Call when user says: where is grocery, indomaret dimana.",
        "parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{
        "name":"find_nearby_restaurant",
        "description":"Show restaurants on map. Call when user says: show restaurants, where to eat, restoran mana.",
        "parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{
        "name":"find_nearby_atm",
        "description":"Show ATMs on map. Call when user says: show ATMs, ATM dimana.",
        "parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{
        "name":"find_nearby_pharmacy",
        "description":"Show pharmacies on map. Call when user says: show pharmacy, apotek dimana.",
        "parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{
        "name":"find_nearby_parking",
        "description":"Show parking on map. Call when user says: show parking, parkir dimana.",
        "parameters":{"type":"object","properties":{}}}},

    # Weather & driver
    {"type":"function","function":{
        "name":"get_weather",
        "description":"Get weather. Call when user asks about weather, rain, temperature, cuaca, hujan.",
        "parameters":{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}}},
    {"type":"function","function":{
        "name":"get_driver_status",
        "description":"Check drive time. Call when user says tired, lelah, break, rest, how long driving.",
        "parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{
        "name":"emergency_sos",
        "description":"EMERGENCY ONLY. Call for: emergency, SOS, call police, accident, help me, ambulance.",
        "parameters":{"type":"object","properties":{"type":{"type":"string","enum":["police","ambulance","fire","general"]}},"required":["type"]}}},

    # WhatsApp
    {"type":"function","function":{
        "name":"read_whatsapp_messages",
        "description":"Read WhatsApp messages. Call when user says: read messages, check WhatsApp, ada pesan, baca WA.",
        "parameters":{"type":"object","properties":{}}}},

    # Spotify — basic
    {"type":"function","function":{
        "name":"spotify_play_pause",
        "description":"Play or pause Spotify. Call when user says: play music, pause, resume, stop song.",
        "parameters":{"type":"object","properties":{"action":{"type":"string","enum":["play","pause","toggle"]}}}}},
    {"type":"function","function":{
        "name":"spotify_next_track",
        "description":"Skip to next song. Call when user says: next song, skip.",
        "parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{
        "name":"spotify_previous_track",
        "description":"Go to previous song. Call when user says: previous, go back.",
        "parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{
        "name":"spotify_play_playlist",
        "description":"Play a Spotify playlist by name.",
        "parameters":{"type":"object","properties":{"playlist_name":{"type":"string"}},"required":["playlist_name"]}}},
    {"type":"function","function":{
        "name":"spotify_set_volume",
        "description":"Set Spotify volume. Call when user says: louder, quieter, volume X percent.",
        "parameters":{"type":"object","properties":{"volume":{"type":"integer"}},"required":["volume"]}}},

    # Spotify — new features
    {"type":"function","function":{
        "name":"spotify_search_play",
        "description":"Search and play song/artist/album on Spotify. Call when user says: play [song name], play songs by [artist], play album [name].",
        "parameters":{"type":"object","properties":{
            "query":       {"type":"string"},
            "search_type": {"type":"string","enum":["track","artist","album","playlist"]},
        },"required":["query"]}}},
    {"type":"function","function":{
        "name":"spotify_play_genre",
        "description":"Play music by mood or genre. Call when user says: play relaxing music, play jazz, play driving music, play something happy, play workout music.",
        "parameters":{"type":"object","properties":{"genre":{"type":"string"}},"required":["genre"]}}},
    {"type":"function","function":{
        "name":"spotify_now_playing",
        "description":"Get current song info. Call when user says: what song is this, what's playing, song name.",
        "parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{
        "name":"spotify_who_sings",
        "description":"Who sings current song. Call when user says: who sings this, what artist, who is singing.",
        "parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{
        "name":"spotify_like_song",
        "description":"Save current song to Liked Songs. Call when user says: like this song, save this song, add to favorites.",
        "parameters":{"type":"object","properties":{}}}},
    {"type":"function","function":{
        "name":"spotify_unlike_song",
        "description":"Remove current song from Liked Songs. Call when user says: unlike this song, remove from favorites.",
        "parameters":{"type":"object","properties":{}}}},

    # Car controls
    {"type":"function","function":{
        "name":"control_car_ac",
        "description":"MANDATORY: Control AC. Call for: turn on AC, turn off AC, AC on, AC off, set temperature, matikan AC, nyalakan AC.",
        "parameters":{"type":"object","properties":{
            "action":      {"type":"string","enum":["on","off","status"]},
            "temperature": {"type":"integer"},
        },"required":["action"]}}},
    {"type":"function","function":{
        "name":"control_car_window",
        "description":"MANDATORY: Control window. Call for: open window, close window, roll down, roll up, window halfway.",
        "parameters":{"type":"object","properties":{
            "position":{"type":"string","enum":["open","close","half"]}
        },"required":["position"]}}},
]

# ─────────────────────────────────────────────────────────────
# TOOL MAP
# ─────────────────────────────────────────────────────────────
TOOL_MAP = {
    "control_bot_emote":      lambda a: control_bot_emote(a.get("emote", "ready")),
    "get_weather":            lambda a: get_weather(a.get("city", state["driver_location"].get("city", DEFAULT_CITY))),
    "get_traffic":            lambda a: get_traffic(a.get("origin"), a.get("destination")),
    "get_directions":         lambda a: get_directions(a.get("destination", "")),
    "get_flood_alert":        lambda a: get_flood_alert(),
    "get_fuel_stations":      lambda a: get_fuel_stations(a.get("location")),
    "find_nearby_grocery":    lambda a: find_nearby("supermarket", "grocery stores"),
    "find_nearby_restaurant": lambda a: find_nearby("restaurant", "restaurants"),
    "find_nearby_atm":        lambda a: find_nearby("atm", "ATMs"),
    "find_nearby_pharmacy":   lambda a: find_nearby("pharmacy", "pharmacies"),
    "find_nearby_parking":    lambda a: find_nearby("parking", "parking spots"),
    "emergency_sos":          lambda a: emergency_sos(a.get("type", "general")),
    "get_driver_status":      lambda a: get_driver_status(),
    "read_whatsapp_messages": lambda a: read_whatsapp_messages(),
    "spotify_play_pause":     lambda a: spotify_play_pause(a.get("action", "toggle")),
    "spotify_next_track":     lambda a: spotify_next_track(),
    "spotify_previous_track": lambda a: spotify_previous_track(),
    "spotify_play_playlist":  lambda a: spotify_play_playlist(a.get("playlist_name", "")),
    "spotify_set_volume":     lambda a: spotify_set_volume(int(a.get("volume", 50))),
    "spotify_search_play":    lambda a: spotify_search_play(a.get("query",""), a.get("search_type","track")),
    "spotify_play_genre":     lambda a: spotify_play_genre(a.get("genre", "pop")),
    "spotify_now_playing":    lambda a: spotify_now_playing(),
    "spotify_who_sings":      lambda a: spotify_who_sings(),
    "spotify_like_song":      lambda a: spotify_like_song(),
    "spotify_unlike_song":    lambda a: spotify_unlike_song(),
    "control_car_ac":         lambda a: control_car_ac(a.get("action", "status"), a.get("temperature")),
    "control_car_window":     lambda a: control_car_window(a.get("position", "close")),
    "navigate_to_nearest_grocery":    lambda a: navigate_to_nearest("supermarket", "grocery store"),
    "navigate_to_nearest_restaurant": lambda a: navigate_to_nearest("restaurant", "restaurant"),
    "navigate_to_nearest_fuel":       lambda a: navigate_to_nearest("gas_station", "fuel station"),
    "navigate_to_nearest_atm":        lambda a: navigate_to_nearest("atm", "ATM"),
    "navigate_to_nearest_pharmacy":   lambda a: navigate_to_nearest("pharmacy", "pharmacy"),
    "navigate_to_nearest_parking":    lambda a: navigate_to_nearest("parking", "parking"),
    "navigate_to_nearest_fastfood":   lambda a: navigate_to_nearest("restaurant", a.get("name","fast food")),
}

# ─────────────────────────────────────────────────────────────
# SPEAK DIRECT
# ─────────────────────────────────────────────────────────────
SPEAK_DIRECT = {
    "control_bot_emote",
    "get_fuel_stations", "find_nearby_grocery", "find_nearby_restaurant",
    "find_nearby_atm", "find_nearby_pharmacy", "find_nearby_parking",
    "navigate_to_nearest_grocery", "navigate_to_nearest_restaurant",
    "navigate_to_nearest_fuel", "navigate_to_nearest_atm",
    "navigate_to_nearest_pharmacy", "navigate_to_nearest_parking",
    "navigate_to_nearest_fastfood",
    "get_directions", "get_weather", "get_traffic", "get_flood_alert",
    "get_driver_status", "emergency_sos",
    "control_car_ac", "control_car_window",
    "spotify_play_pause", "spotify_next_track", "spotify_previous_track",
    "spotify_set_volume", "spotify_play_playlist",
    "spotify_search_play", "spotify_play_genre", "spotify_now_playing",
    "spotify_who_sings", "spotify_like_song", "spotify_unlike_song",
}

# ─────────────────────────────────────────────────────────────
# KEYWORD FALLBACK
# ─────────────────────────────────────────────────────────────
def _keyword_fallback(text):
    u = text.lower().strip()

    # ── Math ─────────────────────────────────────────────────
    math_patterns = [
        (r'(\d+\.?\d*)\s*(?:times|multiplied by|dikali)\s*(\d+\.?\d*)',   '*'),
        (r'(\d+\.?\d*)\s*(?:plus|add(?:ed to)?|ditambah)\s*(\d+\.?\d*)', '+'),
        (r'(\d+\.?\d*)\s*(?:minus|subtract|dikurang)\s*(\d+\.?\d*)',      '-'),
        (r'(\d+\.?\d*)\s*(?:divided by|over|dibagi)\s*(\d+\.?\d*)',       '/'),
        (r'what(?:\'s| is)\s+(\d+\.?\d*)\s*([+\-*/])\s*(\d+\.?\d*)',     'direct'),
        (r'(\d+\.?\d*)\s*([+\-*/])\s*(\d+\.?\d*)',                        'direct'),
    ]
    for pattern, op in math_patterns:
        m = re.search(pattern, u)
        if m:
            try:
                if op == 'direct':
                    a, op2, b = float(m.group(1)), m.group(2), float(m.group(3))
                    result = eval(f"{a}{op2}{b}")
                else:
                    a, b = float(m.group(1)), float(m.group(2))
                    result = eval(f"{a}{op}{b}")
                if isinstance(result, float) and result == int(result):
                    result = int(result)
                return ("math_result", {"result": f"That's {result}."})
            except Exception:
                pass

    # ── Navigate to nearest ───────────────────────────────────
    navigate_nearest_triggers = [
        "navigate to nearest", "take me to nearest", "take me to the nearest",
        "go to nearest", "go to the nearest", "drive me to nearest",
        "drive to nearest", "bring me to nearest", "find me the nearest",
    ]
    is_navigate_nearest = any(t in u for t in navigate_nearest_triggers)

    if is_navigate_nearest or any(k in u for k in [
        "nearest mcdonald", "nearest kfc", "nearest burger king",
        "nearest pizza hut", "nearest fast food", "nearest mcdonalds",
        "take me to mcd", "take me to kfc", "nearest jollibee",
    ]):
        if any(k in u for k in [
            "mcdonald", "mcdonalds", "mcd", "kfc", "burger king",
            "pizza hut", "jollibee", "fast food", "burger", "pizza",
        ]):
            return ("navigate_to_nearest_fastfood", {"name": u})

    if is_navigate_nearest:
        if any(k in u for k in [
            "grocery", "supermarket", "indomaret", "alfamart",
            "mini market", "minimarket", "swalayan",
        ]):
            return ("navigate_to_nearest_grocery", {})
        if any(k in u for k in [
            "restaurant", "food", "makan", "warung", "eat",
            "restoran", "rumah makan",
        ]):
            return ("navigate_to_nearest_restaurant", {})
        if any(k in u for k in [
            "gas", "fuel", "petrol", "bensin", "spbu",
            "pertamina", "shell", "pom bensin",
        ]):
            return ("navigate_to_nearest_fuel", {})
        if any(k in u for k in ["atm", "cash", "withdraw", "tarik tunai"]):
            return ("navigate_to_nearest_atm", {})
        if any(k in u for k in [
            "pharmacy", "apotek", "apotik", "obat", "medicine",
        ]):
            return ("navigate_to_nearest_pharmacy", {})
        if any(k in u for k in ["parking", "parkir"]):
            return ("navigate_to_nearest_parking", {})

    # ── Show nearby ───────────────────────────────────────────
    if any(k in u for k in [
        "where is gas", "where is fuel", "find gas", "show gas",
        "gas station", "fuel station", "bensin dimana", "spbu dimana",
        "cari spbu", "nearest gas", "nearest fuel", "nearest spbu",
        "nearest pertamina", "pom bensin",
    ]):
        return ("get_fuel_stations", {})

    if any(k in u for k in [
        "where is grocery", "show grocery", "find grocery",
        "indomaret dimana", "alfamart dimana", "cari supermarket",
        "nearest mini market", "nearest indomaret", "nearest alfamart",
    ]):
        return ("find_nearby_grocery", {})

    if any(k in u for k in [
        "show restaurant", "find restaurant", "where to eat",
        "restoran mana", "cari makan", "nearest restaurant", "nearest warung",
    ]):
        return ("find_nearby_restaurant", {})

    if any(k in u for k in [
        "show atm", "find atm", "atm dimana", "cari atm",
        "nearest atm", "where is atm",
    ]):
        return ("find_nearby_atm", {})

    if any(k in u for k in [
        "show pharmacy", "find pharmacy", "apotek dimana",
        "cari apotek", "nearest pharmacy", "nearest apotek",
    ]):
        return ("find_nearby_pharmacy", {})

    if any(k in u for k in [
        "show parking", "find parking", "parkir dimana",
        "cari parkir", "nearest parking",
    ]):
        return ("find_nearby_parking", {})

    # ── AC ────────────────────────────────────────────────────
    if any(k in u for k in [
        "turn off ac", "ac off", "stop ac", "disable ac",
        "matikan ac", "mati ac",
    ]):
        return ("control_car_ac", {"action": "off"})

    if any(k in u for k in [
        "turn on ac", "ac on", "switch on ac", "start ac",
        "nyalakan ac", "hidupkan ac",
    ]):
        temp_match = re.search(r'(\d+)\s*(?:degree|celsius|c|derajat)?', u)
        if temp_match:
            t = int(temp_match.group(1))
            if 16 <= t <= 30:
                return ("control_car_ac", {"action": "on", "temperature": t})
        return ("control_car_ac", {"action": "on"})

    if any(k in u for k in ["ac status", "how is ac", "is ac on", "is ac off"]):
        return ("control_car_ac", {"action": "status"})

    temp_match = re.search(
        r'(?:set|temperature|temp|suhu)\s*(?:to|ke|jadi)?\s*(\d+)', u
    )
    if temp_match and any(k in u for k in [
        "temperature", "temp", "suhu", "degree", "derajat",
    ]):
        t = int(temp_match.group(1))
        if 16 <= t <= 30:
            return ("control_car_ac", {"action": "on", "temperature": t})

    # ── Bot emotes (BEFORE window) ────────────────────────────
    if any(k in u for k in [
        "robot dance", "bot dance", "dravixa dance", "dance",
    ]):
        return ("control_bot_emote", {"emote": "dance"})

    if any(k in u for k in [
        "shake your head", "shake head", "shake no",
        "robot shake", "shake", "shaking",
    ]):
        return ("control_bot_emote", {"emote": "shake"})

    if any(k in u for k in [
        "robot confused", "bot confused", "confused",
        "are you confused", "look confused",
    ]):
        return ("control_bot_emote", {"emote": "confused"})

    if any(k in u for k in [
        "robot nod", "nod yes", "bot nod", "nod your head", "nod",
    ]):
        return ("control_bot_emote", {"emote": "nod"})

    if any(k in u for k in [
        "robot sleep", "bot sleep", "go to sleep", "sleep", "dravixa sleep",
    ]):
        return ("control_bot_emote", {"emote": "sleep"})

    if any(k in u for k in [
        "standby", "robot ready", "bot ready", "robot stand", "attention",
    ]):
        return ("control_bot_emote", {"emote": "ready"})

    # ── Window ────────────────────────────────────────────────
    if any(k in u for k in [
        "open window", "roll down", "window open",
        "buka jendela", "turunkan kaca", "buka kaca",
        "open the window", "lower the window",
    ]):
        return ("control_car_window", {"position": "open"})

    if any(k in u for k in [
        "close window", "roll up", "window close", "shut window",
        "tutup jendela", "naikkan kaca", "tutup kaca",
        "closed window", "window closed", "close the window",
        "shut the window", "window shut", "please close",
    ]):
        return ("control_car_window", {"position": "close"})

    if any(k in u for k in [
        "half window", "window half", "halfway", "setengah jendela",
    ]):
        return ("control_car_window", {"position": "half"})
     # ── Music basic (BEFORE search) ──────────────────────────────
    if any(k in u for k in [
     	"next song", "skip song", "next track", "skip this",
     	"lagu berikutnya", "skip aja", "next music",
    ]):
     	return ("spotify_next_track", {})
    if any(k in u for k in [
     	"previous song", "prev song", "last song", "go back song",
     	"lagu sebelumnya", "back song", "previous music",
     	"play previous", "go back", "last track",
    ]):
     	return ("spotify_previous_track", {})
    if any(k in u for k in [
     	"pause music", "pause song", "stop music", "stop song",
     	"pause lagu", "stop lagu",
    ]):
     	return ("spotify_play_pause", {"action": "pause"})
    if any(k in u for k in [
    	"play music", "resume music", "resume song", "play lagu",
    	"putar musik", "play again",
    ]):
    	return ("spotify_play_pause", {"action": "play"})   

    # ── Spotify search ────────────────────────────────────────
    play_match = re.search(r'play\s+(?:song\s+)?(?:called\s+)?(.+)', u)
    if play_match and not any(k in u for k in [
        "playlist", "music", "lagu", "something", "some",
        "relaxing", "chill", "energetic", "driving", "jazz",
        "rock", "pop", "classical", "happy", "sad",
        "play music", "play lagu", "putar musik",
    ]):
        query = play_match.group(1).strip()
        if len(query) > 2:
            if any(k in u for k in ["by", "songs by", "artist", "from"]):
                return ("spotify_search_play", {"query": query, "search_type": "artist"})
            return ("spotify_search_play", {"query": query, "search_type": "track"})

    # ── Genre / mood ──────────────────────────────────────────
    if any(k in u for k in [
        "relaxing music", "chill music", "energetic music",
        "workout music", "driving music", "focus music",
        "happy music", "sad music", "romantic music",
        "play jazz", "play rock", "play pop", "play classical",
        "play indie", "play hip hop", "play electronic",
        "play something", "play some",
    ]):
        for mood in [
            "relaxing", "chill", "energetic", "workout", "driving",
            "focus", "happy", "sad", "romantic", "jazz", "rock", "pop",
            "classical", "indie", "hip hop", "electronic", "sleep",
        ]:
            if mood in u:
                return ("spotify_play_genre", {"genre": mood})
        return ("spotify_play_genre", {"genre": "pop"})

    # ── Now playing ───────────────────────────────────────────
    if any(k in u for k in [
        "what song", "what is this song", "what's playing",
        "song name", "current song", "now playing",
        "lagu apa", "lagu ini apa",
    ]):
        return ("spotify_now_playing", {})

    if any(k in u for k in [
        "who sings", "who is this", "who is singing",
        "what artist", "siapa penyanyi", "siapa yang nyanyi",
    ]):
        return ("spotify_who_sings", {})

    if any(k in u for k in [
        "like this song", "save this song", "add to favorites",
        "i like this", "save this", "tambah ke favorit",
    ]):
        return ("spotify_like_song", {})

    if any(k in u for k in [
        "unlike this song", "remove from favorites",
        "dislike this song", "hapus dari favorit",
    ]):
        return ("spotify_unlike_song", {})

    # ── Music basic ───────────────────────────────────────────
    if any(k in u for k in [
        "next song", "skip song", "next track", "skip this",
        "lagu berikutnya", "skip aja",
    ]):
        return ("spotify_next_track", {})

    if any(k in u for k in [
        "previous song", "prev song", "last song", "go back song",
        "lagu sebelumnya", "back song",
    ]):
        return ("spotify_previous_track", {})

    if any(k in u for k in [
        "pause music", "pause song", "stop music", "stop song",
        "pause lagu", "stop lagu",
    ]):
        return ("spotify_play_pause", {"action": "pause"})

    if any(k in u for k in [
        "play music", "resume music", "resume song", "play lagu",
        "putar musik", "play again",
    ]):
        return ("spotify_play_pause", {"action": "play"})

    vol_match = re.search(r'volume\s*(?:to|ke|jadi)?\s*(\d+)', u)
    if vol_match:
        return ("spotify_set_volume", {"volume": int(vol_match.group(1))})

    if any(k in u for k in ["louder", "turn up", "keraskan", "tambah volume"]):
        return ("spotify_set_volume", {"volume": 80})

    if any(k in u for k in ["quieter", "turn down", "kecilkan", "kurangi volume"]):
        return ("spotify_set_volume", {"volume": 30})

    # ── Traffic & flood ───────────────────────────────────────
    if any(k in u for k in [
        "traffic", "macet", "congestion", "jalan macet",
        "how is traffic", "gimana lalu lintas",
    ]):
        return ("get_traffic", {})

    if any(k in u for k in [
        "flood", "banjir", "hujan deras", "is it flooding",
    ]):
        return ("get_flood_alert", {})

    # ── Driver ────────────────────────────────────────────────
    if any(k in u for k in [
        "tired", "lelah", "rest", "break", "istirahat",
        "how long driving", "sudah berapa lama",
    ]):
        return ("get_driver_status", {})

    # ── WhatsApp ──────────────────────────────────────────────
    if any(k in u for k in [
        "read message", "check whatsapp", "any message",
        "ada pesan", "baca wa", "any wa", "new message",
    ]):
        return ("read_whatsapp_messages", {})

    # ── Emergency ─────────────────────────────────────────────
    if any(k in u for k in [
        "emergency", "sos", "help me", "tolong",
        "call police", "call ambulance", "accident", "kecelakaan",
    ]):
        if any(k in u for k in ["police", "polisi", "110"]):
            return ("emergency_sos", {"type": "police"})
        if any(k in u for k in ["ambulance", "119", "doctor", "medic"]):
            return ("emergency_sos", {"type": "ambulance"})
        if any(k in u for k in ["fire", "kebakaran", "113"]):
            return ("emergency_sos", {"type": "fire"})
        return ("emergency_sos", {"type": "general"})

    return None

# ─────────────────────────────────────────────────────────────
# WORKERS
# ─────────────────────────────────────────────────────────────
def drain_queue(q):
    count = 0
    while not q.empty():
        try: q.get_nowait(); q.task_done(); count += 1
        except queue.Empty: break
    return count

def tts_worker():
    def _auto_emote(text):
        u = text.lower()
        if any(k in u for k in [
            "sorry", "can't", "unable", "don't know",
            "unavailable", "error", "not connected", "say again",
        ]):
            return "CONFUSED"
        if any(k in u for k in [
            "got it", "sure", "done", "no problem", "showing",
            "found", "navigating", "playing", "ac on", "ac off",
            "window opening", "window closing", "window halfway",
            "turned off", "turned on", "saved", "paused",
        ]):
            return "NOD"
        if any(k in u for k in [
            "hey", "listening", "i'm here", "ready", "i'm listening",
        ]):
            return "READY"
        if any(k in u for k in [
            "emergency", "calling police", "calling ambulance",
            "sos", "calling fire",
        ]):
            return "SHAKE"
        if any(k in u for k in [
            "let's go", "great", "awesome", "nice",
        ]):
            return "DANCE"
        return None

    while True:
        text = tts_queue.get()
        if text is None: break

        state["is_speaking"]    = True
        state["interrupt_flag"] = False

        if len(text) > 250:
            text = text[:250] + "..."

        UI.dravixa(text)
        hmi({"type": "chat", "text": text, "is_user": False})
        hmi({"type": "status", "awake": state["is_awake"],
             "processing": False, "speaking": True})

        # Duck Spotify volume
        threading.Thread(target=spotify_duck, daemon=True).start()

        # Auto emote
        emote_cmd = _auto_emote(text)
        if emote_cmd and arduino_state["connected"]:
            threading.Thread(
                target=lambda c=emote_cmd: _arduino_send(c),
                daemon=True
            ).start()

        try:
            t0 = time.perf_counter()
            samples, sr = tts_model.create(text, voice="af_heart", speed=1.1, lang="en-us")
            samples = samples.astype(np.float32)

            target_sr = 16000
            if sr != target_sr:
                num_samples = int(len(samples) * target_sr / sr)
                samples     = scipy.signal.resample(samples, num_samples)
                sr          = target_sr

            latency["tts"] = (time.perf_counter() - t0) * 1000

            if samples.ndim == 1:
                samples = np.column_stack([samples, samples])

            try:
                dev_info = sd.query_devices(TTS_OUTPUT_DEVICE, "output")
                max_ch   = dev_info["max_output_channels"]
                if max_ch < 2:
                    samples = samples[:, 0]
                    sd.play(samples, sr, device=TTS_OUTPUT_DEVICE, channels=1)
                else:
                    sd.play(samples, sr, device=TTS_OUTPUT_DEVICE, channels=2)
            except Exception:
                sd.play(samples, sr)

            dur     = len(samples) / sr
            t_start = time.time()
            while time.time() - t_start < dur:
                if state["interrupt_flag"]: sd.stop(); break
                time.sleep(0.05)
            else:
                sd.wait()

            time.sleep(0.15)
            UI.latency(latency["stt"], latency["llm"], latency["tts"])

        except Exception as e:
            UI.error(f"TTS: {e}")

        # Restore Spotify volume
        threading.Thread(target=spotify_unduck, daemon=True).start()

        state["is_speaking"]    = False
        state["interrupt_flag"] = False
        hmi({"type": "status", "awake": state["is_awake"],
             "processing": False, "speaking": False})
        tts_queue.task_done()

def stt_worker():
    while True:
        audio_file = audio_processing_queue.get()
        if audio_file is None: break
        try:
            t0 = time.perf_counter()
            segments, _ = stt_model.transcribe(
                audio_file, beam_size=5, condition_on_previous_text=False)
            transcription = "".join([s.text for s in segments]).strip()
            latency["stt"] = (time.perf_counter() - t0) * 1000
            if transcription and len(transcription) > 3:
                words = transcription.split()
                if len(set(w.lower() for w in words)) >= 2 or len(words) <= 3:
                    UI.you(transcription)
                    hmi({"type": "chat", "text": transcription, "is_user": True})
                    hmi({"type": "status", "awake": True, "processing": True, "speaking": False})
                    drain_queue(agent_task_queue)
                    agent_task_queue.put(transcription)
        except Exception as e:
            UI.error(f"STT: {e}")
        finally:
            try:
                if os.path.exists(audio_file): os.remove(audio_file)
            except Exception: pass
        audio_processing_queue.task_done()

def llm_agent_worker():
    system_prompt = (
        "You are Dravixa, a friendly Toyota car assistant for Jakarta, Indonesia. "
        "Personality: Chill, casual. Use: 'Yeah', 'Sure', 'Got it', 'No problem'. "
        "RULES: "
        "1. ONE sentence max 15 words. "
        "2. ALWAYS call tools — NEVER answer without the right tool. "
        "3. Gas/fuel/bensin/SPBU -> get_fuel_stations. "
        "4. Grocery/supermarket/indomaret -> find_nearby_grocery. "
        "5. Restaurant/makan/hungry -> find_nearby_restaurant. "
        "6. ATM/cash -> find_nearby_atm. "
        "7. Pharmacy/apotek -> find_nearby_pharmacy. "
        "8. Parking/parkir -> find_nearby_parking. "
        "9. AC on/off/temp -> control_car_ac MANDATORY. "
        "10. Window -> control_car_window MANDATORY. "
        "11. Emergency -> emergency_sos IMMEDIATELY. "
        "12. Navigate/drive to -> get_directions. "
        "13. Play [song] -> spotify_search_play. "
        "14. Play [genre] music -> spotify_play_genre. "
        "15. What song / who sings -> spotify_now_playing / spotify_who_sings. "
        "16. Like/save this song -> spotify_like_song. "
        "17. Driver is driving. Be very brief."
    )

    while True:
        user_text = agent_task_queue.get()
        if user_text is None: break
        state["is_processing"] = True
        chat_history.append({"role": "user", "content": user_text})
        if len(chat_history) > MAX_HISTORY:
            chat_history.pop(0)

        # High confidence — skip LLM entirely
        kb = _keyword_fallback(user_text)
        if kb:
            name, args = kb
            if name == "math_result":
                result = args["result"]
                UI.tool("math", result)
                tts_queue.put(result)
                chat_history.append({"role": "assistant", "content": result})
                state["is_processing"] = False
                agent_task_queue.task_done()
                continue
            elif name in SPEAK_DIRECT:
                result = TOOL_MAP[name](args)
                UI.tool(f"direct:{name}", result)
                tts_queue.put(result)
                chat_history.append({"role": "assistant", "content": result})
                state["is_processing"] = False
                agent_task_queue.task_done()
                continue

        payload = {
            "model": "qwen2.5:3b",
            "messages": [{"role": "system", "content": system_prompt}] + chat_history,
            "stream": False,
            "tools": tools_schema,
            "options": {
                "num_predict": 30,
                "temperature": 0.2,
                "top_k": 10,
                "top_p": 0.7,
                "repeat_penalty": 1.2,
            }
        }

        try:
            t0 = time.perf_counter()
            resp = requests.post(
                "http://localhost:11434/api/chat",
                json=payload, timeout=30,
                headers={"Connection": "close"})
            latency["llm"] = (time.perf_counter() - t0) * 1000

            if resp.status_code != 200:
                tts_queue.put("Brain hiccup. Say again?")
                state["is_processing"] = False
                agent_task_queue.task_done()
                continue

            message   = resp.json().get("message", {})
            tool_done = False
            direct    = False

            if "tool_calls" in message and message["tool_calls"]:
                for tc in message["tool_calls"]:
                    name = tc.get("function", {}).get("name")
                    args = tc.get("function", {}).get("arguments", {})
                    if isinstance(args, str):
                        try: args = json.loads(args)
                        except Exception: args = {}
                    if name not in TOOL_MAP: continue
                    result = TOOL_MAP[name](args)
                    UI.tool(name, result)
                    tool_done = True
                    chat_history.append(message)
                    chat_history.append({"role": "tool", "content": result})
                    if name in SPEAK_DIRECT or name == "read_whatsapp_messages":
                        tts_queue.put(result)
                        chat_history.append({"role": "assistant", "content": result})
                        direct = True
                    else:
                        follow = dict(payload)
                        follow["messages"] = (
                            [{"role": "system", "content": system_prompt}] + chat_history
                        )
                        follow["options"] = {**payload["options"], "num_predict": 25}
                        t1 = time.perf_counter()
                        try:
                            fu = requests.post(
                                "http://localhost:11434/api/chat",
                                json=follow, timeout=30,
                                headers={"Connection": "close"}).json()
                            latency["llm"] += (time.perf_counter() - t1) * 1000
                            message = fu.get("message", {})
                        except Exception: pass

            if not tool_done and not direct:
                kb = _keyword_fallback(user_text)
                if kb:
                    name, args = kb
                    if name == "math_result":
                        result = args["result"]
                    else:
                        result = TOOL_MAP[name](args)
                    UI.tool(f"fallback:{name}", result)
                    tts_queue.put(result)
                    chat_history.append({"role": "assistant", "content": result})
                    state["is_processing"] = False
                    agent_task_queue.task_done()
                    continue

            if not direct:
                final = message.get("content", "").strip()
                if final:
                    if len(final) > 250: final = final[:250] + "..."
                    chat_history.append({"role": "assistant", "content": final})
                    tts_queue.put(final)
                elif tool_done:
                    tts_queue.put("Got it.")
                else:
                    tts_queue.put("Say again?")

        except requests.exceptions.Timeout:
            UI.error("Ollama timeout")
            kb = _keyword_fallback(user_text)
            if kb and kb[0] != "math_result":
                name, args = kb
                result = TOOL_MAP[name](args)
                tts_queue.put(result)
            else:
                tts_queue.put("Brain's slow. Try again?")
        except requests.exceptions.ConnectionError:
            UI.error("Ollama offline")
            tts_queue.put("Can't reach my brain.")
        except Exception as e:
            UI.error(f"Agent: {e}")
            tts_queue.put("Brain error.")

        state["is_processing"] = False
        agent_task_queue.task_done()

def fatigue_monitor():
    while True:
        time.sleep(60)
        h = (time.time() - state["session_start"]) / 3600
        if h >= 2 and (time.time() - state["last_fatigue_warn"]) >= 7200:
            state["last_fatigue_warn"] = time.time()
            if state["is_awake"]:
                tts_queue.put(f"Hey, {int(h)} hours driving. Rest soon yeah?")

def location_update_worker():
    while True:
        time.sleep(300)
        loc = get_current_location()
        if loc != state["driver_location"]:
            state["driver_location"] = loc
def mood_bridge_worker():
    """Listen for mood/alert events from drivermonitor on UDP port 5003."""
    import socket as _socket
    sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 5003))
        sock.settimeout(1.0)
        UI.info("Mood bridge: listening on port 5003")
    except Exception as e:
        UI.info(f"Mood bridge: unavailable ({e})")
        return

    _last_trigger = {"Sad": 0, "Anger": 0, "WARNING": 0, "CRITICAL": 0}

    # Pending mood confirmation state
    _pending_mood = {"active": False, "emotion": None, "asked_at": 0}

    def _ask_mood_music(emotion):
    	if _pending_mood["active"]:
    	    return
    	_pending_mood["active"]   = True
    	_pending_mood["emotion"]  = emotion
    	_pending_mood["asked_at"] = time.time()
    	state["is_awake"]        = True
    	state["last_interaction"] = time.time()
    	hmi({"type": "status", "awake": True, "processing": False, "speaking": False})
    	UI.wake()
    	if emotion == "Sad":
    	    msg = "Hey, you seem a bit down. Want me to play some music to cheer you up?"
    	else:
    	    msg = "You seem tense. Want me to put on something relaxing?"
    	tts_queue.put(msg)
    	UI.info(f"Mood bridge: asked about {emotion} music, waiting for reply...")

    def _handle_voice_reply(text: str):
        """
        Called from stt_worker when driver responds to mood question.
        Returns True if consumed the reply, False if not a mood reply.
        """
        if not _pending_mood["active"]:
            return False

        # Timeout — if driver doesn't reply in 30 sec, forget it
        if time.time() - _pending_mood["asked_at"] > 30:
            _pending_mood["active"]  = False
            _pending_mood["emotion"] = None
            UI.info("Mood bridge: question timed out")
            return False

        u = text.lower().strip()

        # YES responses
        if any(k in u for k in [
            "yes", "yeah", "sure", "ok", "okay", "please",
            "play", "go ahead", "yep", "boleh", "iya", "mau",
        ]):
            emotion  = _pending_mood["emotion"]
            _pending_mood["active"]  = False
            _pending_mood["emotion"] = None
            tts_queue.put("Sure, playing your test playlist.")
            time.sleep(2)
            result = spotify_play_playlist("test")
            UI.tool(f"mood:{emotion}→playlist:test", result)
            return True

        # NO responses
        if any(k in u for k in [
            "no", "nope", "don't", "dont", "skip", "nah",
            "not now", "tidak", "gak", "ga",
        ]):
            _pending_mood["active"]  = False
            _pending_mood["emotion"] = None
            tts_queue.put("No problem, just let me know if you need anything.")
            return True

        return False  # not a yes/no, let LLM handle normally

    # Patch stt_worker to intercept mood replies
    # We do this by wrapping agent_task_queue.put
    _original_put = agent_task_queue.put

    def _intercepted_put(text):
        if _handle_voice_reply(text):
            return  # consumed — don't send to LLM
        _original_put(text)

    agent_task_queue.put = _intercepted_put
    UI.info("Mood bridge: voice intercept active")

    while True:
        try:
            data, _ = sock.recvfrom(4096)
            pkt      = json.loads(data.decode())
            t        = pkt.get("type")
            now      = time.time()

            if t == "driver_mood":
                emotion = pkt.get("emotion", "")
                conf    = pkt.get("confidence", 0)

                if emotion == "Sad" and now - _last_trigger["Sad"] > 120:
                    _last_trigger["Sad"] = now
                    _ask_mood_music("Sad")

                elif emotion == "Anger" and now - _last_trigger["Anger"] > 120:
                    _last_trigger["Anger"] = now
                    _ask_mood_music("Anger")

            elif t == "driver_alert":
                level   = pkt.get("alert_level", "")
                message = pkt.get("message", "")

                if level == "CRITICAL" and now - _last_trigger["CRITICAL"] > 10:
                    _last_trigger["CRITICAL"] = now
                    tts_queue.put(message)
                    if arduino_state["connected"]:
                        threading.Thread(
                            target=lambda: _arduino_send("SHAKE"),
                            daemon=True
                        ).start()

                elif level == "WARNING" and now - _last_trigger["WARNING"] > 180:
                    _last_trigger["WARNING"] = now
                    tts_queue.put(message)

        except socket.timeout:
            continue
        except Exception as e:
            UI.error(f"Mood bridge: {e}")

# ─────────────────────────────────────────────────────────────
# STARTUP
# ─────────────────────────────────────────────────────────────
loc = get_current_location()
state["driver_location"] = loc
UI.info(f"Location: {loc['city']} ({loc['lat']:.4f}, {loc['lng']:.4f})")

#init_whatsapp()
init_arduino(ARDUINO_PORT)

#threading.Thread(target=whatsapp_monitor_worker, daemon=True).start()
threading.Thread(target=tts_worker,            daemon=True).start()
threading.Thread(target=stt_worker,            daemon=True).start()
threading.Thread(target=llm_agent_worker,      daemon=True).start()
threading.Thread(target=fatigue_monitor,       daemon=True).start()
threading.Thread(target=location_update_worker, daemon=True).start()
threading.Thread(target=mood_bridge_worker, daemon=True).start()
# ─────────────────────────────────────────────────────────────
# RESPEAKER INIT
# ─────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────
# RESPEAKER INIT
# ─────────────────────────────────────────────────────────────
try:
    dev = usb.core.find(idVendor=0x2886, idProduct=0x0018)
    if not dev: raise ValueError("ReSpeaker not found on USB")
    Mic_tuning = Tuning(dev)
    try:
        Mic_tuning.write('NONSTATNOISEONOFF', 1)
        Mic_tuning.write('STATNOISEONOFF', 1)
        Mic_tuning.write('ECHOONOFF', 1)
        Mic_tuning.write('GAMMAVAD', 2.0)
        Mic_tuning.write('GAMMAVADSE', 2.0)
        Mic_tuning.write('AGCONOFF', 1)
        Mic_tuning.write('AGCDESIREDLEVEL', 0.03)
        Mic_tuning.write('AGCMAXGAIN', 30)
    except Exception: pass
except Exception as e:
    UI.error(f"ReSpeaker DSP: {e}"); sys.exit(1)

audio = pyaudio.PyAudio()
respeaker_index = None
for i in range(audio.get_device_count()):
    if "ReSpeaker" in audio.get_device_info_by_index(i).get("name", ""):
        respeaker_index = i; break

if respeaker_index is None:
    UI.error("ReSpeaker not found in PyAudio device list"); sys.exit(1)

stream = None
for buf_size in [1024, 2048, 512, 4096]:
    try:
        stream = audio.open(
            format=pyaudio.paInt16, channels=6, rate=16000,
            input=True, input_device_index=respeaker_index,
            frames_per_buffer=buf_size)
        UI.info(f"ReSpeaker opened with buffer {buf_size}")
        break
    except Exception as e:
        UI.info(f"Buffer {buf_size} failed: {e}")

if stream is None:
    UI.error("Failed to open ReSpeaker stream"); sys.exit(1)

ring_buffer    = deque(maxlen=int(16000 / 1024 * 1.5))
audio_counter  = 0
CHUNKS_PER_SEC = int(16000 / 1024)

UI.banner(loc["city"], respeaker_index)

# ─────────────────────────────────────────────────────────────
# MAIN AUDIO LOOP
# ─────────────────────────────────────────────────────────────
voice_chunks   = 0
tts_start_time = 0

try:
    while True:
        raw = stream.read(1024, exception_on_overflow=False)

        # ── Barge-in ─────────────────────────────────────────
        if state["is_speaking"]:
            if tts_start_time == 0: tts_start_time = time.time()
            if (time.time() - tts_start_time) < BARGE_IN_GRACE_PERIOD:
                ring_buffer.clear(); voice_chunks = 0; continue
            if Mic_tuning.is_voice():
                try:
                    doa = Mic_tuning.direction
                    voice_chunks += 1 if (DOA_ACCEPT_MIN <= doa <= DOA_ACCEPT_MAX) else 0
                except Exception:
                    voice_chunks += 1
                if voice_chunks >= BARGE_IN_MIN_CHUNKS:
                    state["interrupt_flag"] = True
                    voice_chunks = 0; tts_start_time = 0
            else:
                voice_chunks = max(0, voice_chunks - 2)
            ring_buffer.clear(); continue

        tts_start_time = 0
        voice_chunks   = 0
        ring_buffer.append(raw)

        # ── Sleep timeout ─────────────────────────────────────
        if state["is_awake"] and (time.time() - state["last_interaction"] > SLEEP_TIMEOUT):
            state["is_awake"] = False
            hmi({"type": "status", "awake": False, "processing": False, "speaking": False})
            UI.sleep()
            if arduino_state["connected"]:
                threading.Thread(target=lambda: _arduino_send("SLEEP"), daemon=True).start()

        # ── Voice activity ────────────────────────────────────
        if Mic_tuning.is_voice() and not state["is_speaking"]:
            try:
                doa = Mic_tuning.direction
                if not (DOA_ACCEPT_MIN <= doa <= DOA_ACCEPT_MAX):
                    ring_buffer.clear(); continue
            except Exception: pass

            consec = 1
            for _ in range(MIN_VOICE_CHUNKS - 1):
                d = stream.read(1024, exception_on_overflow=False)
                ring_buffer.append(d)
                if Mic_tuning.is_voice(): consec += 1

            required = MIN_WAKE_CHUNKS if not state["is_awake"] else MIN_VOICE_CHUNKS
            if consec < required: continue

            UI.recording()
            frames      = list(ring_buffer)
            silence     = 0
            total       = 0
            req_silence = int(CHUNKS_PER_SEC * SILENCE_DURATION)
            max_chunks  = int(CHUNKS_PER_SEC * MAX_RECORDING_DURATION)

            while silence < req_silence and total < max_chunks:
                d = stream.read(1024, exception_on_overflow=False)
                frames.append(d); total += 1
                silence = 0 if Mic_tuning.is_voice() else silence + 1

            raw_bytes   = b"".join(frames)
            samples_6ch = np.frombuffer(raw_bytes, dtype=np.int16).reshape(-1, 6)
            mono        = samples_6ch[:, 0]

            rms = np.sqrt(np.mean(mono.astype(np.float32) ** 2))
            if rms < 100:
                ring_buffer.clear(); continue

            audio_counter += 1
            filename = f"temp_{audio_counter}.wav"
            with wave.open(filename, "wb") as wf:
                wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000)
                wf.writeframes(mono.tobytes())

            if not state["is_awake"]:
                try:
                    segs, _ = stt_model.transcribe(
                        filename, beam_size=3, language="en",
                        condition_on_previous_text=False,
                        no_speech_threshold=0.4,
                        log_prob_threshold=-1.0,
                    )
                    wake_txt = "".join([s.text for s in segs]).lower().strip()
                    UI.info(f"Wake check: '{wake_txt}'")

                    exact = any(w in wake_txt for w in WAKE_WORDS)
                    fuzzy = any(
                        any(ww in word or word in ww
                            for ww in ["toyota", "dravixa", "dravix", "travis", "tejatah", "teyota", "teotah"])
                        for word in wake_txt.split()
                        if len(word) >= 4
                    )

                    if exact or fuzzy:
                        state["is_awake"]        = True
                        state["last_interaction"] = time.time()
                        drain_queue(agent_task_queue)
                        drain_queue(audio_processing_queue)
                        hmi({"type": "status", "awake": True, "processing": False, "speaking": False})
                        UI.wake()
                        if arduino_state["connected"]:
                            threading.Thread(
                                target=lambda: _arduino_send("READY"),
                                daemon=True).start()
                        tts_queue.put("Hey! I'm listening.")
                except Exception as e:
                    UI.error(f"Wake check: {e}")
                try: os.remove(filename)
                except Exception: pass
            else:
                state["last_interaction"] = time.time()
                if state["is_processing"]:
                    drain_queue(audio_processing_queue)
                audio_processing_queue.put(filename)

            ring_buffer.clear()
            time.sleep(0.1)

except KeyboardInterrupt:
    print(); UI.info("Shutting down...")
except Exception as e:
    UI.error(f"Critical: {e}")
    import traceback; traceback.print_exc()
finally:
    stream.stop_stream(); stream.close(); audio.terminate()
    if whatsapp_state["driver"]:
        try: whatsapp_state["driver"].quit()
        except Exception: pass
    audio_processing_queue.put(None)
    agent_task_queue.put(None)
    tts_queue.put(None)
    UI.info("DRAVIXA offline. Terima kasih!")
