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

# ─────────────────────────────────────────────────────────────
# GEMINI API CONFIG
# ─────────────────────────────────────────────────────────────
# Get a free key at https://aistudio.google.com/apikey
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "yourownapikey")
GEMINI_MODEL   = "gemini-3.1-flash-lite"   # fast + cheap, good for tool-calling
GEMINI_URL     = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
)

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
TTS_OUTPUT_DEVICE      = 0 # ReSpeaker 4-Mic Array output — confirmed via sd.query_devices()

GMAPS_KEY      = "AIzaSyCKh_E_tmWl_05LywLZlmx2Kjy0M_gXxi0"
OWM_API_KEY    = "af19ff126aae69998ef4b40a54c6b13d"
TOMTOM_API_KEY = "zZJXQwbJOUv5zsxeNMRXAdPY9TnrQaFD"
ORS_API_KEY    = "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImE4ODY2NDk0OTI3ZjRlNmFiODFlMmY3MjdjMzUzMzgwIiwiaCI6Im11cm11cjY0In0="
DEFAULT_CITY   = "Tangerang"

MANUAL_LOCATION = {"lat": -6.1781, "lng": 106.6300, "city": "Tangerang"}
ARDUINO_PORT    = "/dev/ttyCH341USB0"

# Separate UART line to the ESP32-S3 robot face board — physically
# different wiring from the Arduino above (Jetson pins 8/10, not USB).
# The firmware only understands two single-byte commands:
#   'W' -> warning face   'C' -> confused face
# It holds that expression for FACE_CMD_HOLD_MS (3s, firmware-side)
# then automatically reverts to the Firestore-selected idle face —
# we don't need to send anything to revert it.
FACE_BOARD_PORT = "/dev/ttyTHS1"   # Jetson GPIO UART (physical pins 8/10),
                                    # NOT a USB device. Verify with
                                    # `ls /dev/ttyTHS*` — may be ttyTHS0/1/2
                                    # depending on carrier board + JetPack.
                                    # If missing, enable via:
                                    #   sudo /opt/nvidia/jetson-io/jetson-io.py
                                    # -> Configure 40-pin header -> UART -> reboot
FACE_BOARD_BAUD = 115200

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
    # Two-phase Japanese trigger flow: when True, the main audio loop's
    # NEXT recording is transcribed with language="ja" forced (instead
    # of going through the normal auto-detect stt_worker path), since
    # this fresh clip is dedicated entirely to the actual command and
    # not mixed in with a short/ambiguous trigger phrase.
    "awaiting_ja_command": False,
}

chat_history = []
MAX_HISTORY  = 12

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
    stt_model = WhisperModel("deepdml/faster-whisper-large-v3-turbo-ct2", device="cuda", compute_type="float16")
    UI.info("STT: GPU CUDA float16")
except Exception as e:
    # IMPORTANT: must stay multilingual (NOT "base.en") so Indonesian and
    # Japanese speech are transcribed correctly instead of being forced into
    # English-only vocabulary.
    stt_model = WhisperModel("base", device="cpu", compute_type="int8")
    UI.info(f"STT: CPU int8 multilingual fallback (GPU failed: {e})")

# ─────────────────────────────────────────────────────────────
# LANGUAGE SUPPORT (STT detect -> LLM hint -> TTS voice)
# ─────────────────────────────────────────────────────────────
# Three languages Dravixa actively supports end-to-end.
# Whisper language codes: en, id, ja
SUPPORTED_LANGUAGES = {"en", "id", "ja"}
DEFAULT_LANGUAGE     = "en"

# Per-language Edge-TTS voice — used instead of a single multilingual voice
# so pronunciation/accent is natural in each language.
TTS_VOICE_BY_LANG = {
    "en": "en-US-EmmaMultilingualNeural",
    "id": "id-ID-GadisNeural",
    "ja": "ja-JP-NanamiNeural",
}
TTS_VOICE_DEFAULT = TTS_VOICE_BY_LANG[DEFAULT_LANGUAGE]

# Human-readable name for the LLM system prompt
LANGUAGE_NAME = {"en": "English", "id": "Indonesian", "ja": "Japanese"}

# Shared mutable state: last language detected from the driver's speech.
# stt_worker() updates this; llm_agent_worker() and tts_worker() read it.
lang_state = {"current": DEFAULT_LANGUAGE}

