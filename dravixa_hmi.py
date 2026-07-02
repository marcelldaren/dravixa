"""
================================================================
 DRAVIXA HMI v4.0 — NIO ET9 × Cyberpunk Cockpit
================================================================
 Design language:
   Deep-space OLED black + Electric cyan primary
   Crimson-plasma accent for warnings/alerts
   Monospace data font for all numeric readouts
   Signature: Rotating vinyl album art disc on media tab

 Tabs: Dashboard | Media (Spotify-grade) | Navigate | Climate | Settings
 Bridge: TCP 19999 JSON packets from dravixa_main.py
 Install: pip install PyQt5 PyQtWebEngine spotipy psutil
================================================================
"""
import json
import sys, math, time, random, threading, json, socket, os
from datetime import datetime
from collections import deque

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QStackedWidget, QLineEdit, QScrollArea,
    QFrame, QSizePolicy, QSlider, QProgressBar, QGridLayout,
    QGraphicsOpacityEffect, QSpacerItem
)
from PyQt5.QtCore import (
    Qt, QTimer, QRectF, QPointF, QPropertyAnimation, QRect,
    QEasingCurve, pyqtSignal, QObject, QSize, QThread
)
from PyQt5.QtGui import (
    QPainter, QColor, QPen, QFont, QBrush, QPainterPath,
    QLinearGradient, QRadialGradient, QPolygonF, QConicalGradient,
    QPalette, QFontMetrics, QPixmap, QImage
)

# NOMI/Xiao-P-inspired robot face + BYD/XPeng-style car visualization —
# kept as separate files (dravixa_avatar.py / car_visual.py) so they can
# be developed/tested independently, same pattern as driveremo.py being
# separate from drivermonitor.py.
from dravixa_avatar import DravixaAvatar
from car_visual import CarVisual

try:
    from PyQt5.QtWebEngineWidgets import QWebEngineView
    WEB_ENGINE = True
except ImportError:
    WEB_ENGINE = False

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
    SPOTIPY_OK = True
except ImportError:
    SPOTIPY_OK = False

try:
    import psutil
    PSUTIL_OK = True
except ImportError:
    PSUTIL_OK = False

# ─────────────────────────────────────────────────────────────
# TOKENS
# ─────────────────────────────────────────────────────────────
C = {
    "void":      "#020408",
    "deep":      "#05080F",
    "surface":   "#080E1A",
    "raised":    "#0C1524",
    "hover":     "#101C2E",
    "border":    "#0D1F30",
    "border_hi": "#163048",
    "cyan":      "#00C8FF",
    "cyan2":     "#00A8D8",
    "cyan_bg":   "#001E2E",
    "crimson":   "#FF2952",
    "amber":     "#FFA820",
    "green":     "#00E896",
    "purple":    "#A78BFA",
    "white":     "#EAF2FF",
    "dim":       "#4A6A88",
    "muted":     "#223348",
    "spotify":   "#1ED760",
}

FONT_UI   = "Arial"
FONT_DATA = "Courier New"
MAP_KEY   = "AIzaSyCKh_E_tmWl_05LywLZlmx2Kjy0M_gXxi0"

SP_CLIENT_ID     = "1ef7ec97d28e48efacd3771cb3a0fea2"
SP_CLIENT_SECRET = "15736ab0b7974500a72779f632647b6a"
SP_REDIRECT      = "http://127.0.0.1:8888/callback"
SP_SCOPE         = ("user-read-playback-state user-modify-playback-state "
                    "user-read-currently-playing playlist-read-private "
                    "user-library-read")

# ─────────────────────────────────────────────────────────────
# BRIDGE
# ─────────────────────────────────────────────────────────────
class Bridge(QObject):
    chat_sig     = pyqtSignal(str, bool)
    status_sig   = pyqtSignal(dict)
    spotify_sig  = pyqtSignal(dict)
    vehicle_sig  = pyqtSignal(dict)
    navigate_sig = pyqtSignal(dict)
    adas_sig     = pyqtSignal(dict)
    alert_sig    = pyqtSignal(dict)   # driver fatigue/distraction warnings

    def __init__(self):
        super().__init__()
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", 19999))
            srv.listen(4)
            srv.settimeout(1.0)
            while True:
                try:
                    conn, _ = srv.accept()
                    data = b""
                    while True:
                        c = conn.recv(4096)
                        if not c: break
                        data += c
                    conn.close()
                    pkt = json.loads(data.decode())
                    t = pkt.get("type","")
                    if   t == "chat":     self.chat_sig.emit(pkt.get("text",""), pkt.get("is_user",False))
                    elif t == "status":   self.status_sig.emit(pkt)
                    elif t == "spotify":  self.spotify_sig.emit(pkt)
                    elif t == "vehicle":  self.vehicle_sig.emit(pkt)
                    elif t == "navigate": self.navigate_sig.emit(pkt)
                    elif t == "adas":     self.adas_sig.emit(pkt)
                    elif t == "alert":    self.alert_sig.emit(pkt)
                except socket.timeout:
                    pass
        except Exception:
            pass

# ─────────────────────────────────────────────────────────────
# HOLOGRAPHIC GAUGE
# ─────────────────────────────────────────────────────────────
class HoloGauge(QWidget):
    def __init__(self, title, unit, max_val, decimals=0, rpm=False):
        super().__init__()
        self.title    = title
        self.unit     = unit
        self.max_val  = max_val
        self.decimals = decimals
        self.rpm      = rpm
        self._val     = 0.0
        self._disp    = 0.0
        self._phase   = 0.0
        self.setMinimumSize(240, 240)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        t = QTimer(self); t.timeout.connect(self._tick); t.start(16)

    def set_value(self, v):
        self._val = max(0.0, min(float(v), self.max_val))

    def _tick(self):
        self._disp  += (self._val - self._disp) * 0.07
        self._phase  = (self._phase + 0.025) % (math.pi * 2)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h   = self.width(), self.height()
        cx, cy = w/2, h/2
        R      = min(w,h)/2 - 18
        ratio  = min(self._disp / self.max_val, 1.0)
        S, SP  = 225, -270   # start deg, span deg

        # Outer decorative ring
        p.setPen(QPen(QColor(C["border"]), 1))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(cx,cy), R+12, R+12)

        # Tick marks — 48 total, every 6th is major
        for i in range(49):
            a = math.radians(S + SP*i/48)
            is_maj = (i % 6 == 0)
            r_out  = R + 10
            r_in   = R + (3 if not is_maj else -4)
            x1 = cx + r_out*math.cos(a); y1 = cy - r_out*math.sin(a)
            x2 = cx + r_in *math.cos(a); y2 = cy - r_in *math.sin(a)
            col = QColor(C["dim"] if not is_maj else C["border_hi"])
            p.setPen(QPen(col, 1.5 if is_maj else 0.8))
            p.drawLine(QPointF(x1,y1), QPointF(x2,y2))

        # Background track
        rect = QRectF(cx-R, cy-R, R*2, R*2)
        p.setPen(QPen(QColor(C["border"]), 9, Qt.SolidLine, Qt.FlatCap))
        p.drawArc(rect, int(S*16), int(SP*16))

        # Colored arc with glow
        if ratio > 0.002:
            if ratio < 0.55:   arc_c = C["cyan"]
            elif ratio < 0.80: arc_c = C["amber"]
            else:              arc_c = C["crimson"]
            sweep = int(SP * ratio * 16)
            for w2, al in [(18,15),(12,35),(7,180)]:
                gc = QColor(arc_c); gc.setAlpha(al)
                p.setPen(QPen(gc, w2, Qt.SolidLine, Qt.FlatCap))
                p.drawArc(rect, int(S*16), sweep)

        # Needle
        na = math.radians(S + SP*ratio)
        nx = cx + (R-20)*math.cos(na); ny = cy - (R-20)*math.sin(na)
        pp = QPainterPath()
        pp.moveTo(cx, cy)
        pp.lineTo(nx, ny)
        pen = QPen(QColor(C["cyan"]), 2, Qt.SolidLine, Qt.RoundCap)
        p.setPen(pen); p.drawPath(pp)

        # Hub
        pulse = 180 + int(55*math.sin(self._phase))
        hc = QColor(C["cyan"]); hc.setAlpha(pulse)
        p.setPen(Qt.NoPen); p.setBrush(QBrush(hc))
        p.drawEllipse(QPointF(cx,cy), 7, 7)
        p.setBrush(QBrush(QColor(C["void"])))
        p.drawEllipse(QPointF(cx,cy), 3.5, 3.5)

        # Value
        val_s = f"{self._disp:.1f}" if self.rpm else f"{int(self._disp)}"
        p.setPen(QColor(C["white"]))
        f = QFont(FONT_DATA, 34, QFont.Bold)
        p.setFont(f)
        p.drawText(QRectF(cx-R*.7, cy-24, R*1.4, 48), Qt.AlignCenter, val_s)

        # Unit
        p.setPen(QColor(C["cyan2"]))
        p.setFont(QFont(FONT_UI, 9))
        p.drawText(QRectF(cx-R*.7, cy+22, R*1.4, 18), Qt.AlignCenter, self.unit)

        # Title above
        p.setPen(QColor(C["dim"]))
        tf = QFont(FONT_UI, 9); tf.setLetterSpacing(QFont.AbsoluteSpacing, 2.5)
        p.setFont(tf)
        p.drawText(QRectF(cx-R, cy-R-16, R*2, 16), Qt.AlignCenter, self.title.upper())


