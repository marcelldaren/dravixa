#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# DRAVIXA LAUNCHER
# Starts the full pipeline, each component in its own terminal:
#   1. spotifyd          — Spotify Connect daemon
#   2. driveremo.py      — emotion classification node
#   3. drivermonitor.py  — driver fatigue/phone/mood monitor
#   4. gemini_dravixa.py — main AI assistant
#   5. dravixa_hmi.py    — PyQt5 HMI dashboard
#
# Usage: ./launch_dravixa.sh
# Stop:  ./stop_dravixa.sh   (or close each terminal window)
# ═══════════════════════════════════════════════════════════════

set -e

# ── Configuration — edit these if your paths differ ─────────────
DRAVIXA_DIR="/home/dravixa/dravixa"
VENV_ACTIVATE="source /home/dravixa/dravixa_env/bin/activate"
RESPEAKER_SINK="alsa_output.usb-SEEED_ReSpeaker_4_Mic_Array__UAC1.0-00.analog-stereo"

# Delays between stages (seconds) — gives each process time to fully
# initialize (load models, bind ports, open camera) before the next
# one starts and tries to connect to it.
DELAY_AFTER_SPOTIFYD=3
DELAY_AFTER_DRIVEREMO=4
DELAY_AFTER_DRIVERMONITOR=10   # longest — EAR calibration takes ~8s
DELAY_AFTER_DRAVIXA=5

# ── Helper: open a command in a new terminal tab/window ──────────
# Uses gnome-terminal; falls back to xterm if not available.
open_terminal() {
    local title="$1"
    local cmd="$2"
    if command -v gnome-terminal &> /dev/null; then
        gnome-terminal --title="$title" -- bash -c "$cmd; exec bash"
    elif command -v xterm &> /dev/null; then
        xterm -T "$title" -e bash -c "$cmd; exec bash" &
    else
        echo "ERROR: Neither gnome-terminal nor xterm found. Install one:"
        echo "  sudo apt install gnome-terminal"
        exit 1
    fi
}

echo "═══════════════════════════════════════════════"
echo "  DRAVIXA LAUNCHER"
echo "═══════════════════════════════════════════════"

# ── 1. Set audio sink to ReSpeaker before anything else starts ───
echo "[1/5] Setting default audio sink to ReSpeaker..."
pactl set-default-sink "$RESPEAKER_SINK" 2>/dev/null || \
    echo "  WARN: Could not set sink — check sink name with 'pactl list short sinks'"

# ── 2. spotifyd ────────────────────────────────────────────────
echo "[2/5] Starting spotifyd..."
open_terminal "Dravixa - spotifyd" "spotifyd --no-daemon"
sleep $DELAY_AFTER_SPOTIFYD

# ── 3. driveremo.py (emotion node) ────────────────────────────
echo "[3/5] Starting driveremo.py (emotion node)..."
open_terminal "Dravixa - Emotion Node" \
    "cd '$DRAVIXA_DIR' && $VENV_ACTIVATE && python3 driveremo.py"
sleep $DELAY_AFTER_DRIVEREMO

# ── 4. drivermonitor.py (fatigue/phone/mood) ──────────────────
echo "[4/5] Starting drivermonitor.py (this takes ~10s for EAR calibration)..."
open_terminal "Dravixa - Driver Monitor" \
    "cd '$DRAVIXA_DIR' && $VENV_ACTIVATE && python3 drivermonitor.py"
sleep $DELAY_AFTER_DRIVERMONITOR

# ── 5. gemini_dravixa.py (main AI assistant) ──────────────────
echo "[5/5] Starting gemini_dravixa.py (main assistant)..."
open_terminal "Dravixa - Main Assistant" \
    "cd '$DRAVIXA_DIR' && $VENV_ACTIVATE && python3 gemini_dravixa.py"
sleep $DELAY_AFTER_DRAVIXA

# ── 6. dravixa_hmi.py (dashboard UI) ───────────────────────────
echo "[6/6] Starting dravixa_hmi.py (HMI dashboard)..."
open_terminal "Dravixa - HMI Dashboard" \
    "cd '$DRAVIXA_DIR' && $VENV_ACTIVATE && python3 dravixa_hmi.py"
 
open_terminal "Dravixa - Phone Detection" \
    "cd '$DRAVIXA_DIR' && source phone_env/bin/activate && python3 phone_detect_node.py"

echo "═══════════════════════════════════════════════"
echo "  All components launched."
echo "  5 terminal windows should now be open."
echo "  Say 'Hello Toyota' or 'Dravixa' to begin."
echo "═══════════════════════════════════════════════"