# ─────────────────────────────────────────────────────────────
# TOOL RESPONSE TRANSLATIONS
# ─────────────────────────────────────────────────────────────
# Tool functions (control_car_ac, get_directions, etc.) previously always
# returned hardcoded English text regardless of what language the driver
# was speaking — so e.g. a Japanese conversation would suddenly drop into
# an English sentence whenever a tool ran. T() looks up a template for
# the currently active language (lang_state["current"]) and falls back to
# English if a translation is missing, so adding new strings never breaks.
TOOL_STRINGS = {
    "ac_status_on":      {"en": "AC on, {temp}°C.",
                           "id": "AC menyala, {temp}°C.",
                           "ja": "エアコンはオンです、{temp}度。"},
    "ac_status_off":     {"en": "AC off.",
                           "id": "AC mati.",
                           "ja": "エアコンはオフです。"},
    "ac_no_arduino":     {"en": "AC {state}{temp_str}. (Arduino not connected)",
                           "id": "AC {state}{temp_str}. (Arduino tidak terhubung)",
                           "ja": "エアコン{state}{temp_str}。（Arduino未接続）"},
    "ac_on":             {"en": "AC on, {temp}°C.",
                           "id": "AC menyala, {temp}°C.",
                           "ja": "エアコンをつけました、{temp}度。"},
    "ac_off":            {"en": "AC turned off.",
                           "id": "AC dimatikan.",
                           "ja": "エアコンを消しました。"},
    "ac_unclear":        {"en": "AC command unclear.",
                           "id": "Perintah AC tidak jelas.",
                           "ja": "エアコンの指示がわかりませんでした。"},

    "window_no_arduino": {"en": "Window {pos}. (Arduino not connected)",
                           "id": "Jendela {pos}. (Arduino tidak terhubung)",
                           "ja": "窓{pos}。（Arduino未接続）"},
    "window_opening":    {"en": "Window opening.",
                           "id": "Jendela dibuka.",
                           "ja": "窓を開けています。"},
    "window_halfway":    {"en": "Window halfway.",
                           "id": "Jendela setengah terbuka.",
                           "ja": "窓を半分開けました。"},
    "window_closing":    {"en": "Window closing.",
                           "id": "Jendela ditutup.",
                           "ja": "窓を閉めています。"},
    "window_unclear":    {"en": "Say open, close, or half.",
                           "id": "Bilang buka, tutup, atau setengah.",
                           "ja": "「開ける」「閉める」「半分」と言ってください。"},

    "weather_unavailable": {"en": "Weather service unavailable.",
                             "id": "Layanan cuaca tidak tersedia.",
                             "ja": "天気情報を取得できませんでした。"},
    "weather_city_fail":   {"en": "Can't get weather for {city}.",
                             "id": "Tidak bisa mendapatkan cuaca untuk {city}.",
                             "ja": "{city}の天気を取得できませんでした。"},

    "directions_set":     {"en": "Navigation to {dest} set. Use Waze for turn-by-turn.",
                            "id": "Navigasi ke {dest} sudah diatur. Gunakan Waze untuk panduan arah.",
                            "ja": "{dest}までのナビを設定しました。詳細はWazeをご利用ください。"},
    "directions_not_found": {"en": "Can't find {dest}.",
                              "id": "Tidak bisa menemukan {dest}.",
                              "ja": "{dest}が見つかりませんでした。"},
    "directions_result":  {"en": "{dest} is {dist} km, about {dur} min.",
                            "id": "{dest} berjarak {dist} km, sekitar {dur} menit.",
                            "ja": "{dest}まで{dist}km、約{dur}分です。"},

    "fuel_not_found":     {"en": "No fuel stations found nearby.",
                            "id": "Tidak ada SPBU ditemukan di sekitar.",
                            "ja": "近くにガソリンスタンドが見つかりませんでした。"},

    "nearby_not_found":   {"en": "No {label} found nearby.",
                            "id": "Tidak ada {label} ditemukan di sekitar.",
                            "ja": "近くに{label}が見つかりませんでした。"},

    "emergency_sent":     {"en": "Emergency alert sent. Stay calm, help is on the way.",
                            "id": "Peringatan darurat terkirim. Tetap tenang, bantuan sedang menuju lokasi.",
                            "ja": "緊急通報を送信しました。落ち着いてください、まもなく助けが来ます。"},

    "spotify_not_connected": {"en": "Spotify not connected.",
                               "id": "Spotify tidak terhubung.",
                               "ja": "Spotifyが接続されていません。"},
    "spotify_playing_playlist": {"en": "Playing playlist: {name}.",
                                  "id": "Memutar playlist: {name}.",
                                  "ja": "プレイリスト「{name}」を再生中です。"},
    "spotify_no_playlist": {"en": "No playlist matching '{name}'.",
                             "id": "Tidak ada playlist yang cocok dengan '{name}'.",
                             "ja": "「{name}」に一致するプレイリストが見つかりませんでした。"},

    "traffic_heavy":    {"en": "Heavy traffic. Moving at {speed} km/h.",
                          "id": "Lalu lintas padat. Kecepatan {speed} km/jam.",
                          "ja": "渋滞しています。速度は時速{speed}kmです。"},
    "traffic_moderate": {"en": "Moderate traffic. Speed {speed} km/h.",
                          "id": "Lalu lintas sedang. Kecepatan {speed} km/jam.",
                          "ja": "やや混雑しています。速度は時速{speed}kmです。"},
    "traffic_good":     {"en": "Traffic flowing well at {speed} km/h.",
                          "id": "Lalu lintas lancar, kecepatan {speed} km/jam.",
                          "ja": "交通はスムーズです。時速{speed}kmです。"},
    "traffic_morning":  {"en": "Morning rush hour. Heavy traffic on Sudirman, Gatot Subroto.",
                          "id": "Jam sibuk pagi. Macet di Sudirman, Gatot Subroto.",
                          "ja": "朝のラッシュアワーです。スディルマン、ガトット・スブロト通りは渋滞しています。"},
    "traffic_evening":  {"en": "Evening rush hour. Heavy traffic on main roads.",
                          "id": "Jam sibuk sore. Macet di jalan utama.",
                          "ja": "夕方のラッシュアワーです。主要道路は渋滞しています。"},
    "traffic_light":    {"en": "Traffic is relatively light.",
                          "id": "Lalu lintas relatif lancar.",
                          "ja": "交通は比較的空いています。"},

    "flood_active":      {"en": "Flood warning active. Avoid Grogol, Kampung Melayu, Cipinang.",
                           "id": "Peringatan banjir aktif. Hindari Grogol, Kampung Melayu, Cipinang.",
                           "ja": "洪水警報が発令中です。グロゴール、カンプンムラユ、チピナンを避けてください。"},
    "flood_rain_risk":   {"en": "Heavy rain detected. Flood risk in low areas. Drive carefully.",
                           "id": "Hujan deras terdeteksi. Risiko banjir di area rendah. Hati-hati berkendara.",
                           "ja": "大雨を検知しました。低地で洪水のリスクがあります。安全運転をお願いします。"},
    "flood_none":        {"en": "No active flood alerts.",
                           "id": "Tidak ada peringatan banjir aktif.",
                           "ja": "現在、洪水警報は出ていません。"},
    "flood_unavailable": {"en": "Flood service unavailable.",
                           "id": "Layanan informasi banjir tidak tersedia.",
                           "ja": "洪水情報サービスが利用できません。"},

    "fuel_showing":      {"en": "Showing fuel stations on your map.",
                           "id": "Menampilkan SPBU di peta Anda.",
                           "ja": "地図にガソリンスタンドを表示しています。"},
    "fuel_found":        {"en": "Found {count} stations. Nearest: {names}.",
                           "id": "Ditemukan {count} SPBU. Terdekat: {names}.",
                           "ja": "{count}件のガソリンスタンドが見つかりました。最寄り：{names}。"},

    "nearby_showing":    {"en": "Showing nearby {label} on your map.",
                           "id": "Menampilkan {label} terdekat di peta Anda.",
                           "ja": "近くの{label}を地図に表示しています。"},
    "nearby_none":       {"en": "No {label} found within 2km.",
                           "id": "Tidak ada {label} ditemukan dalam radius 2km.",
                           "ja": "2km以内に{label}が見つかりませんでした。"},
    "nearby_found":      {"en": "Found {count} {label} nearby. Nearest: {names}.",
                           "id": "Ditemukan {count} {label} di sekitar. Terdekat: {names}.",
                           "ja": "近くで{count}件の{label}が見つかりました。最寄り：{names}。"},

    "emergency_police":    {"en": "Calling Police: 110",
                             "id": "Menghubungi Polisi: 110",
                             "ja": "警察に通報します：110"},
    "emergency_ambulance": {"en": "Calling Ambulance: 119",
                             "id": "Menghubungi Ambulans: 119",
                             "ja": "救急車を呼びます：119"},
    "emergency_fire":      {"en": "Calling Fire Dept: 113",
                             "id": "Menghubungi Pemadam Kebakaran: 113",
                             "ja": "消防に通報します：113"},
    "emergency_general":   {"en": "Calling Emergency: 112",
                             "id": "Menghubungi Layanan Darurat: 112",
                             "ja": "緊急通報します：112"},

    "spotify_paused":      {"en": "Music paused.",
                             "id": "Musik dijeda.",
                             "ja": "音楽を一時停止しました。"},
    "spotify_playing":     {"en": "Music playing.",
                             "id": "Musik diputar.",
                             "ja": "音楽を再生しています。"},
    "spotify_track":       {"en": "Playing {name} by {artists}.",
                             "id": "Memutar {name} oleh {artists}.",
                             "ja": "{artists}の「{name}」を再生中です。"},
    "spotify_skipped":     {"en": "Skipped to next track.",
                             "id": "Lanjut ke lagu berikutnya.",
                             "ja": "次の曲にスキップしました。"},
    "spotify_previous":    {"en": "Previous track.",
                             "id": "Lagu sebelumnya.",
                             "ja": "前の曲に戻りました。"},
    "spotify_error":       {"en": "Spotify error: {err}",
                             "id": "Terjadi kesalahan Spotify: {err}",
                             "ja": "Spotifyエラー：{err}"},

    "spotify_not_found":      {"en": "Can't find '{query}' on Spotify.",
                                "id": "Tidak bisa menemukan '{query}' di Spotify.",
                                "ja": "Spotifyで「{query}」が見つかりませんでした。"},
    "spotify_no_artist_tracks": {"en": "No tracks for {name}.",
                                  "id": "Tidak ada lagu untuk {name}.",
                                  "ja": "{name}の楽曲が見つかりませんでした。"},
    "spotify_top_tracks":      {"en": "Playing top tracks by {name}.",
                                 "id": "Memutar lagu-lagu populer dari {name}.",
                                 "ja": "{name}の人気曲を再生中です。"},
    "spotify_album":           {"en": "Playing album {name} by {artists}.",
                                 "id": "Memutar album {name} oleh {artists}.",
                                 "ja": "{artists}のアルバム「{name}」を再生中です。"},
    "spotify_playlist_track":  {"en": "Playing playlist {name}.",
                                 "id": "Memutar playlist {name}.",
                                 "ja": "プレイリスト「{name}」を再生中です。"},
    "spotify_search_error":    {"en": "Spotify search error: {err}",
                                 "id": "Terjadi kesalahan pencarian Spotify: {err}",
                                 "ja": "Spotify検索エラー：{err}"},

    "spotify_volume_set":   {"en": "Volume set to {vol}%.",
                              "id": "Volume diatur ke {vol}%.",
                              "ja": "音量を{vol}%に設定しました。"},
    "spotify_nothing_playing": {"en": "Nothing playing on Spotify.",
                                 "id": "Tidak ada yang sedang diputar di Spotify.",
                                 "ja": "Spotifyで何も再生されていません。"},
    "spotify_now_playing":  {"en": "Now playing: {name} by {artists}. {pos} of {dur}.",
                              "id": "Sedang diputar: {name} oleh {artists}. {pos} dari {dur}.",
                              "ja": "再生中：{artists}の「{name}」。{pos} / {dur}。"},
    "spotify_nothing_right_now": {"en": "Nothing playing right now.",
                                   "id": "Tidak ada yang sedang diputar saat ini.",
                                   "ja": "現在何も再生されていません。"},
    "spotify_who_sings":    {"en": "This is {name} by {artists}.",
                              "id": "Ini adalah {name} oleh {artists}.",
                              "ja": "これは{artists}の「{name}」です。"},
    "spotify_nothing_to_save": {"en": "Nothing playing to save.",
                                 "id": "Tidak ada lagu yang sedang diputar untuk disimpan.",
                                 "ja": "保存する再生中の曲がありません。"},
    "spotify_already_liked": {"en": "{name} is already in your Liked Songs.",
                               "id": "{name} sudah ada di Lagu yang Disukai Anda.",
                               "ja": "「{name}」はすでにお気に入りに登録されています。"},
    "spotify_saved":        {"en": "Saved {name} to your Liked Songs.",
                              "id": "{name} disimpan ke Lagu yang Disukai Anda.",
                              "ja": "「{name}」をお気に入りに追加しました。"},
    "spotify_nothing":      {"en": "Nothing playing.",
                              "id": "Tidak ada yang sedang diputar.",
                              "ja": "何も再生されていません。"},
    "spotify_removed":      {"en": "Removed {name} from Liked Songs.",
                              "id": "{name} dihapus dari Lagu yang Disukai Anda.",
                              "ja": "「{name}」をお気に入りから削除しました。"},
}