# ─────────────────────────────────────────────────────────────
# SPINNING VINYL DISC (signature element — media tab)
# ─────────────────────────────────────────────────────────────
class VinylDisc(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(260, 260)
        self._angle   = 0.0
        self._playing = False
        self._color1  = QColor(C["cyan"])
        self._color2  = QColor(C["purple"])
        self._timer   = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)

    def set_playing(self, playing):
        self._playing = playing

    def set_colors(self, c1: QColor, c2: QColor):
        self._color1 = c1; self._color2 = c2

    def _tick(self):
        if self._playing:
            self._angle = (self._angle + 0.8) % 360
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w/2, h/2
        R = min(w,h)/2 - 4

        p.translate(cx, cy)
        p.rotate(self._angle)

        # Outer disc — conical gradient simulating vinyl
        for i in range(0, 360, 3):
            a1 = math.radians(i)
            a2 = math.radians(i+3)
            t  = i/360.0
            rc = QColor(
                int(self._color1.red()   * (1-t) + self._color2.red()   * t),
                int(self._color1.green() * (1-t) + self._color2.green() * t),
                int(self._color1.blue()  * (1-t) + self._color2.blue()  * t),
            )
            rc.setAlpha(180)
            path = QPainterPath()
            path.moveTo(0, 0)
            path.arcTo(QRectF(-R,-R,R*2,R*2), i, 3)
            path.closeSubpath()
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(rc))
            p.drawPath(path)

        # Groove rings
        p.setBrush(Qt.NoBrush)
        for r in range(int(R*0.35), int(R), 8):
            gc = QColor(C["void"]); gc.setAlpha(60)
            p.setPen(QPen(gc, 1))
            p.drawEllipse(QPointF(0,0), r, r)

        # Center label circle
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(C["surface"])))
        p.drawEllipse(QPointF(0,0), R*0.28, R*0.28)

        # Center hole
        p.setBrush(QBrush(QColor(C["void"])))
        p.drawEllipse(QPointF(0,0), 5, 5)

        p.resetTransform()
        p.translate(cx, cy)

        # Outer glow ring
        if self._playing:
            glow_alpha = 80 + int(40*math.sin(self._angle*0.05))
            gc = QColor(self._color1); gc.setAlpha(glow_alpha)
            p.setPen(QPen(gc, 3))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(0,0), R+2, R+2)


# ─────────────────────────────────────────────────────────────
# AI WAVEFORM
# ─────────────────────────────────────────────────────────────
class AIWave(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(38)
        self._state = "idle"
        self._bars  = [0.0]*32
        self._ph    = 0.0
        t = QTimer(self); t.timeout.connect(self._tick); t.start(33)

    def set_state(self, s): self._state = s

    def _tick(self):
        self._ph += 0.12
        n = len(self._bars)
        for i in range(n):
            s = self._state
            if   s == "idle":       tgt = 0.04 + 0.03*math.sin(self._ph + i*0.5)
            elif s == "listening":  tgt = 0.35 + 0.3 *math.sin(self._ph*2.2 + i*0.6)*random.uniform(0.6,1.4)
            elif s == "speaking":   tgt = 0.55 + 0.42*abs(math.sin(self._ph*3.1 + i*0.8))
            elif s == "processing": tgt = 0.18 + 0.14*math.sin(self._ph*1.5 + i*1.1)
            else:                   tgt = 0.04
            self._bars[i] += (tgt - self._bars[i]) * 0.25
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        n    = len(self._bars)
        bw   = w / (n * 1.7)
        gap  = bw * 0.7
        tot  = bw + gap
        off  = (w - tot*n) / 2
        cols = {"idle": C["muted"], "listening": C["green"],
                "speaking": C["cyan"], "processing": C["amber"]}
        col  = QColor(cols.get(self._state, C["muted"]))
        for i, val in enumerate(self._bars):
            bh = max(2, val*h)
            x  = off + i*tot
            y  = (h - bh)/2
            gl = QColor(col); gl.setAlpha(30)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(gl))
            p.drawRoundedRect(QRectF(x-1, y-1, bw+2, bh+2), 2, 2)
            p.setBrush(QBrush(col))
            p.drawRoundedRect(QRectF(x, y, bw, bh), 2, 2)


# ─────────────────────────────────────────────────────────────
# ADAS BAR
# ─────────────────────────────────────────────────────────────
class AdasBar(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(44)
        self._state = {"lane": True, "collision": False, "blind_l": False, "blind_r": False}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 6, 20, 6)
        layout.setSpacing(10)
        self._pills = {}
        items = [("LKA","Lane Keep","lane"),("AEB","Auto Brake","collision"),
                 ("BSM-L","Blind L","blind_l"),("BSM-R","Blind R","blind_r")]
        for code, tip, key in items:
            pill = QLabel(code)
            pill.setFixedSize(58, 26)
            pill.setAlignment(Qt.AlignCenter)
            pill.setToolTip(tip)
            self._pills[key] = pill
            layout.addWidget(pill)
        layout.addStretch()
        self._spd_lbl = QLabel("LIMIT  80")
        self._spd_lbl.setStyleSheet(f"color:{C['crimson']}; font-size:11px; font-weight:bold; font-family:{FONT_DATA};")
        layout.addWidget(self._spd_lbl)
        self._time_lbl = QLabel("00:00")
        self._time_lbl.setStyleSheet(f"color:{C['dim']}; font-size:11px; font-family:{FONT_DATA}; padding-left:16px;")
        layout.addWidget(self._time_lbl)
        t = QTimer(self); t.timeout.connect(self._tick_time); t.start(1000)
        self._refresh()

    def _tick_time(self):
        self._time_lbl.setText(datetime.now().strftime("%H:%M"))

    def update_adas(self, pkt):
        self._state["lane"]      = pkt.get("lane", True)
        self._state["collision"] = pkt.get("collision", False)
        self._state["blind_l"]   = pkt.get("blind_left", False)
        self._state["blind_r"]   = pkt.get("blind_right", False)
        lim = pkt.get("speed_limit", 80)
        self._spd_lbl.setText(f"LIMIT  {lim}")
        self._refresh()

    def _refresh(self):
        cfg = {
            "lane":      (self._state["lane"],      C["green"],   "ACTIVE"),
            "collision": (self._state["collision"],  C["crimson"], "ALERT"),
            "blind_l":   (self._state["blind_l"],    C["amber"],   "WARN"),
            "blind_r":   (self._state["blind_r"],    C["amber"],   "WARN"),
        }
        for key, (active, on_c, _) in cfg.items():
            pill = self._pills[key]
            c = on_c if active else C["muted"]
            pill.setStyleSheet(f"""
                border:1px solid {c}; border-radius:4px;
                color:{c}; font-size:9px; font-weight:bold; letter-spacing:1px;
                background: transparent;
            """)


# ─────────────────────────────────────────────────────────────
# TOAST
# ─────────────────────────────────────────────────────────────
class Toast(QLabel):
    def __init__(self, parent, text, level="info"):
        super().__init__(text, parent)
        lc = {"info":C["cyan"],"ok":C["green"],"warn":C["amber"],"error":C["crimson"]}.get(level,C["cyan"])
        self.setStyleSheet(f"""
            background:{C['raised']}; border:1px solid {lc}; border-left:3px solid {lc};
            border-radius:6px; color:{C['white']}; font-size:12px; padding:10px 16px;
        """)
        self.setFixedWidth(320); self.adjustSize()
        self.move(parent.width()-self.width()-20, parent.height()-self.height()-20)
        self.show()
        self._op = QGraphicsOpacityEffect(self); self.setGraphicsEffect(self._op)
        QTimer.singleShot(2500, self._fade)

    def _fade(self):
        self._anim = QPropertyAnimation(self._op, b"opacity")
        self._anim.setDuration(800); self._anim.setStartValue(1.0); self._anim.setEndValue(0.0)
        self._anim.finished.connect(self.deleteLater); self._anim.start()