def T(key: str, **kwargs) -> str:
    """Look up a tool response template for the currently active language
    and format it with the given kwargs. Falls back to English if the key
    or language is missing, so a missing translation never crashes."""
    entry = TOOL_STRINGS.get(key)
    if not entry:
        return key  # unknown key — surface it rather than silently failing
    lang = lang_state.get("current", "en")
    template = entry.get(lang) or entry.get("en", "")
    try:
        return template.format(**kwargs)
    except Exception:
        return template

# Minimum Whisper language-ID confidence required before we trust its guess
# enough to switch languages. Below this, keep using whatever language was
# active last turn instead of flip-flopping on short/ambiguous audio.
LANG_CONFIDENCE_THRESHOLD = 0.6

# ── Hard trigger words — force a language switch regardless of Whisper's
# detected language or confidence. Useful because short Japanese phrases
# are the hardest case for Whisper's language ID; saying one of these
# words anywhere in the utterance locks Dravixa into Japanese immediately,
# instead of waiting for confidence-based detection to catch up.
FORCE_LANGUAGE_TRIGGERS = {
    "ja": ["sumimasen", "konnichiwa", "konbanwa", "ohayou", "ohayo",
           "arigatou", "arigato", "moshi moshi", "hajimemashite",
           # Kana spellings — in case Whisper transcribes the trigger
           # phrase directly into Japanese script instead of romaji.
           "すみません", "こんにちは", "こんにちわ", "こんばんは",
           "おはよう", "ありがとう", "もしもし", "はじめまして"],
    "id": ["halo", "selamat pagi", "selamat siang", "selamat malam",
           "terima kasih", "permisi"],
    "en": ["hello there", "good morning", "good evening"],
}

def _check_force_language(text: str):
    """Return a language code if the utterance contains a hard trigger
    word for that language, else None. Checked before confidence-based
    detection so these words always win."""
    t = text.lower()
    for lang_code, triggers in FORCE_LANGUAGE_TRIGGERS.items():
        for trig in triggers:
            if trig in t:
                return lang_code
    return None

import re as _re_lang

def _sanity_check_language(text: str, whisper_lang: str) -> tuple:
    """Cross-check Whisper's language guess against simple text heuristics,
    since short utterances are unreliable for language ID.

    Returns (language_code, strong_signal: bool). strong_signal is True
    when kana/kanji was found directly, or when word-marker hits clearly
    favor one language — these cases are trusted even if Whisper's own
    confidence score was low, since a clearly-formed sentence with
    multiple matching function words is a reliable signal on its own."""
    # Japanese: any hiragana/katakana/kanji character is unambiguous
    if _re_lang.search(r'[\u3040-\u30ff\u4e00-\u9fff]', text):
        return "ja", True

    t = text.lower()
    words = set(_re_lang.findall(r"[a-z']+", t))

    id_markers = {"yang", "saya", "kamu", "bisa", "apa", "ini", "itu",
                  "dan", "tidak", "ada", "aku", "kita", "nya", "ya",
                  "putar", "buka", "tutup", "nyalakan", "matikan",
                  "tolong", "mau", "lagu", "musik", "tunjukkan"}
    en_markers = {"the", "is", "are", "you", "can", "what", "this", "that",
                  "and", "i'm", "im", "hey", "please", "do", "does", "will",
                  # Common voice-command verbs/nouns — short imperative
                  # phrases like "play X" or "turn on Y" often contain no
                  # function words at all, so without these the sanity
                  # check has nothing to grab onto and silently defers
                  # back to Whisper's own (sometimes wrong) guess.
                  "play", "stop", "pause", "resume", "skip", "next",
                  "previous", "turn", "open", "close", "show", "find",
                  "search", "set", "tell", "give", "song", "music",
                  "volume", "louder", "quieter", "window", "navigate",
                  "directions", "weather", "traffic"}
    # Common romaji (Latin-spelled) Japanese words — Whisper sometimes
    # transcribes spoken Japanese as romaji text instead of kana/kanji,
    # so the script-detection above misses it entirely.
    ja_markers = {"wa", "wo", "desu", "ka", "kudasai", "arigatou",
                  "konnichiwa", "ohayou", "genki", "ongaku", "kaketai",
                  "hai", "sumimasen", "onegaishimasu", "ima", "kyou",
                  "eakon", "tsukete", "mado", "akete", "tenki", "doko",
                  "chikai", "ichiban", "gozaimasu", "desuka"}

    id_hits = len(words & id_markers)
    en_hits = len(words & en_markers)
    ja_hits = len(words & ja_markers)

    best = max(id_hits, en_hits, ja_hits)
    if best == 0:
        # No marker words matched at all — this is exactly the case that
        # silently broke on short commands like "play blinding lights"
        # (Whisper misjudged it as Indonesian at 94% confidence with
        # nothing here to catch it). As a last-resort signal, Indonesian
        # very reliably uses prefixes/suffixes (me-, di-, ter-, ber-,
        # -kan, -nya) that essentially never appear in English text, and
        # Japanese romaji leans on long vowel doubling / specific endings
        # rare in English. If the text looks like plain English spelling
        # with none of those patterns, prefer "en" over a low-context
        # Whisper guess — short commands are far more often English or
        # Indonesian than anything else, and Indonesian morphology is
        # distinctive enough to not need this fallback.
        if not _re_lang.search(r"\b(me|di|ter|ber|peng|pen)[a-z]{3,}|[a-z]+(kan|nya|lah)\b", t):
            return "en", False
        return whisper_lang, False

    # 2+ matching marker words is a strong, reliable signal — e.g. "who IS
    # THE president of Indonesia THIS..." style English sentences hit
    # several en_markers at once, which is meaningful regardless of
    # Whisper's own (sometimes low) confidence score on accented audio.
    strong = best >= 2

    if ja_hits == best:
        return "ja", strong
    elif en_hits == best:
        return "en", strong
    elif id_hits == best:
        return "id", strong
    return whisper_lang, False

UI.info("Loading Kokoro TTS with CUDA...")
#try:
#    _tts_sess = _ort.InferenceSession(
#        "kokoro-v1.0.onnx",
#        providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
#    tts_model = Kokoro.from_session(_tts_sess, "voices-v1.0.bin")
#    UI.info("TTS: GPU CUDAExecutionProvider")
#except Exception as e:
    #UI.error(f"TTS load failed: {e}"); sys.exit(1)

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
    if not _sp: return T("spotify_not_connected")
    _sp_refresh()  # ← add this
    try:
        pb = _sp.current_playback()
        if action == "pause" or (action == "toggle" and pb and pb["is_playing"]):
            _sp.pause_playback(); return T("spotify_paused")
        _sp.start_playback(); return T("spotify_playing")
    except Exception as e: return T("spotify_error", err=e)
def spotify_next_track():
    if not _sp: return T("spotify_not_connected")
    _sp_refresh()  # ← add this
    try:
        _sp.next_track()
        time.sleep(0.8)
        pb = _sp.current_playback()
        if pb and pb.get("item"):
            name    = pb["item"]["name"]
            artists = ", ".join(a["name"] for a in pb["item"]["artists"])
            return T("spotify_track", name=name, artists=artists)
        return T("spotify_skipped")
    except Exception as e: return T("spotify_error", err=e)

def spotify_previous_track():
    if not _sp: return T("spotify_not_connected")
    try:
        _sp.previous_track()
        time.sleep(0.5)
        _sp.previous_track()  # call twice to skip to actual previous
        time.sleep(0.8)
        pb = _sp.current_playback()
        if pb and pb.get("item"):
            name    = pb["item"]["name"]
            artists = ", ".join(a["name"] for a in pb["item"]["artists"])
            return T("spotify_track", name=name, artists=artists)
        return T("spotify_previous")
    except Exception as e: return T("spotify_error", err=e)

def spotify_play_playlist(name):
    if not _sp: return T("spotify_not_connected")
    try:
        device_id = None
        try:
            for d in _sp.devices().get("devices", []):
                if "dravixa" in d["name"].lower():
                    device_id = d["id"]
                    break
        except Exception:
            pass

        for pl in _sp.current_user_playlists(limit=50)["items"]:
            if name.lower() in pl["name"].lower():
                _sp.start_playback(device_id=device_id, context_uri=pl["uri"])
                hmi({"type": "spotify", "track": pl["name"], "artist": "Playlist"})
                return T("spotify_playing_playlist", name=pl['name'])
        return T("spotify_no_playlist", name=name)
    except Exception as e: return T("spotify_error", err=e)

def spotify_set_volume(vol):
    if not _sp: return T("spotify_not_connected")
    try:
        _sp.volume(max(0, min(100, int(vol))))
        return T("spotify_volume_set", vol=vol)
    except Exception as e: return T("spotify_error", err=e)

# ── Search and play ───────────────────────────────────────────
def spotify_search_play(query, search_type="track"):
    sp = _get_sp()
    if not _sp: return T("spotify_not_connected")
    _sp_refresh()  # ← add this
    try:
        results = _sp.search(q=query, type=search_type, limit=1)
        items   = results.get(f"{search_type}s", {}).get("items", [])
        if not items: return T("spotify_not_found", query=query)
        item = items[0]
        if search_type == "track":
            _sp.start_playback(uris=[item["uri"]])
            artists = ", ".join(a["name"] for a in item["artists"])
            hmi({"type": "spotify", "track": item["name"], "artist": artists})
            return T("spotify_track", name=item['name'], artists=artists)
        elif search_type == "artist":
            top  = _sp.artist_top_tracks(item["id"])["tracks"]
            if not top: return T("spotify_no_artist_tracks", name=item['name'])
            _sp.start_playback(uris=[t["uri"] for t in top[:10]])
            hmi({"type": "spotify", "track": top[0]["name"], "artist": item["name"]})
            return T("spotify_top_tracks", name=item['name'])
        elif search_type == "album":
            _sp.start_playback(context_uri=item["uri"])
            artists = ", ".join(a["name"] for a in item["artists"])
            hmi({"type": "spotify", "track": item["name"], "artist": artists})
            return T("spotify_album", name=item['name'], artists=artists)
        elif search_type == "playlist":
            _sp.start_playback(context_uri=item["uri"])
            hmi({"type": "spotify", "track": item["name"], "artist": "Playlist"})
            return T("spotify_playlist_track", name=item['name'])
    except Exception as e: return T("spotify_search_error", err=e)