# ─────────────────────────────────────────────────────────────
# SYSTEM BAR
# ─────────────────────────────────────────────────────────────
class SysBar(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(20)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(24)
        self._lbls = {}
        for k in ["CPU","RAM","GPU","DISK"]:
            l = QLabel(f"{k}: —")
            l.setStyleSheet(f"color:{C['muted']}; font-size:9px; font-family:{FONT_DATA};")
            self._lbls[k] = l
            layout.addWidget(l)
        layout.addStretch()
        self._ver = QLabel("DRAVIXA v4.0  |  Jetson Orin Nano")
        self._ver.setStyleSheet(f"color:{C['muted']}; font-size:9px; font-family:{FONT_DATA};")
        layout.addWidget(self._ver)
        t = QTimer(self); t.timeout.connect(self._refresh); t.start(3000); self._refresh()

    def _refresh(self):
        if not PSUTIL_OK: return
        try:
            self._lbls["CPU"].setText(f"CPU: {psutil.cpu_percent():.0f}%")
            self._lbls["RAM"].setText(f"RAM: {psutil.virtual_memory().percent:.0f}%")
            self._lbls["DISK"].setText(f"DISK: {psutil.disk_usage('/').percent:.0f}%")
            try:
                with open("/sys/devices/virtual/thermal/thermal_zone1/temp") as f:
                    self._lbls["GPU"].setText(f"GPU: {int(f.read())//1000}°C")
            except Exception:
                self._lbls["GPU"].setText("GPU: —")
        except Exception:
            pass

def _place_color(place_type):
    return {
        "gas_station":    "#FFA820",
        "grocery_or_supermarket": "#00E896",
        "restaurant":     "#FF2952",
        "hospital":       "#FF2952",
        "atm":            "#A78BFA",
        "pharmacy":       "#00C8FF",
        "parking":        "#4A6A88",
    }.get(place_type, "#00C8FF")

def _place_label(place_type):
    return {
        "gas_station":    "fuel stations",
        "grocery_or_supermarket": "grocery stores",
        "restaurant":     "restaurants",
        "hospital":       "hospitals",
        "atm":            "ATMs",
        "pharmacy":       "pharmacies",
        "parking":        "parking spots",
    }.get(place_type, "places")
# ─────────────────────────────────────────────────────────────
# MAP HTML
# ─────────────────────────────────────────────────────────────
def _map_html(olat, olng, dlat=None, dlng=None, dest_name="", places=None):
    dest_js = ""
    if dlat and dlng:
        dest_js = f"""
  var dest = {{lat:{dlat}, lng:{dlng}}};
  window._destMarker = new google.maps.Marker({{position:dest, map:window._map,
    icon:{{path:google.maps.SymbolPath.CIRCLE, scale:11,
           fillColor:'#FF2952', fillOpacity:1,
           strokeColor:'#fff', strokeWeight:2}}}});
  window._dirRenderer = new google.maps.DirectionsRenderer({{
    map: window._map, suppressMarkers:true,
    polylineOptions:{{strokeColor:'#00C8FF',strokeOpacity:0.9,strokeWeight:6}}
  }});
  new google.maps.DirectionsService().route(
    {{origin:pos, destination:dest, travelMode:google.maps.TravelMode.DRIVING}},
    function(res,st){{
      if(st==='OK') window._dirRenderer.setDirections(res);
    }}
  );
  window._map.setCenter({{lat:({olat}+{dlat})/2, lng:({olng}+{dlng})/2}});
  window._map.setZoom(12);
  document.getElementById('nav_info').style.display='block';
  document.getElementById('nav_info').innerText='→  {dest_name}';
"""

    places_js = ""
    if places:
        places_js = f"""
  window._placeMarkers = [];
  var service = new google.maps.places.PlacesService(window._map);
  service.nearbySearch({{
    location: pos,
    radius: 2000,
    type: '{places}'
  }}, function(results, status) {{
    if (status === google.maps.places.PlacesServiceStatus.OK) {{
      for (var i = 0; i < Math.min(results.length, 8); i++) {{
        var place = results[i];
        var marker = new google.maps.Marker({{
          position: place.geometry.location,
          map: window._map,
          icon: {{
            path: google.maps.SymbolPath.CIRCLE,
            scale: 9,
            fillColor: '{_place_color(places)}',
            fillOpacity: 1,
            strokeColor: '#fff',
            strokeWeight: 1.5
          }}
        }});
        window._placeMarkers.push(marker);
        (function(m, p) {{
          var iw = new google.maps.InfoWindow({{
            content: '<div style="background:#080E1A;color:#EAF2FF;'
                   + 'padding:8px 12px;border-radius:6px;'
                   + 'font-family:Arial;font-size:12px;">'
                   + '<b>' + p.name + '</b><br>'
                   + (p.vicinity || '') + '<br>'
                   + (p.rating ? '&#9733; ' + p.rating : '')
                   + '</div>'
          }});
          m.addListener('click', function() {{ iw.open(window._map, m); }});
        }})(marker, place);
      }}
      document.getElementById('nav_info').style.display='block';
      document.getElementById('nav_info').innerText =
        'Found ' + Math.min(results.length, 8) + ' nearby {_place_label(places)}';
    }}
  }});
"""

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<style>
  body,html{{margin:0;padding:0;width:100%;height:100%;background:#020408;}}
  #map{{width:100%;height:100%;}}
  #nav_info{{
    display:none;position:absolute;top:14px;left:50%;
    transform:translateX(-50%);
    background:#080E1A;border:1px solid #00C8FF;border-radius:8px;
    color:#00C8FF;font:bold 12px Arial;padding:9px 20px;z-index:999;
    letter-spacing:1px;white-space:nowrap;
  }}
</style></head><body>
<div id="nav_info"></div>
<div id="map"></div>
<script>
function initMap(){{
  var pos={{lat:{olat},lng:{olng}}};
  window._map = new google.maps.Map(document.getElementById('map'),{{
    zoom:14, center:pos, mapTypeId:'roadmap', disableDefaultUI:true,
    styles:[
      {{"elementType":"geometry","stylers":[{{"color":"#05080F"}}]}},
      {{"elementType":"labels.text.fill","stylers":[{{"color":"#4A6A88"}}]}},
      {{"elementType":"labels.text.stroke","stylers":[{{"color":"#020408"}}]}},
      {{"featureType":"road","elementType":"geometry","stylers":[{{"color":"#0C1524"}}]}},
      {{"featureType":"road.highway","elementType":"geometry","stylers":[{{"color":"#163048"}}]}},
      {{"featureType":"road.highway","elementType":"labels.text.fill","stylers":[{{"color":"#00C8FF"}}]}},
      {{"featureType":"water","elementType":"geometry","stylers":[{{"color":"#020408"}}]}},
      {{"featureType":"poi","elementType":"geometry","stylers":[{{"color":"#05080F"}}]}},
      {{"featureType":"transit","elementType":"geometry","stylers":[{{"color":"#080E1A"}}]}}
    ]
  }});
  window._originMarker = new google.maps.Marker({{
    position:pos, map:window._map,
    icon:{{
      path:google.maps.SymbolPath.CIRCLE, scale:10,
      fillColor:'#00C8FF', fillOpacity:1,
      strokeColor:'#fff', strokeWeight:2
    }}
  }});
  {dest_js}
  {places_js}
}}
</script>
<script src="https://maps.googleapis.com/maps/api/js?key={MAP_KEY}&libraries=places&callback=initMap" async defer></script>
</body></html>"""

class MapPanel(QWidget):
    def __init__(self, lat=-6.1781, lng=106.630):
        super().__init__()
        self._lat = lat
        self._lng = lng
        self._initialized = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        if WEB_ENGINE:
            self._view = QWebEngineView()
            self._view.loadFinished.connect(self._on_load_finished)
            self._view.setHtml(_map_html(lat, lng))
            layout.addWidget(self._view)
        else:
            ph = QLabel(f"Map requires PyQtWebEngine\npip install PyQtWebEngine\n\n{lat:.4f}°N  {lng:.4f}°E")
            ph.setAlignment(Qt.AlignCenter)
            ph.setStyleSheet(f"color:{C['dim']}; font-size:13px;")
            layout.addWidget(ph)

    def _on_load_finished(self, ok):
        self._initialized = ok

    def show_route(self, olat, olng, dlat, dlng, dest="Destination"):
        if not WEB_ENGINE:
            return
        if not self._initialized:
            # Not loaded yet — reload full page with route
            self._view.setHtml(_map_html(olat, olng, dlat, dlng, dest))
            return
        # Update map without reloading page
        js = f"""
            var origin = {{lat:{olat}, lng:{olng}}};
            var dest   = {{lat:{dlat}, lng:{dlng}}};

            if (typeof window._map === 'undefined') {{
                location.reload();
            }} else {{
                window._map.setCenter({{lat:({olat}+{dlat})/2, lng:({olng}+{dlng})/2}});
                window._map.setZoom(13);

                if (window._dirRenderer) window._dirRenderer.setMap(null);
                window._dirRenderer = new google.maps.DirectionsRenderer({{
                    map: window._map,
                    suppressMarkers: true,
                    polylineOptions: {{
                        strokeColor: '#00C8FF',
                        strokeOpacity: 0.9,
                        strokeWeight: 6
                    }}
                }});

                new google.maps.DirectionsService().route(
                    {{origin: origin, destination: dest,
                      travelMode: google.maps.TravelMode.DRIVING}},
                    function(res, st) {{
                        if (st === 'OK') window._dirRenderer.setDirections(res);
                    }}
                );

                if (window._destMarker) window._destMarker.setMap(null);
                window._destMarker = new google.maps.Marker({{
                    position: dest, map: window._map,
                    icon: {{path: google.maps.SymbolPath.CIRCLE, scale: 11,
                            fillColor: '#FF2952', fillOpacity: 1,
                            strokeColor: '#fff', strokeWeight: 2}}
                }});

                document.getElementById('nav_info').style.display = 'block';
                document.getElementById('nav_info').innerText = '→  {dest}';
            }}
        """
        self._view.page().runJavaScript(js)

    def show_places(self, place_type):
        if not WEB_ENGINE:
            return
        if not self._initialized:
            self._view.setHtml(_map_html(self._lat, self._lng, places=place_type))
            return
        js = f"""
            if (typeof window._map === 'undefined') {{
                location.reload();
            }} else {{
                window._map.setCenter({{lat:{self._lat}, lng:{self._lng}}});
                window._map.setZoom(14);

                var service = new google.maps.places.PlacesService(window._map);
                service.nearbySearch({{
                    location: {{lat:{self._lat}, lng:{self._lng}}},
                    radius: 2000,
                    type: '{place_type}'
                }}, function(results, status) {{
                    if (status === google.maps.places.PlacesServiceStatus.OK) {{
                        if (window._placeMarkers) {{
                            window._placeMarkers.forEach(function(m) {{ m.setMap(null); }});
                        }}
                        window._placeMarkers = [];
                        var colors = {{
                            'gas_station': '#FFA820',
                            'grocery_or_supermarket': '#00E896',
                            'restaurant': '#FF2952',
                            'atm': '#A78BFA',
                            'pharmacy': '#00C8FF',
                            'parking': '#4A6A88'
                        }};
                        var col = colors['{place_type}'] || '#00C8FF';
                        for (var i = 0; i < Math.min(results.length, 8); i++) {{
                            var place = results[i];
                            var marker = new google.maps.Marker({{
                                position: place.geometry.location,
                                map: window._map,
                                icon: {{path: google.maps.SymbolPath.CIRCLE,
                                        scale: 9, fillColor: col, fillOpacity: 1,
                                        strokeColor: '#fff', strokeWeight: 1.5}}
                            }});
                            window._placeMarkers.push(marker);
                            (function(m, p) {{
                                var iw = new google.maps.InfoWindow({{
                                    content: '<div style="background:#080E1A;color:#EAF2FF;padding:8px 12px;border-radius:6px;font-size:12px;font-family:Arial;">'
                                           + '<b>' + p.name + '</b><br>'
                                           + (p.vicinity || '') + '<br>'
                                           + (p.rating ? '★ ' + p.rating : '') + '</div>'
                                }});
                                m.addListener('click', function() {{ iw.open(window._map, m); }});
                            }})(marker, place);
                        }}
                        document.getElementById('nav_info').style.display = 'block';
                        document.getElementById('nav_info').innerText = 'Found ' + Math.min(results.length, 8) + ' nearby places';
                    }}
                }});
            }}
        """
        self._view.page().runJavaScript(js)

    def update_location(self, lat, lng):
        self._lat = lat
        self._lng = lng
        if WEB_ENGINE and self._initialized:
            js = f"""
                if (typeof window._map !== 'undefined') {{
                    window._originMarker.setPosition({{lat:{lat}, lng:{lng}}});
                    window._map.panTo({{lat:{lat}, lng:{lng}}});
                }}
            """
            self._view.page().runJavaScript(js)


# ─────────────────────────────────────────────────────────────
# CHAT BUBBLE
# ─────────────────────────────────────────────────────────────
class Bubble(QFrame):
    def __init__(self, text, is_user):
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 3, 0, 3)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setContentsMargins(10, 7, 10, 7)
        lbl.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        lbl.setMaximumWidth(240)
        if is_user:
            lbl.setStyleSheet(f"background:{C['raised']}; border:1px solid {C['cyan2']}; border-radius:12px 12px 2px 12px; color:{C['white']}; font-size:11px;")
            layout.addStretch(); layout.addWidget(lbl)
        else:
            lbl.setStyleSheet(f"background:{C['surface']}; border:1px solid {C['purple']}; border-radius:12px 12px 12px 2px; color:{C['white']}; font-size:11px;")
            layout.addWidget(lbl); layout.addStretch()


# ─────────────────────────────────────────────────────────────
# MEDIA TAB — full Spotify-grade player
# ─────────────────────────────────────────────────────────────
class MediaTab(QWidget):
    def __init__(self):
        super().__init__()
        self._sp      = None
        self._blocked = False
        self._playing = False
        self._liked   = False
        self._playlist_cache = []
        self._build()
        self._connect_spotify()
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll)
        self._poll_timer.start(3000)

    def _connect_spotify(self):
        if not SPOTIPY_OK:
            self._status_lbl.setText("Install spotipy:  pip install spotipy")
            return
        try:
            self._sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
                client_id=SP_CLIENT_ID, client_secret=SP_CLIENT_SECRET,
                redirect_uri=SP_REDIRECT, scope=SP_SCOPE, open_browser=False))
            self._status_lbl.setText("")
        except Exception as e:
            self._status_lbl.setText(f"Auth needed: {str(e)[:50]}")

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Left: now playing ─────────────────────────────────
        left = QWidget()
        left.setObjectName("media_left")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(40, 30, 40, 30)
        ll.setSpacing(0)

        # Vinyl disc
        disc_row = QHBoxLayout()
        self._vinyl = VinylDisc()
        disc_row.addStretch()
        disc_row.addWidget(self._vinyl)
        disc_row.addStretch()
        ll.addLayout(disc_row)
        ll.addSpacing(24)

        # Track info
        self._track_lbl = QLabel("Nothing playing")
        self._track_lbl.setAlignment(Qt.AlignCenter)
        self._track_lbl.setStyleSheet(f"color:{C['white']}; font-size:20px; font-weight:bold;")
        self._track_lbl.setWordWrap(True)
        ll.addWidget(self._track_lbl)

        ll.addSpacing(4)
        self._artist_lbl = QLabel("—")
        self._artist_lbl.setAlignment(Qt.AlignCenter)
        self._artist_lbl.setStyleSheet(f"color:{C['dim']}; font-size:13px;")
        ll.addWidget(self._artist_lbl)

        ll.addSpacing(4)
        self._album_lbl = QLabel("")
        self._album_lbl.setAlignment(Qt.AlignCenter)
        self._album_lbl.setStyleSheet(f"color:{C['muted']}; font-size:11px;")
        ll.addWidget(self._album_lbl)
        ll.addSpacing(20)

        # Progress
        prog_row = QHBoxLayout()
        self._pos_lbl = QLabel("0:00")
        self._pos_lbl.setStyleSheet(f"color:{C['dim']}; font-size:10px; font-family:{FONT_DATA};")
        self._dur_lbl = QLabel("0:00")
        self._dur_lbl.setStyleSheet(f"color:{C['dim']}; font-size:10px; font-family:{FONT_DATA};")
        self._prog_bar = QProgressBar()
        self._prog_bar.setRange(0, 1000); self._prog_bar.setValue(0)
        self._prog_bar.setTextVisible(False); self._prog_bar.setFixedHeight(4)
        self._prog_bar.setStyleSheet(f"""
            QProgressBar {{background:{C['border']}; border-radius:2px;}}
            QProgressBar::chunk {{background:{C['spotify']}; border-radius:2px;}}
        """)
        prog_row.addWidget(self._pos_lbl)
        prog_row.addWidget(self._prog_bar, 1)
        prog_row.addWidget(self._dur_lbl)
        ll.addLayout(prog_row)
        ll.addSpacing(20)

        # Controls row
        ctrl = QHBoxLayout()
        ctrl.setSpacing(10)
        ctrl.setAlignment(Qt.AlignCenter)

        self._shuffle_btn = self._mk_icon_btn("⇌", self._shuffle, small=True)
        self._prev_btn    = self._mk_icon_btn("⏮", self._prev)
        self._play_btn    = self._mk_icon_btn("▶", self._toggle, primary=True)
        self._next_btn    = self._mk_icon_btn("⏭", self._next)
        self._repeat_btn  = self._mk_icon_btn("↺", self._repeat, small=True)

        for b in [self._shuffle_btn, self._prev_btn, self._play_btn,
                  self._next_btn, self._repeat_btn]:
            ctrl.addWidget(b)
        ll.addLayout(ctrl)
        ll.addSpacing(16)

        # Like + Volume
        like_vol = QHBoxLayout()
        self._like_btn = QPushButton("♡")
        self._like_btn.setFixedSize(36, 36)
        self._like_btn.setStyleSheet(f"QPushButton{{background:transparent;color:{C['dim']};border:none;font-size:18px;}} QPushButton:hover{{color:{C['crimson']};}} ")
        self._like_btn.clicked.connect(self._toggle_like)
        like_vol.addWidget(self._like_btn)
        like_vol.addSpacing(8)
        vol_icon = QLabel("🔈")
        vol_icon.setStyleSheet(f"color:{C['dim']}; font-size:13px;")
        self._vol_slider = QSlider(Qt.Horizontal)
        self._vol_slider.setRange(0, 100); self._vol_slider.setValue(60)
        self._vol_slider.setStyleSheet(f"""
            QSlider::groove:horizontal{{background:{C['border']};height:4px;border-radius:2px;}}
            QSlider::handle:horizontal{{background:{C['spotify']};width:14px;height:14px;margin:-5px 0;border-radius:7px;}}
            QSlider::sub-page:horizontal{{background:{C['spotify']};border-radius:2px;}}
        """)
        self._vol_slider.valueChanged.connect(self._set_vol)
        like_vol.addWidget(vol_icon)
        like_vol.addWidget(self._vol_slider, 1)
        ll.addLayout(like_vol)

        self._status_lbl = QLabel("")
        self._status_lbl.setStyleSheet(f"color:{C['dim']}; font-size:10px;")
        self._status_lbl.setWordWrap(True)
        ll.addWidget(self._status_lbl)
        ll.addStretch()

        root.addWidget(left, 3)

        # ── Divider ───────────────────────────────────────────
        div = QFrame()
        div.setFixedWidth(1)
        div.setStyleSheet(f"background:{C['border']};")
        root.addWidget(div)

        # ── Right: queue / playlists ──────────────────────────
        right = QWidget()
        right.setObjectName("media_right")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(20, 20, 20, 20)
        rl.setSpacing(10)

        # Header tabs
        hdr = QHBoxLayout()
        self._queue_btn    = self._mk_tab_btn("Queue",     lambda: self._show_panel(0))
        self._playlist_btn = self._mk_tab_btn("Playlists", lambda: self._show_panel(1))
        hdr.addWidget(self._queue_btn)
        hdr.addWidget(self._playlist_btn)
        hdr.addStretch()
        rl.addLayout(hdr)

        self._right_stack = QStackedWidget()

        # Queue panel
        queue_w = QWidget()
        qwl = QVBoxLayout(queue_w)
        qwl.setContentsMargins(0,0,0,0)
        up_lbl = QLabel("UP NEXT")
        up_lbl.setStyleSheet(f"color:{C['dim']}; font-size:9px; letter-spacing:2px;")
        qwl.addWidget(up_lbl)
        self._queue_scroll = QScrollArea()
        self._queue_scroll.setWidgetResizable(True)
        self._queue_scroll.setFrameShape(QFrame.NoFrame)
        self._queue_content = QWidget()
        self._queue_vbox = QVBoxLayout(self._queue_content)
        self._queue_vbox.setSpacing(2)
        self._queue_vbox.addStretch()
        self._queue_scroll.setWidget(self._queue_content)
        qwl.addWidget(self._queue_scroll, 1)
        self._right_stack.addWidget(queue_w)

        # Playlist panel
        pl_w = QWidget()
        pl_l = QVBoxLayout(pl_w)
        pl_l.setContentsMargins(0,0,0,0)
        pl_hdr = QLabel("YOUR PLAYLISTS")
        pl_hdr.setStyleSheet(f"color:{C['dim']}; font-size:9px; letter-spacing:2px;")
        pl_l.addWidget(pl_hdr)
        self._pl_scroll = QScrollArea()
        self._pl_scroll.setWidgetResizable(True)
        self._pl_scroll.setFrameShape(QFrame.NoFrame)
        self._pl_content = QWidget()
        self._pl_vbox = QVBoxLayout(self._pl_content)
        self._pl_vbox.setSpacing(4)
        self._pl_vbox.addStretch()
        self._pl_scroll.setWidget(self._pl_content)
        pl_l.addWidget(self._pl_scroll, 1)
        self._right_stack.addWidget(pl_w)

        rl.addWidget(self._right_stack, 1)
        root.addWidget(right, 2)

        self._show_panel(0)
        self._load_playlists()

    def _mk_icon_btn(self, text, slot, primary=False, small=False):
        btn = QPushButton(text)
        sz  = 52 if primary else (34 if small else 44)
        btn.setFixedSize(sz, sz)
        if primary:
            btn.setStyleSheet(f"""
                QPushButton{{background:{C['spotify']};color:#000;border-radius:26px;
                    font-size:20px;font-weight:bold;}}
                QPushButton:hover{{background:#26ff72;}}
                QPushButton:pressed{{background:#17b84d;}}
            """)
        elif small:
            btn.setStyleSheet(f"""
                QPushButton{{background:transparent;color:{C['dim']};border:none;font-size:16px;}}
                QPushButton:hover{{color:{C['spotify']};}}
            """)
        else:
            btn.setStyleSheet(f"""
                QPushButton{{background:{C['raised']};color:{C['white']};
                    border:1px solid {C['border_hi']};border-radius:22px;font-size:18px;}}
                QPushButton:hover{{border-color:{C['spotify']};color:{C['spotify']};}}
                QPushButton:pressed{{background:{C['surface']};}}
            """)
        btn.clicked.connect(slot)
        return btn

    def _mk_tab_btn(self, text, slot):
        btn = QPushButton(text)
        btn.setFixedHeight(28)
        btn.setStyleSheet(f"""
            QPushButton{{background:transparent;color:{C['dim']};
                border-bottom:2px solid transparent;border-top:none;
                border-left:none;border-right:none;font-size:11px;padding:0 8px;}}
            QPushButton:hover{{color:{C['white']};border-bottom-color:{C['dim']};}}
        """)
        btn.clicked.connect(slot)
        return btn

    def _show_panel(self, idx):
        self._right_stack.setCurrentIndex(idx)

    def _add_queue_item(self, title, artist, is_current=False):
        row = QWidget()
        rl  = QHBoxLayout(row)
        rl.setContentsMargins(6, 6, 6, 6)
        rl.setSpacing(10)
        dot = QLabel("▶" if is_current else "·")
        dot.setFixedWidth(14)
        dot.setStyleSheet(f"color:{C['spotify'] if is_current else C['muted']}; font-size:10px;")
        t_lbl = QLabel(title)
        t_lbl.setStyleSheet(f"color:{C['white'] if is_current else C['dim']}; font-size:11px;{'font-weight:bold;' if is_current else ''}")
        a_lbl = QLabel(artist)
        a_lbl.setStyleSheet(f"color:{C['muted']}; font-size:10px;")
        a_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        rl.addWidget(dot); rl.addWidget(t_lbl, 1); rl.addWidget(a_lbl)
        row.setStyleSheet(f"background:{C['raised'] if is_current else 'transparent'}; border-radius:6px;")
        self._queue_vbox.insertWidget(self._queue_vbox.count()-1, row)

    def _load_playlists(self):
        if not self._sp: return
        try:
            pls = self._sp.current_user_playlists(limit=20)["items"]
            self._playlist_cache = pls
            for pl in pls:
                btn = QPushButton(pl["name"])
                btn.setStyleSheet(f"""
                    QPushButton{{background:transparent;color:{C['dim']};
                        border:none;text-align:left;font-size:11px;padding:6px 4px;}}
                    QPushButton:hover{{color:{C['white']};}}
                """)
                uri = pl["uri"]
                btn.clicked.connect(lambda _, u=uri: self._play_playlist(u))
                self._pl_vbox.insertWidget(self._pl_vbox.count()-1, btn)
        except Exception:
            pass

    def _play_playlist(self, uri):
        if not self._sp: return
        try: self._sp.start_playback(context_uri=uri); QTimer.singleShot(600, self._poll)
        except Exception: pass

    def _poll(self):
        if not self._sp or self._blocked: return
        try:
            pb = self._sp.current_playback()
            if pb and pb.get("item"):
                item    = pb["item"]
                playing = pb["is_playing"]
                pos_ms  = pb.get("progress_ms", 0) or 0
                dur_ms  = item["duration_ms"]
                artists = ", ".join(a["name"] for a in item["artists"])
                album   = item.get("album",{}).get("name","")
                self._track_lbl.setText(item["name"])
                self._artist_lbl.setText(artists)
                self._album_lbl.setText(album)
                self._prog_bar.setValue(int(pos_ms/dur_ms*1000))
                self._pos_lbl.setText(self._fmt(pos_ms))
                self._dur_lbl.setText(self._fmt(dur_ms))
                self._play_btn.setText("⏸" if playing else "▶")
                self._vinyl.set_playing(playing)
                try:
                    v = pb.get("device",{}).get("volume_percent",60)
                    self._vol_slider.blockSignals(True)
                    self._vol_slider.setValue(v)
                    self._vol_slider.blockSignals(False)
                except Exception: pass
            else:
                self._track_lbl.setText("Nothing playing")
                self._vinyl.set_playing(False)
        except Exception as e:
            if "403" in str(e) or "premium" in str(e).lower():
                self._blocked = True
                self._track_lbl.setText("Premium activating...")
                self._artist_lbl.setText("Check back in ~2 hours")
                QTimer.singleShot(1800000, lambda: setattr(self,"_blocked",False))

    @staticmethod
    def _fmt(ms):
        s = ms//1000; return f"{s//60}:{s%60:02d}"

    def _toggle(self):
        if not self._sp: return
        try:
            pb = self._sp.current_playback()
            if pb and pb["is_playing"]: self._sp.pause_playback(); self._play_btn.setText("▶")
            else: self._sp.start_playback(); self._play_btn.setText("⏸")
        except Exception: pass

    def _next(self):
        if not self._sp: return
        try: self._sp.next_track(); QTimer.singleShot(700, self._poll)
        except Exception: pass

    def _prev(self):
        if not self._sp: return
        try: self._sp.previous_track(); QTimer.singleShot(700, self._poll)
        except Exception: pass

    def _shuffle(self):
        if not self._sp: return
        try:
            pb = self._sp.current_playback()
            cur = pb.get("shuffle_state", False) if pb else False
            self._sp.shuffle(not cur)
            self._shuffle_btn.setStyleSheet(self._shuffle_btn.styleSheet().replace(
                C["spotify"] if not cur else C["dim"],
                C["dim"] if not cur else C["spotify"]
            ))
        except Exception: pass

    def _repeat(self):
        if not self._sp: return
        try:
            pb  = self._sp.current_playback()
            cur = pb.get("repeat_state","off") if pb else "off"
            nxt = {"off":"context","context":"track","track":"off"}[cur]
            self._sp.repeat(nxt)
        except Exception: pass

    def _toggle_like(self):
        self._liked = not self._liked
        self._like_btn.setText("♥" if self._liked else "♡")
        self._like_btn.setStyleSheet(f"QPushButton{{background:transparent;color:{C['crimson'] if self._liked else C['dim']};border:none;font-size:18px;}} QPushButton:hover{{color:{C['crimson']};}} ")

    def _set_vol(self, v):
        if not self._sp: return
        try: self._sp.volume(v)
        except Exception: pass

    def update_from_packet(self, pkt):
        if "track"  in pkt: self._track_lbl.setText(pkt["track"])
        if "artist" in pkt: self._artist_lbl.setText(pkt["artist"])
        if "progress" in pkt: self._prog_bar.setValue(int(pkt["progress"]*1000))


# ─────────────────────────────────────────────────────────────
# CLIMATE TAB
# ─────────────────────────────────────────────────────────────
class ClimateTab(QWidget):
    def __init__(self):
        super().__init__()
        self._temp = 24; self._ac = False; self._fan = 3
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 32, 48, 32)
        layout.setSpacing(24)

        title = QLabel("CLIMATE")
        title.setStyleSheet(f"color:{C['dim']}; font-size:10px; letter-spacing:4px;")
        layout.addWidget(title)

        # AC toggle + Temp
        top_row = QHBoxLayout()
        top_row.setSpacing(32)

        ac_col = QVBoxLayout()
        ac_lbl = QLabel("A/C")
        ac_lbl.setStyleSheet(f"color:{C['dim']}; font-size:9px; letter-spacing:2px;")
        self._ac_btn = QPushButton("OFF")
        self._ac_btn.setFixedSize(80, 80)
        self._ac_btn.clicked.connect(self._toggle_ac)
        ac_col.addWidget(ac_lbl, alignment=Qt.AlignCenter)
        ac_col.addWidget(self._ac_btn, alignment=Qt.AlignCenter)

        temp_col = QVBoxLayout()
        t_lbl = QLabel("TEMPERATURE")
        t_lbl.setStyleSheet(f"color:{C['dim']}; font-size:9px; letter-spacing:2px;")
        t_lbl.setAlignment(Qt.AlignCenter)
        temp_row = QHBoxLayout()
        self._m_btn = QPushButton("−")
        self._m_btn.setFixedSize(44, 44)
        self._p_btn = QPushButton("+")
        self._p_btn.setFixedSize(44, 44)
        self._temp_lbl = QLabel(f"{self._temp}°")
        self._temp_lbl.setAlignment(Qt.AlignCenter)
        self._temp_lbl.setStyleSheet(f"color:{C['cyan']}; font-size:56px; font-weight:bold; font-family:{FONT_DATA};")
        for b in [self._m_btn, self._p_btn]:
            b.setStyleSheet(f"""
                QPushButton{{background:{C['raised']};color:{C['cyan']};
                    border:1px solid {C['border_hi']};border-radius:22px;
                    font-size:22px;font-weight:bold;}}
                QPushButton:hover{{background:{C['hover']};border-color:{C['cyan']};}}
            """)
        self._m_btn.clicked.connect(lambda: self._adj(-1))
        self._p_btn.clicked.connect(lambda: self._adj(+1))
        temp_row.addWidget(self._m_btn)
        temp_row.addWidget(self._temp_lbl, 1)
        temp_row.addWidget(self._p_btn)
        temp_col.addWidget(t_lbl)
        temp_col.addLayout(temp_row)

        top_row.addLayout(ac_col)
        top_row.addLayout(temp_col, 1)
        layout.addLayout(top_row)

        # Fan
        fan_lbl = QLabel("FAN SPEED")
        fan_lbl.setStyleSheet(f"color:{C['dim']}; font-size:9px; letter-spacing:2px;")
        layout.addWidget(fan_lbl)
        fan_row = QHBoxLayout()
        fan_row.setSpacing(8)
        self._fan_btns = []
        for i in range(1, 6):
            fb = QPushButton(str(i))
            fb.setFixedSize(48, 48)
            self._fan_btns.append(fb)
            fb.clicked.connect(lambda _, n=i: self._set_fan(n))
            fan_row.addWidget(fb)
        fan_row.addStretch()
        layout.addLayout(fan_row)
        layout.addStretch()
        self._refresh()

    def _toggle_ac(self):
        self._ac = not self._ac; self._refresh()

    def _adj(self, d):
        self._temp = max(16, min(30, self._temp+d))
        self._temp_lbl.setText(f"{self._temp}°")

    def _set_fan(self, n):
        self._fan = n; self._refresh()

    def _refresh(self):
        if self._ac:
            self._ac_btn.setStyleSheet(f"""
                QPushButton{{background:{C['cyan_bg']};color:{C['cyan']};
                    border:2px solid {C['cyan']};border-radius:40px;
                    font-size:14px;font-weight:bold;letter-spacing:2px;}}
                QPushButton:hover{{background:{C['hover']};}}
            """)
            self._ac_btn.setText("ON")
        else:
            self._ac_btn.setStyleSheet(f"""
                QPushButton{{background:{C['surface']};color:{C['dim']};
                    border:1px solid {C['border']};border-radius:40px;
                    font-size:14px;font-weight:bold;letter-spacing:2px;}}
                QPushButton:hover{{border-color:{C['cyan']};color:{C['cyan']};}}
            """)
            self._ac_btn.setText("OFF")
        for i, fb in enumerate(self._fan_btns):
            active = (i+1 <= self._fan)
            fb.setStyleSheet(f"""
                QPushButton{{background:{C['cyan_bg'] if active else C['surface']};
                    color:{C['cyan'] if active else C['dim']};
                    border:1px solid {C['cyan'] if active else C['border']};
                    border-radius:24px;font-size:13px;font-weight:bold;}}
                QPushButton:hover{{border-color:{C['cyan']};color:{C['cyan']};}}
            """)

    def set_from_bridge(self, ac_on, temp):
        self._ac = ac_on; self._temp = temp
        self._temp_lbl.setText(f"{temp}°")
        self._refresh()


# ─────────────────────────────────────────────────────────────
# MAIN WINDOW
# ─────────────────────────────────────────────────────────────
class Cockpit(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DRAVIXA")
        self.resize(1440, 900)
        self.setMinimumSize(1200, 720)
        self._spd = 0.0; self._tspd = 0.0
        self._rpm = 0.8; self._trpm = 0.8
        self._gear = "P"

        self._bridge = Bridge()
        self._bridge.chat_sig.connect(self._on_chat)
        self._bridge.status_sig.connect(self._on_status)
        self._bridge.spotify_sig.connect(self._on_spotify)
        self._bridge.vehicle_sig.connect(self._on_vehicle)
        self._bridge.navigate_sig.connect(self._on_navigate)
        self._bridge.adas_sig.connect(self._on_adas)
        self._bridge.alert_sig.connect(self._on_alert)

        self._build()
        self._apply_styles()

        t = QTimer(self); t.timeout.connect(self._sim); t.start(16)

    def _build(self):
        root = QWidget(); self.setCentralWidget(root)
        vl = QVBoxLayout(root)
        vl.setContentsMargins(0,0,0,0); vl.setSpacing(0)

        # Sys bar
        self._sys = SysBar()
        self._sys.setStyleSheet(f"background:{C['void']}; border-bottom:1px solid {C['border']};")
        vl.addWidget(self._sys)

        # ADAS bar
        self._adas = AdasBar()
        self._adas.setStyleSheet(f"background:{C['deep']}; border-bottom:1px solid {C['border']};")
        vl.addWidget(self._adas)

        # Main row
        row = QHBoxLayout(); row.setContentsMargins(0,0,0,0); row.setSpacing(0)

        # Sidebar
        row.addWidget(self._build_sidebar())

        # Tabs
        self._tabs = QStackedWidget()
        self._tabs.addWidget(self._build_dash())
        self._tabs.addWidget(self._build_media())
        self._tabs.addWidget(self._build_map())
        self._tabs.addWidget(self._build_climate())
        self._tabs.setCurrentIndex(0)
        row.addWidget(self._tabs, 1)

        # Right panel
        row.addWidget(self._build_right())

        mw = QWidget(); mw.setLayout(row)
        vl.addWidget(mw, 1)

    def _build_sidebar(self):
        sb = QFrame(); sb.setFixedWidth(70); sb.setObjectName("sidebar")
        l = QVBoxLayout(sb); l.setContentsMargins(8,20,8,20); l.setSpacing(6)

        logo = QLabel("◈"); logo.setAlignment(Qt.AlignCenter)
        logo.setStyleSheet(f"color:{C['cyan']}; font-size:26px; padding:8px 0 22px 0;")
        l.addWidget(logo)

        self._nav_btns = []
        for icon, tip, idx in [("⊙","Dashboard",0),("♫","Media",1),("◎","Navigate",2),("❄","Climate",3)]:
            btn = QPushButton(icon); btn.setToolTip(tip); btn.setFixedSize(50,46)
            btn.setObjectName("nav_btn")
            btn.clicked.connect(lambda _, i=idx: self._switch(i))
            l.addWidget(btn, alignment=Qt.AlignCenter)
            self._nav_btns.append(btn)

        l.addStretch()
        self._ai_dot = QLabel("●"); self._ai_dot.setAlignment(Qt.AlignCenter)
        self._ai_dot.setStyleSheet(f"color:{C['muted']}; font-size:14px;")
        l.addWidget(self._ai_dot)
        al = QLabel("AI"); al.setAlignment(Qt.AlignCenter)
        al.setStyleSheet(f"color:{C['muted']}; font-size:9px; letter-spacing:1px;")
        l.addWidget(al)
        return sb

    def _build_dash(self):
        pg = QWidget(); pg.setObjectName("tab_page")
        l = QVBoxLayout(pg); l.setContentsMargins(28,16,28,16); l.setSpacing(14)

        # Info cards
        cards_row = QHBoxLayout(); cards_row.setSpacing(10)
        self._cards = {}
        for title, val, key, color in [
            ("GEAR","P","gear",C["cyan"]),
            ("RANGE","380 km","range",C["green"]),
            ("FOCUS","95%","focus",C["purple"]),
            ("TRIP","12,458 km","trip",C["dim"]),
        ]:
            card = QFrame(); card.setObjectName("info_card")
            cl = QVBoxLayout(card); cl.setContentsMargins(12,8,12,8)
            tl = QLabel(title)
            tl.setStyleSheet(f"color:{C['dim']}; font-size:9px; letter-spacing:2px;")
            tl.setAlignment(Qt.AlignCenter)
            vl2 = QLabel(val)
            vl2.setStyleSheet(f"color:{color}; font-size:22px; font-weight:bold; font-family:{FONT_DATA};")
            vl2.setAlignment(Qt.AlignCenter)
            cl.addWidget(tl); cl.addWidget(vl2)
            cards_row.addWidget(card)
            self._cards[key] = vl2
        l.addLayout(cards_row)

        # Gauges + car visual side by side
        gr = QHBoxLayout(); gr.setSpacing(20)
        self._spd_gauge = HoloGauge("Speed","km/h",240)
        self._rpm_gauge = HoloGauge("Engine","×1000 RPM",8,decimals=1,rpm=True)

        gauges_col = QVBoxLayout(); gauges_col.setSpacing(16)
        gauges_col.addWidget(self._spd_gauge)
        gauges_col.addWidget(self._rpm_gauge)
        gauges_w = QWidget(); gauges_w.setLayout(gauges_col)

        # BYD/XPeng-style live vehicle visualization — lock, charging,
        # lights, door-ajar status, plus an ADAS sensing bubble
        self._car = CarVisual()
        self._car.set_status(locked=True, charging=False, lights=False,
                              doors_open=[], adas=True, charge_pct=72)

        gr.addWidget(gauges_w, 1)
        gr.addWidget(self._car, 1)
        l.addLayout(gr, 1)
        return pg

    def _build_media(self):
        self._media_tab = MediaTab()
        return self._media_tab

    def _build_map(self):
        self._map = MapPanel()
        return self._map

    def _build_climate(self):
        self._climate = ClimateTab()
        return self._climate

    def _build_right(self):
        p = QFrame(); p.setFixedWidth(300); p.setObjectName("right_panel")
        l = QVBoxLayout(p); l.setContentsMargins(14,16,14,14); l.setSpacing(10)

        hdr = QHBoxLayout()
        hl = QLabel("DRAVIXA AI")
        hl.setStyleSheet(f"color:{C['cyan']}; font-size:12px; font-weight:bold; letter-spacing:2px;")
        self._state_lbl = QLabel("idle")
        self._state_lbl.setStyleSheet(f"color:{C['muted']}; font-size:9px;")
        hdr.addWidget(hl); hdr.addStretch(); hdr.addWidget(self._state_lbl)
        l.addLayout(hdr)

        # NOMI-style animated robot face — the visual identity of Dravixa,
        # eye color is the primary glanceable AI-state signal
        self._avatar = DravixaAvatar()
        self._avatar.setFixedHeight(180)
        l.addWidget(self._avatar)

        self._wave = AIWave()
        l.addWidget(self._wave)

        div = QFrame(); div.setFixedHeight(1); div.setStyleSheet(f"background:{C['border']};")
        l.addWidget(div)

        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._chat_w = QWidget()
        self._chat_l = QVBoxLayout(self._chat_w)
        self._chat_l.setSpacing(4); self._chat_l.addStretch()
        self._scroll.setWidget(self._chat_w)
        l.addWidget(self._scroll, 1)

        qa_lbl = QLabel("QUICK")
        qa_lbl.setStyleSheet(f"color:{C['muted']}; font-size:9px; letter-spacing:1px;")
        l.addWidget(qa_lbl)
        qa = QHBoxLayout(); qa.setSpacing(5)
        for label, prompt in [("Traffic","Check traffic"),("Weather","Weather now"),("Fuel","Nearest fuel")]:
            btn = QPushButton(label); btn.setFixedHeight(28)
            btn.setStyleSheet(f"""
                QPushButton{{background:{C['surface']};color:{C['dim']};
                    border:1px solid {C['border_hi']};border-radius:5px;font-size:10px;}}
                QPushButton:hover{{border-color:{C['cyan']};color:{C['cyan']};}}
            """)
            btn.clicked.connect(lambda _, t=prompt: self._send(t))
            qa.addWidget(btn)
        l.addLayout(qa)

        inp = QHBoxLayout(); inp.setSpacing(6)
        self._inp = QLineEdit(); self._inp.setPlaceholderText("Ask Dravixa...")
        self._inp.setFixedHeight(36); self._inp.returnPressed.connect(lambda: self._send())
        sb2 = QPushButton("➤"); sb2.setFixedSize(36,36); sb2.setObjectName("send_btn")
        sb2.clicked.connect(lambda: self._send())
        inp.addWidget(self._inp); inp.addWidget(sb2)
        l.addLayout(inp)
        return p

    # ── Navigation ────────────────────────────────────────────
    TABS = ["DASHBOARD","MEDIA","NAVIGATE","CLIMATE"]
    def _switch(self, idx):
        self._tabs.setCurrentIndex(idx)
        for i, b in enumerate(self._nav_btns):
            b.setProperty("active", i==idx)
            b.style().unpolish(b); b.style().polish(b)

    # ── Bridge handlers ───────────────────────────────────────
    def _on_chat(self, text, is_user):
        b = Bubble(text, is_user)
        self._chat_l.insertWidget(self._chat_l.count()-1, b)
        QTimer.singleShot(60, lambda: self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()))
        if not is_user:
            self._set_state("speaking")
            QTimer.singleShot(3500, lambda: self._set_state("idle"))

    def _on_status(self, pkt):
        if pkt.get("processing"):   self._set_state("processing")
        elif pkt.get("speaking"):   self._set_state("speaking")
        elif pkt.get("awake"):      self._set_state("listening")
        else:                       self._set_state("idle")

    def _on_alert(self, pkt):
        """Driver fatigue/phone-distraction warning from drivermonitor.py
        (relayed through dravixa_final.py's mood_bridge_worker). Shows
        the robot's worried face for a few seconds, then returns to
        whatever state the AI conversation is actually in."""
        level = pkt.get("alert_level", "")
        self._set_state("alert")
        hold_ms = 4000 if level == "CRITICAL" else 2500
        QTimer.singleShot(hold_ms, lambda: self._set_state("idle"))
        msg = pkt.get("message", "")
        if msg:
            self.show_toast(msg, "warning" if level == "WARNING" else "error")

    def _on_spotify(self, pkt): self._media_tab.update_from_packet(pkt)

    def _on_vehicle(self, pkt):
        if "speed" in pkt: self._spd_gauge.set_value(pkt["speed"])
        if "rpm"   in pkt: self._rpm_gauge.set_value(pkt["rpm"])
        if "gear"  in pkt: self._cards["gear"].setText(pkt["gear"])
        if "ac_on" in pkt and "temp" in pkt:
            self._climate.set_from_bridge(pkt["ac_on"], pkt["temp"])

    def _on_navigate(self, pkt):
    	self._switch(2)
    	markers = pkt.get("markers", [])
    	if pkt.get("places"):
    	    self._map.show_places(pkt["places"], markers)
    	    self.show_toast(f"Searching nearby {_place_label(pkt['places'])}", "info")
    	else:
    	    self._map.show_route(
    	    	pkt.get("origin_lat", -6.1781),
    	    	pkt.get("origin_lng", 106.630),
    	    	pkt.get("dest_lat"),
    	    	pkt.get("dest_lng"),
    	    	pkt.get("destination", "Destination"))
    	    self.show_toast(
    	    	f"→  {pkt.get('destination')}  ·  "
    	    	f"{pkt.get('distance_km')} km  ·  "
    	    	f"{pkt.get('duration_min')} min", "ok")

    def _on_adas(self, pkt): self._adas.update_adas(pkt)

    def _set_state(self, s):
        self._wave.set_state(s)
        self._avatar.set_state(s)   # drive the robot face expression
        self._state_lbl.setText(s)
        cols = {
            "idle":       C["muted"],
            "listening":  C["green"],
            "speaking":   C["cyan"],
            "processing": C["amber"],
        }
        col = cols.get(s, C["muted"])
        self._ai_dot.setStyleSheet(f"color:{col}; font-size:14px;")

    def _send(self, text=None):
        text = text or self._inp.text().strip()
        if not text: return
        self._inp.clear()
        b = Bubble(text, True)
        self._chat_l.insertWidget(self._chat_l.count()-1, b)
        QTimer.singleShot(60, lambda: self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()))
        self._set_state("processing")
        QTimer.singleShot(800, lambda: (self._on_chat("Got it! 🚗", False), self._set_state("idle")))

    def show_toast(self, text, level="info"):
        Toast(self.centralWidget(), text, level)

    # ── Simulation ────────────────────────────────────────────
    def _sim(self):
        if random.random() > 0.97:
            self._tspd = random.uniform(0,160); self._trpm = random.uniform(0.8,6.5)
        self._spd += (self._tspd-self._spd)*0.05
        self._rpm += (self._trpm-self._rpm)*0.05
        g = ("P" if self._spd<4 else "1" if self._spd<18 else "2" if self._spd<40
             else "3" if self._spd<75 else "4" if self._spd<110 else "5")
        if g != self._gear:
            self._gear = g; self._cards["gear"].setText(g)
        self._spd_gauge.set_value(self._spd)
        self._rpm_gauge.set_value(self._rpm)

    # ── Styles ────────────────────────────────────────────────
    def _apply_styles(self):
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{
                background-color:{C['deep']}; color:{C['white']};
                font-family:{FONT_UI}; font-size:13px;
            }}
            #sidebar {{ background:{C['void']}; border-right:1px solid {C['border']}; }}
            #right_panel {{ background:{C['void']}; border-left:1px solid {C['border']}; }}
            #tab_page {{ background:{C['deep']}; }}
            #media_left {{ background:{C['deep']}; }}
            #media_right {{ background:{C['surface']}; }}
            #nav_btn {{
                background:transparent; border:none; border-radius:10px;
                color:{C['dim']}; font-size:22px;
            }}
            #nav_btn:hover {{ background:{C['surface']}; color:{C['cyan']}; }}
            #nav_btn[active=true] {{
                background:{C['surface']}; color:{C['cyan']};
                border-right:2px solid {C['cyan']};
            }}
            #info_card {{
                background:{C['surface']}; border:1px solid {C['border']};
                border-radius:10px;
            }}
            QLineEdit {{
                background:{C['surface']}; border:1px solid {C['border_hi']};
                border-radius:8px; padding:6px 12px; color:{C['white']}; font-size:12px;
            }}
            QLineEdit:focus {{ border-color:{C['cyan']}; }}
            #send_btn {{
                background:{C['cyan_bg']}; border:1px solid {C['cyan']};
                border-radius:8px; color:{C['cyan']}; font-size:14px; font-weight:bold;
            }}
            #send_btn:hover {{ background:{C['cyan']}; color:{C['void']}; }}
            QScrollBar:vertical {{
                background:{C['void']}; width:3px; border-radius:1px;
            }}
            QScrollBar::handle:vertical {{
                background:{C['border_hi']}; border-radius:1px;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
            QScrollArea {{ border:none; }}
            QProgressBar {{ background:{C['border']}; border-radius:2px; border:none; }}
            QProgressBar::chunk {{ background:{C['cyan']}; border-radius:2px; }}
        """)


# ─────────────────────────────────────────────────────────────
# ENTRY
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    pal = app.palette()
    pal.setColor(QPalette.Window,      QColor(C["deep"]))
    pal.setColor(QPalette.WindowText,  QColor(C["white"]))
    pal.setColor(QPalette.Base,        QColor(C["surface"]))
    pal.setColor(QPalette.Text,        QColor(C["white"]))
    pal.setColor(QPalette.Button,      QColor(C["surface"]))
    pal.setColor(QPalette.ButtonText,  QColor(C["white"]))
    pal.setColor(QPalette.Highlight,   QColor(C["cyan"]))
    app.setPalette(pal)

    win = Cockpit()
    win._on_chat("DRAVIXA v4.0 online. All systems nominal.", False)
    win._on_chat("Say 'Hello Toyota' to activate voice assistant.", False)
    win.show()
    sys.exit(app.exec_())