# ── Genre / mood ──────────────────────────────────────────────
def spotify_play_genre(genre):
    """Play music for a mood/genre request.

    Spotify deprecated the /v1/recommendations endpoint for all apps
    created after Nov 2024 — it now returns 404 no matter what seeds
    are passed. Instead of trying to match the genre to real Spotify
    catalog data, we just play the driver's own "test" playlist for
    any genre/mood request. Simple, reliable, no dead API calls.
    """
    if not _sp: return T("spotify_not_connected")
    return spotify_play_playlist("test")

# ── Now playing ───────────────────────────────────────────────
def spotify_now_playing():
    if not _sp: return T("spotify_not_connected")
    try:
        pb = _sp.current_playback()
        if not pb or not pb.get("item"):
            return T("spotify_nothing_playing")
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
        pos = f"{pos_s // 60}:{pos_s % 60:02d}"
        dur = f"{dur_s // 60}:{dur_s % 60:02d}"
        return T("spotify_now_playing", name=item['name'], artists=artists, pos=pos, dur=dur)
    except Exception as e: return T("spotify_error", err=e)

def spotify_who_sings():
    if not _sp: return T("spotify_not_connected")
    try:
        pb = _sp.current_playback()
        if not pb or not pb.get("item"): return T("spotify_nothing_right_now")
        item    = pb["item"]
        artists = ", ".join(a["name"] for a in item["artists"])
        return T("spotify_who_sings", name=item['name'], artists=artists)
    except Exception as e: return T("spotify_error", err=e)

# ── Like / save ───────────────────────────────────────────────
def spotify_like_song():
    if not _sp: return T("spotify_not_connected")
    try:
        pb = _sp.current_playback()
        if not pb or not pb.get("item"): return T("spotify_nothing_to_save")
        track_id = pb["item"]["id"]
        name     = pb["item"]["name"]
        saved    = _sp.current_user_saved_tracks_contains([track_id])
        if saved and saved[0]: return T("spotify_already_liked", name=name)
        _sp.current_user_saved_tracks_add([track_id])
        return T("spotify_saved", name=name)
    except Exception as e: return T("spotify_error", err=e)

def spotify_unlike_song():
    if not _sp: return T("spotify_not_connected")
    try:
        pb = _sp.current_playback()
        if not pb or not pb.get("item"): return T("spotify_nothing")
        track_id = pb["item"]["id"]
        name     = pb["item"]["name"]
        _sp.current_user_saved_tracks_delete([track_id])
        return T("spotify_removed", name=name)
    except Exception as e: return T("spotify_error", err=e)

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
        owm_lang = {"en": "en", "id": "id", "ja": "ja"}.get(lang_state.get("current", "en"), "en")
        r = requests.get("https://api.openweathermap.org/data/2.5/weather",
            params={"q": city, "appid": OWM_API_KEY, "units": "metric", "lang": owm_lang}, timeout=5)
        if r.status_code != 200: return T("weather_city_fail", city=city)
        d = r.json()
        return (f"{d['name']}: {round(d['main']['temp'])}°C, "
                f"{d['weather'][0]['description']}, "
                f"humidity {d['main']['humidity']}%, wind {round(d['wind']['speed'])} m/s")
    except Exception: return T("weather_unavailable")

def get_traffic(origin=None, destination=None):
    lat, lng = state["driver_location"]["lat"], state["driver_location"]["lng"]
    try:
        r = requests.get("https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json",
            params={"key": TOMTOM_API_KEY, "point": f"{lat},{lng}"}, timeout=5)
        if r.status_code != 200: raise Exception()
        d     = r.json()["flowSegmentData"]
        ratio = d["currentSpeed"] / d["freeFlowSpeed"] if d["freeFlowSpeed"] > 0 else 1.0
        if ratio < 0.4: return T("traffic_heavy", speed=d['currentSpeed'])
        if ratio < 0.7: return T("traffic_moderate", speed=d['currentSpeed'])
        return T("traffic_good", speed=d['currentSpeed'])
    except Exception:
        hour = datetime.now().hour
        if 7 <= hour <= 10:  return T("traffic_morning")
        if 16 <= hour <= 20: return T("traffic_evening")
        return T("traffic_light")

def get_directions(destination):
    lat, lng = state["driver_location"]["lat"], state["driver_location"]["lng"]
    try:
        geo = requests.get("https://nominatim.openstreetmap.org/search",
            params={"q": f"{destination}, Jakarta, Indonesia", "format": "json", "limit": 1},
            headers={"User-Agent": "Dravixa/2.5"}, timeout=5).json()
        if not geo: return T("directions_not_found", dest=destination)
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
        return T("directions_result", dest=destination, dist=dist_km, dur=dur_min)
    except Exception:
        state["current_dest"] = destination
        hmi({"type": "navigate", "destination": destination,
             "origin_lat": lat, "origin_lng": lng,
             "distance_km": "?", "duration_min": "?"})
        return T("directions_set", dest=destination)

def get_flood_alert():
    try:
        r = requests.get("https://peringatandini.bmkg.go.id/api/notification", timeout=5)
        if r.status_code == 200 and r.json():
            return T("flood_active")
        w = get_weather(state["driver_location"].get("city", DEFAULT_CITY))
        if "rain" in w.lower() or "thunderstorm" in w.lower():
            return T("flood_rain_risk")
        return T("flood_none")
    except Exception: return T("flood_unavailable")

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
    if error or not places: return T("fuel_showing")
    names = [p["name"] for p in places[:2]]
    return T("fuel_found", count=len(places), names=", ".join(names))

def find_nearby(place_type, label):
    lat, lng = state["driver_location"]["lat"], state["driver_location"]["lng"]
    places, error = _search_nearby_google(place_type, lat, lng)
    if error:
        UI.info(f"Places API error: {error}")
        hmi({"type": "navigate", "places": place_type,
             "origin_lat": lat, "origin_lng": lng, "markers": []})
        return T("nearby_showing", label=label)
    if not places:
        hmi({"type": "navigate", "places": place_type,
             "origin_lat": lat, "origin_lng": lng, "markers": []})
        return T("nearby_none", label=label)
    hmi({"type": "navigate", "places": place_type,
         "origin_lat": lat, "origin_lng": lng, "markers": places})
    names = [p["name"] for p in places[:2]]
    return T("nearby_found", count=len(places), label=label, names=", ".join(names))

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
    return T("directions_result", dest=name, dist=dist_km, dur=dur_min)

def emergency_sos(type="general"):
    return {
        "police":    T("emergency_police"),
        "ambulance": T("emergency_ambulance"),
        "fire":      T("emergency_fire"),
        "general":   T("emergency_general"),
    }.get(type, T("emergency_general"))

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

# ─────────────────────────────────────────────────────────────
# ESP32-S3 ROBOT FACE BOARD — separate UART, separate protocol
# ─────────────────────────────────────────────────────────────
# This is NOT the same connection as arduino_state above. The face
# board only accepts single-byte commands ('W' or 'C') and sends no
# reply, so init/send here are deliberately simpler than the Arduino
# pair (no startup-line read, no response wait).
face_board_state = {"ser": None, "connected": False}

def init_face_board(port=None, baud=None):
    if not _serial_ok: return False
    port = port or FACE_BOARD_PORT
    baud = baud or FACE_BOARD_BAUD
    try:
        ser = serial.Serial(port, baud, timeout=1, rtscts=False, dsrdtr=False)
        time.sleep(1)
        ser.flushInput()
        face_board_state["ser"]       = ser
        face_board_state["connected"] = True
        UI.info(f"Face board: connected on {port}")
        return True
    except Exception as e:
        UI.info(f"Face board: failed ({e})")
        return False

def send_face_command(cmd: str) -> bool:
    """Send a single-byte face command to the ESP32 board.
    cmd must be 'W' (warning) or 'C' (confused) — the firmware ignores
    anything else. The board auto-reverts to the idle/Firestore face
    after its own internal hold timer, so there's no "clear" command
    to send from this side."""
    if cmd not in ("W", "C"):
        UI.info(f"Face board: ignoring invalid command '{cmd}'")
        return False
    if not face_board_state["connected"]:
        return False
    try:
        face_board_state["ser"].write(f"{cmd}\n".encode())
        face_board_state["ser"].flush()
        UI.info(f"Face board << {cmd}")
        return True
    except Exception as e:
        UI.info(f"Face board: send failed ({e})")
        face_board_state["connected"] = False
        return False

def control_car_ac(action, temperature=None):
    if action == "status":
        if arduino_state['ac_on']:
            return T("ac_status_on", temp=arduino_state['ac_temp'])
        return T("ac_status_off")
    if not arduino_state["connected"]:
        ac_state = "on" if action == "on" else "off"
        temp_str = f", {arduino_state['ac_temp']}°C" if action == "on" else ""
        return T("ac_no_arduino", state=ac_state, temp_str=temp_str)
    if action == "on":
        _arduino_send("ON")
        arduino_state["ac_on"] = True
        if temperature:
            arduino_state["ac_temp"] = max(16, min(30, int(temperature)))
        hmi({"type": "vehicle", "ac_on": True, "temp": arduino_state["ac_temp"]})
        return T("ac_on", temp=arduino_state['ac_temp'])
    elif action == "off":
        _arduino_send("OFF")
        arduino_state["ac_on"] = False
        hmi({"type": "vehicle", "ac_on": False, "temp": arduino_state["ac_temp"]})
        return T("ac_off")
    return T("ac_unclear")

def control_car_window(position):
    if not arduino_state["connected"]:
        return T("window_no_arduino", pos=position)
    pos = position.lower().strip()
    if any(k in pos for k in ["open", "buka", "down", "turun"]):
        _arduino_send("F")
        arduino_state["window_angle"] = 180
        return T("window_opening")
    elif any(k in pos for k in ["half", "halfway", "setengah"]):
        _arduino_send("H")
        arduino_state["window_angle"] = 90
        return T("window_halfway")
    elif any(k in pos for k in ["close", "shut", "up", "tutup", "naik"]):
        _arduino_send("B")
        arduino_state["window_angle"] = 0
        return T("window_closing")
    else:
        return T("window_unclear")

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
# GEMINI TOOL SCHEMA CONVERTER
# Ollama/OpenAI format -> Gemini functionDeclarations format
# ─────────────────────────────────────────────────────────────
def _to_gemini_schema(props: dict) -> dict:
    """Recursively strip unsupported keys and uppercase JSON-schema types
    the way Gemini's function-calling schema expects."""
    if not isinstance(props, dict):
        return props
    out = {}
    for k, v in props.items():
        if k == "type" and isinstance(v, str):
            out[k] = v.upper()
        elif k == "properties" and isinstance(v, dict):
            out[k] = {pk: _to_gemini_schema(pv) for pk, pv in v.items()}
        elif k == "items" and isinstance(v, dict):
            out[k] = _to_gemini_schema(v)
        else:
            out[k] = v
    return out

def _build_gemini_tools(openai_tools: list) -> list:
    decls = []
    for t in openai_tools:
        fn = t.get("function", {})
        params = fn.get("parameters", {"type": "object", "properties": {}})
        decls.append({
            "name":        fn.get("name"),
            "description": fn.get("description", ""),
            "parameters":  _to_gemini_schema(params),
        })
    return [{"functionDeclarations": decls}]

gemini_tools_schema = _build_gemini_tools(tools_schema)

def _history_to_gemini_contents(system_prompt: str, history: list) -> list:
    """Convert our Ollama-style chat_history (role: user/assistant/tool)
    into Gemini's `contents` array (role: user/model, parts: [...])."""
    contents = []
    for msg in history:
        role = msg.get("role")
        if role == "user":
            contents.append({"role": "user", "parts": [{"text": msg.get("content", "")}]})
        elif role == "assistant":
            # Plain text assistant turn (no tool call)
            txt = msg.get("content", "")
            if txt:
                contents.append({"role": "model", "parts": [{"text": txt}]})
            elif msg.get("tool_calls"):
                # Re-express a prior Gemini function call turn
                parts = []
                for tc in msg["tool_calls"]:
                    fn = tc.get("function", {})
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        try: args = json.loads(args)
                        except Exception: args = {}
                    parts.append({"functionCall": {"name": fn.get("name"), "args": args}})
                contents.append({"role": "model", "parts": parts})
        elif role == "tool":
            # Gemini expects functionResponse parts, keyed by the function name.
            contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "name": msg.get("name", "tool_result"),
                        "response": {"result": msg.get("content", "")},
                    }
                }],
            })
    return contents

def call_gemini(system_prompt: str, history: list, tools: list, max_tokens: int = 60):
    """Call Gemini generateContent endpoint, return a dict shaped like
    Ollama's `message` ({"content": str, "tool_calls": [...]})."""
    contents = _history_to_gemini_contents(system_prompt, history)
    payload = {
        "contents": contents,
        "tools": tools,
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": {
            "temperature":     0.2,
            "maxOutputTokens": max_tokens,
            "topK":            10,
            "topP":            0.7,
        },
    }
    resp = requests.post(GEMINI_URL, json=payload, timeout=20)
    if resp.status_code != 200:
        UI.error(f"Gemini {resp.status_code} detail: {resp.text}")
        roles = [c.get("role") for c in contents]
        UI.error(f"Gemini contents role sequence: {roles}")
    resp.raise_for_status()
    data = resp.json()

    candidates = data.get("candidates", [])
    if not candidates:
        return {"content": "", "tool_calls": []}

    parts = candidates[0].get("content", {}).get("parts", [])
    text_out  = ""
    tool_calls = []
    for p in parts:
        if "text" in p:
            text_out += p["text"]
        elif "functionCall" in p:
            fc = p["functionCall"]
            tool_calls.append({
                "function": {
                    "name":      fc.get("name"),
                    "arguments": fc.get("args", {}),
                }
            })

    return {"content": text_out.strip(), "tool_calls": tool_calls}

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

        # Mirror the relevant emotes onto the robot's AMOLED face board.
        # The face firmware only understands two commands ('W'=warning,
        # 'C'=confused), so only those two _auto_emote() categories map
        # across — NOD/READY/DANCE have no face-board equivalent and are
        # left to the idle/Firestore-selected expression instead.
        if emote_cmd and face_board_state["connected"]:
            face_cmd = {"CONFUSED": "C", "SHAKE": "W"}.get(emote_cmd)
            if face_cmd:
                threading.Thread(
                    target=lambda c=face_cmd: send_face_command(c),
                    daemon=True
                ).start()

        try:
            t0 = time.perf_counter()
            import asyncio
            import edge_tts
            import soundfile as sf
            import sounddevice as sd
            import os

            # Pick the voice matching the language Whisper detected for the
            # driver's last utterance, so pronunciation/accent is natural
            # instead of relying on one multilingual voice to guess.
            tts_lang  = lang_state["current"]
            tts_voice = TTS_VOICE_BY_LANG.get(tts_lang, TTS_VOICE_DEFAULT)

            # 1. Generate the audio in the matching language with Edge-TTS
            async def generate_audio(voice):
                communicate = edge_tts.Communicate(
                    text,
                    voice,
                    rate="+10%"  # Speeds it up slightly for faster response
                )
                await communicate.save("temp_tts.wav")

            try:
                asyncio.run(generate_audio(tts_voice))
            except Exception as voice_err:
                # Language-specific voice failed (e.g. transient network issue) —
                # retry once with the default English voice so Dravixa still speaks.
                UI.error(f"TTS voice '{tts_voice}' failed ({voice_err}), retrying with default")
                asyncio.run(generate_audio(TTS_VOICE_DEFAULT))

            # 2. Read the downloaded file
            data, fs = sf.read("temp_tts.wav")
            latency["tts"] = (time.perf_counter() - t0) * 1000

            # ReSpeaker only supports 16000Hz — resample Edge-TTS output
            # (usually 24000Hz) or PortAudio throws paInvalidSampleRate.
            TARGET_SR = 16000
            if fs != TARGET_SR:
                import scipy.signal as _sig
                num_samples = int(len(data) * TARGET_SR / fs)
                if data.ndim > 1:
                    data = _sig.resample(data, num_samples, axis=0)
                else:
                    data = _sig.resample(data, num_samples)
                fs = TARGET_SR

            # 3. Play the audio using your existing sounddevice setup
            # blocksize set higher than sounddevice's default to reduce
            # ALSA "underrun occurred" warnings during playback.
            try:
                dev_info = sd.query_devices(TTS_OUTPUT_DEVICE, "output")
                max_ch   = dev_info["max_output_channels"]
                if max_ch < 2:
                    # Mono output
                    if data.ndim > 1: data = data[:, 0]
                    sd.play(data, fs, device=TTS_OUTPUT_DEVICE, blocksize=2048)
                else:
                    # Stereo output
                    if data.ndim == 1: data = np.column_stack([data, data])
                    sd.play(data, fs, device=TTS_OUTPUT_DEVICE, blocksize=2048)
            except Exception:
                sd.play(data, fs, blocksize=2048)

            # 4. Handle Barge-in (Stop talking if you interrupt)
            dur = len(data) / fs
            t_start = time.time()
            while time.time() - t_start < dur:
                if state["interrupt_flag"]: 
                    sd.stop()
                    break
                time.sleep(0.05)
            else:
                sd.wait()

            time.sleep(0.15)
            UI.latency(latency["stt"], latency["llm"], latency["tts"])

            # 5. Clean up the temp file
            try:
                os.remove("temp_tts.wav")
            except Exception:
                pass

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
            segments, info = stt_model.transcribe(
                audio_file, beam_size=5, condition_on_previous_text=False)
            transcription = "".join([s.text for s in segments]).strip()

            # Detected language for this utterance. Whisper's language ID
            # is unreliable on short/ambiguous audio, so we layer checks
            # before committing to a language switch:
            #   1. Hard trigger words (e.g. "sumimasen") always win, and
            #      put us into a two-phase flow (see below) instead of
            #      trying to fix this same recording.
            #   2. Otherwise, cross-check Whisper's guess against simple
            #      word-list heuristics (_sanity_check_language).
            #   3. Only apply the result if Whisper's own confidence
            #      clears LANG_CONFIDENCE_THRESHOLD, so a single garbled
            #      low-confidence guess can't flip the language mid-chat.
            detected      = getattr(info, "language", None)
            detected_prob = getattr(info, "language_probability", 0)

            forced = _check_force_language(transcription) if transcription else None
            if forced == "ja":
                # Two-phase flow: don't try to salvage THIS recording (it's
                # the trigger-word phrase, often short/ambiguous and prone
                # to hallucination if we force-retranscribe it — tested and
                # confirmed worse). Instead: acknowledge in Japanese, set a
                # flag so the main audio loop records a brand NEW clean
                # clip dedicated to the actual command, and transcribes
                # THAT one with language="ja" forced from the start.
                lang_state["current"] = "ja"
                state["awaiting_ja_command"] = True
                UI.info("Detected language: ja (forced by trigger word) — "
                         "switching to two-phase listen for next command")
                tts_queue.put("はい、どうぞ。")
                latency["stt"] = (time.perf_counter() - t0) * 1000
                continue  # skip sending this garbled phrase to the LLM

            elif detected in SUPPORTED_LANGUAGES:
                sanity_lang, strong_signal = _sanity_check_language(transcription, detected)
                detected = sanity_lang
                UI.info(f"Detected language: {detected} (whisper conf: {detected_prob:.2f}"
                         f"{', strong word-match' if strong_signal else ''})")
                # A strong, unambiguous word-marker match (e.g. a clearly
                # formed English sentence hitting 2+ markers) is trusted
                # even when Whisper's own confidence score is low — that
                # score degrades on accented or fast speech even for
                # perfectly clear sentences, so it shouldn't be the only
                # gate keeping the TTS voice stuck on the wrong language.
                if strong_signal or detected_prob >= LANG_CONFIDENCE_THRESHOLD:
                    lang_state["current"] = detected

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
    def build_system_prompt(lang_code: str) -> str:
        lang_name = LANGUAGE_NAME.get(lang_code, "English")
        return (
            "You are Dravixa, a friendly Toyota car assistant for Jakarta, Indonesia. "
            "Personality: Chill, casual. Use: 'Yeah', 'Sure', 'Got it', 'No problem' "
            "(or their natural equivalent in the reply language). "
            "RULES: "
            "1. ONE sentence max 15 words. "
            f"2. LANGUAGE: The driver's last message was detected as {lang_name}. "
            "Reply in that SAME language — English, Indonesian, or Japanese ONLY "
            "(these are the 3 languages this car's speech engine supports). "
            "If you are unsure, default to English. "
            "3. If the user asks a general question or wants to chat, answer directly and naturally. "
            "4. If a request requires an action, ALWAYS call the matching tool instead of just answering. "
            "5. Gas/fuel/bensin/SPBU/gasorin -> get_fuel_stations. "
            "6. Grocery/supermarket/indomaret -> find_nearby_grocery. "
            "7. Restaurant/makan/hungry/gohan/onaka suita -> find_nearby_restaurant. "
            "8. ATM/cash -> find_nearby_atm. "
            "9. Pharmacy/apotek/kusuri -> find_nearby_pharmacy. "
            "10. Parking/parkir/chuusha -> find_nearby_parking. "
            "11. AC on/off/temp/eakon/tsukete/kesite -> control_car_ac MANDATORY. "
            "12. Window/mado/akete/shimete -> control_car_window MANDATORY. "
            "13. Emergency/kinkyuu -> emergency_sos IMMEDIATELY. "
            "14. Navigate/drive to/ittekudasai -> get_directions. "
            "15. Play [specific song]/kyoku wo kakete -> spotify_search_play. "
            "16. Play [genre] music / generic 'play music' / ongaku wo kaketai -> spotify_play_genre. "
            "17. What song / who sings -> spotify_now_playing / spotify_who_sings. "
            "18. Like/save this song -> spotify_like_song. "
            "19. Driver is driving. Keep all general knowledge answers extremely brief."
        )

    if "PASTE_YOUR_GEMINI_API_KEY_HERE" in GEMINI_API_KEY:
        UI.error("Gemini API key not set — edit GEMINI_API_KEY at the top of this file "
                  "or export GEMINI_API_KEY=... before running.")

    while True:
        user_text = agent_task_queue.get()
        if user_text is None: break
        state["is_processing"] = True
        chat_history.append({"role": "user", "content": user_text})
        # Trim from the front only at safe boundaries — never leave a
        # "tool" response or an orphaned assistant functionCall as the
        # new first entry, since Gemini rejects a contents array that
        # doesn't open cleanly on a user turn (or a functionResponse
        # immediately following its functionCall).
        while len(chat_history) > MAX_HISTORY:
            if chat_history[0].get("role") == "user":
                chat_history.pop(0)
                # After removing the user turn, the new front might be
                # an assistant turn that issued a tool call (role
                # "assistant" with "tool_calls"), its paired "tool"
                # response, or both in sequence — drop any leading run
                # of these so history never starts mid-pair.
                while chat_history and (
                    chat_history[0].get("role") == "tool"
                    or (chat_history[0].get("role") == "assistant"
                        and chat_history[0].get("tool_calls"))
                ):
                    chat_history.pop(0)
            else:
                break

        # Build the system prompt fresh each turn using the language Whisper
        # just detected for this utterance (falls back to last known language).
        system_prompt = build_system_prompt(lang_state["current"])

        # High confidence — skip LLM entirely (same fast path as local version)
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

        try:
            t0 = time.perf_counter()
            message = call_gemini(system_prompt, chat_history, gemini_tools_schema, max_tokens=60)
            latency["llm"] = (time.perf_counter() - t0) * 1000

            tool_done = False
            direct    = False

            if message.get("tool_calls"):
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
                    # Record the function call + its result for follow-up context.
                    # Note: we do NOT also append a plain {"role":"assistant",
                    # "content":result} here — that would create two model
                    # turns in a row (functionCall, then plain text) with no
                    # user/functionResponse turn between them, which Gemini
                    # rejects on the *next* request with a 400 error:
                    # "function call turn comes immediately after a user turn
                    # or after a function response turn."
                    chat_history.append({"role": "assistant", "tool_calls": [tc]})
                    chat_history.append({"role": "tool", "name": name, "content": result})
                    if name in SPEAK_DIRECT or name == "read_whatsapp_messages":
                        tts_queue.put(result)
                        direct = True
                    else:
                        t1 = time.perf_counter()
                        try:
                            message = call_gemini(
                                system_prompt, chat_history, gemini_tools_schema, max_tokens=50
                            )
                            latency["llm"] += (time.perf_counter() - t1) * 1000
                        except Exception:
                            pass

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
            UI.error("Gemini timeout")
            kb = _keyword_fallback(user_text)
            if kb and kb[0] != "math_result":
                name, args = kb
                result = TOOL_MAP[name](args)
                tts_queue.put(result)
            else:
                tts_queue.put("Brain's slow. Try again?")
        except requests.exceptions.ConnectionError:
            UI.error("Gemini unreachable (check internet)")
            tts_queue.put("Can't reach my brain — no internet?")
        except requests.exceptions.HTTPError as e:
            UI.error(f"Gemini HTTP error: {e}")
            tts_queue.put("Brain hiccup. Say again?")
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
                    if face_board_state["connected"]:
                        threading.Thread(
                            target=lambda: send_face_command("W"),
                            daemon=True
                        ).start()
                    hmi({"type": "alert", "alert_level": "CRITICAL", "message": message})

                elif level == "WARNING" and now - _last_trigger["WARNING"] > 180:
                    _last_trigger["WARNING"] = now
                    tts_queue.put(message)
                    if face_board_state["connected"]:
                        threading.Thread(
                            target=lambda: send_face_command("W"),
                            daemon=True
                        ).start()
                    hmi({"type": "alert", "alert_level": "WARNING", "message": message})

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
init_face_board(FACE_BOARD_PORT)

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
                        filename, beam_size=3,
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
                        # Clear any pre-roll audio left over from the wake
                        # phrase itself ("...oyota" tail etc.) so it can't
                        # bleed into the start of the next recording and
                        # skew language detection on the first real command.
                        ring_buffer.clear()
                        hmi({"type": "status", "awake": True, "processing": False, "speaking": False})
                        UI.wake()
                        if arduino_state["connected"]:
                            threading.Thread(
                                target=lambda: _arduino_send("READY"),
                                daemon=True).start()
                        # Always greet in English regardless of which language
                        # was active in the previous conversation — the next
                        # thing the driver says will switch it normally.
                        lang_state["current"] = "en"
                        tts_queue.put("Hey! I'm listening.")
                except Exception as e:
                    UI.error(f"Wake check: {e}")
                try: os.remove(filename)
                except Exception: pass
            else:
                state["last_interaction"] = time.time()

                if state["awaiting_ja_command"]:
                    # Phase 2 of the Japanese trigger flow: this is a fresh,
                    # dedicated recording of the actual command (the driver
                    # spoke again after Dravixa's "はい、どうぞ" prompt), so
                    # it's safe to force language="ja" here — unlike trying
                    # to force it on the short/ambiguous trigger-word clip,
                    # this clip has nothing else mixed into it.
                    state["awaiting_ja_command"] = False
                    try:
                        segs_ja, _ = stt_model.transcribe(
                            filename, beam_size=5,
                            condition_on_previous_text=False,
                            language="ja",
                        )
                        ja_text = "".join([s.text for s in segs_ja]).strip()
                        UI.info(f"Phase-2 Japanese transcription: '{ja_text}'")
                        if ja_text and len(ja_text) > 1:
                            UI.you(ja_text)
                            hmi({"type": "chat", "text": ja_text, "is_user": True})
                            hmi({"type": "status", "awake": True, "processing": True, "speaking": False})
                            drain_queue(agent_task_queue)
                            agent_task_queue.put(ja_text)
                    except Exception as e:
                        UI.error(f"Phase-2 Japanese transcribe failed: {e}")
                    try: os.remove(filename)
                    except Exception: pass
                else:
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
