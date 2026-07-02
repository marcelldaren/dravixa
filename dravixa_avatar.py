"""
DravixaAvatar — a NOMI/Xiao-P-inspired animated robot face for the HMI.

Design research notes (BYD / XPeng / NIO NOMI):
  - NIO's NOMI tested best with users precisely BECAUSE it's simple: a
    round face that can turn and make eye contact. Detailed avatars with
    arms/legs felt incongruous in a digital space. So: round head, big
    expressive eyes, NO limbs.
  - The avatar should feel ALIVE even when idle (subtle breathing, blinks,
    occasional glances) — a static face reads as "off".
  - It must clearly signal AI state (idle / listening / speaking /
    processing / happy / alert) at a glance, since the driver only looks
    for a fraction of a second.
  - Eye color is the primary state signal (NOMI uses this too) — calm
    cyan idle, green listening, warm amber thinking, etc.
"""

import sys, math, random
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt5.QtGui import (QPainter, QColor, QBrush, QPen, QRadialGradient,
                         QLinearGradient, QPainterPath)

# Palette (matches the HMI's C dict)
_C = {
    "void": "#020408", "surface": "#080E1A", "raised": "#0C1524",
    "cyan": "#00C8FF", "cyan2": "#00A8D8", "crimson": "#FF2952",
    "amber": "#FFA820", "green": "#00E896", "purple": "#A78BFA",
    "white": "#EAF2FF", "dim": "#4A6A88",
}


class DravixaAvatar(QWidget):
    """Animated robot face. Call set_state(s) where s is one of:
    idle, listening, speaking, processing, happy, alert, sleeping."""

    # Eye accent color per state — the primary glanceable status signal
    _STATE_COLOR = {
        "idle":       _C["cyan"],
        "listening":  _C["green"],
        "speaking":   _C["cyan"],
        "processing": _C["amber"],
        "happy":      _C["green"],
        "alert":      _C["crimson"],
        "sleeping":   _C["dim"],
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(220, 200)
        self._state       = "idle"
        self._t           = 0.0          # master animation clock
        self._blink       = 0.0          # 0 = open, 1 = fully closed
        self._blink_timer = 0.0
        self._next_blink  = random.uniform(2.0, 5.0)
        self._glance_x    = 0.0          # -1..1 horizontal eye look
        self._glance_y    = 0.0
        self._glance_tgt  = (0.0, 0.0)
        self._glance_timer = 0.0
        self._next_glance = random.uniform(3.0, 6.0)
        self._mouth_open  = 0.0          # speaking mouth animation
        self._color       = QColor(_C["cyan"])
        self._color_tgt   = QColor(_C["cyan"])
        self._pulse       = 0.0

        t = QTimer(self)
        t.timeout.connect(self._tick)
        t.start(33)  # ~30fps

    def set_state(self, s):
        if s not in self._STATE_COLOR:
            s = "idle"
        self._state     = s
        self._color_tgt = QColor(self._STATE_COLOR[s])

    # ── animation driver ──────────────────────────────────────
    def _tick(self):
        dt = 0.033
        self._t += dt

        # smooth color toward target
        self._color = QColor(
            int(self._color.red()   + (self._color_tgt.red()   - self._color.red())   * 0.12),
            int(self._color.green() + (self._color_tgt.green() - self._color.green()) * 0.12),
            int(self._color.blue()  + (self._color_tgt.blue()  - self._color.blue())  * 0.12),
        )

        # ── blinking ──
        self._blink_timer += dt
        if self._blink_timer >= self._next_blink and self._blink == 0.0:
            self._blink = 0.001  # start a blink
        if self._blink > 0.0:
            # quick close-open over ~0.16s
            self._blink += dt * 14.0
            if self._blink >= 2.0:
                self._blink = 0.0
                self._blink_timer = 0.0
                self._next_blink = random.uniform(2.0, 5.5)

        # ── idle glancing (eye wander) ──
        self._glance_timer += dt
        if self._glance_timer >= self._next_glance:
            self._glance_timer = 0.0
            self._next_glance = random.uniform(3.0, 6.0)
            if self._state in ("idle", "listening"):
                self._glance_tgt = (random.uniform(-0.5, 0.5),
                                    random.uniform(-0.3, 0.3))
            else:
                self._glance_tgt = (0.0, 0.0)
        self._glance_x += (self._glance_tgt[0] - self._glance_x) * 0.08
        self._glance_y += (self._glance_tgt[1] - self._glance_y) * 0.08

        # ── speaking mouth ──
        if self._state == "speaking":
            self._mouth_open = 0.5 + 0.5 * abs(math.sin(self._t * 11.0))
        else:
            self._mouth_open += (0.0 - self._mouth_open) * 0.2

        # ── pulse (processing ring / listening glow) ──
        self._pulse = 0.5 + 0.5 * math.sin(self._t * 3.2)

        self.update()

    # ── helpers ───────────────────────────────────────────────
    def _blink_amount(self):
        # convert 0..2 sawtooth into 0..1..0 close amount
        b = self._blink
        if b == 0.0:
            base = 0.0
        elif b <= 1.0:
            base = b
        else:
            base = 2.0 - b
        # sleeping = eyes nearly shut and slow
        if self._state == "sleeping":
            base = max(base, 0.82 + 0.05 * math.sin(self._t * 1.5))
        return min(base, 1.0)

    # ── paint ─────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        cx, cy = W / 2, H / 2
        # head radius scales to widget
        R = min(W, H) * 0.42
        col = self._color

        # ── ambient glow behind head (state-tinted) ──
        glow_r = R * (1.7 + 0.12 * self._pulse if self._state in ("listening", "processing", "alert") else 1.55)
        gg = QRadialGradient(cx, cy, glow_r)
        gc = QColor(col); gc.setAlpha(55)
        gg.setColorAt(0.0, gc)
        gc2 = QColor(col); gc2.setAlpha(0)
        gg.setColorAt(1.0, gc2)
        p.setPen(Qt.NoPen); p.setBrush(QBrush(gg))
        p.drawEllipse(QPointF(cx, cy), glow_r, glow_r)

        # ── processing orbit dots ──
        if self._state == "processing":
            for i in range(3):
                a = self._t * 2.5 + i * (2 * math.pi / 3)
                ox = cx + math.cos(a) * R * 1.45
                oy = cy + math.sin(a) * R * 1.45
                dc = QColor(col); dc.setAlpha(220)
                p.setBrush(QBrush(dc)); p.setPen(Qt.NoPen)
                p.drawEllipse(QPointF(ox, oy), 5, 5)

        # ── head body: glossy dark capsule with rim light ──
        head_rect = QRectF(cx - R, cy - R * 0.95, R * 2, R * 1.9)
        body = QRadialGradient(cx, cy - R * 0.3, R * 1.6)
        body.setColorAt(0.0, QColor("#16243A"))
        body.setColorAt(0.6, QColor("#0C1524"))
        body.setColorAt(1.0, QColor("#060B14"))
        p.setBrush(QBrush(body))
        rim = QColor(col); rim.setAlpha(140)
        p.setPen(QPen(rim, 2.5))
        p.drawRoundedRect(head_rect, R * 0.7, R * 0.7)

        # subtle top highlight
        hl = QLinearGradient(cx, cy - R, cx, cy)
        hlc = QColor("#FFFFFF"); hlc.setAlpha(18)
        hl.setColorAt(0.0, hlc)
        hl.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.setBrush(QBrush(hl)); p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(cx - R * 0.8, cy - R * 0.85, R * 1.6, R * 0.9),
                          R * 0.5, R * 0.5)

        # ── eyes ──
        eye_dx   = R * 0.42          # horizontal offset from center
        eye_y    = cy - R * 0.08
        eye_w    = R * 0.46
        eye_h    = R * 0.60
        blink    = self._blink_amount()
        gx       = self._glance_x * R * 0.10
        gy       = self._glance_y * R * 0.10

        for sign in (-1, 1):
            ex = cx + sign * eye_dx + gx
            ey = eye_y + gy

            # eye socket glow
            sg = QRadialGradient(ex, ey, eye_w)
            sgc = QColor(col); sgc.setAlpha(90)
            sg.setColorAt(0.0, sgc)
            sg.setColorAt(1.0, QColor(col.red(), col.green(), col.blue(), 0))
            p.setBrush(QBrush(sg)); p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(ex, ey), eye_w * 0.9, eye_h * 0.9)

            # the eye itself — a rounded vertical capsule, squashed by blink
            open_h = eye_h * (1.0 - blink)
            if self._state == "happy":
                # happy = upward arc (^ ^) eyes
                p.setPen(QPen(col, max(3, R * 0.10)))
                p.setBrush(Qt.NoBrush)
                arc = QRectF(ex - eye_w * 0.6, ey - eye_h * 0.2,
                             eye_w * 1.2, eye_h * 0.9)
                p.drawArc(arc, 20 * 16, 140 * 16)
            elif open_h < 3:
                # fully blinked — a thin line
                p.setPen(QPen(col, max(2, R * 0.06)))
                p.drawLine(QPointF(ex - eye_w * 0.45, ey),
                           QPointF(ex + eye_w * 0.45, ey))
            else:
                eg = QRadialGradient(ex, ey - open_h * 0.15, eye_w * 0.8)
                eg.setColorAt(0.0, QColor("#FFFFFF"))
                eg.setColorAt(0.35, col.lighter(140))
                eg.setColorAt(1.0, col)
                p.setBrush(QBrush(eg))
                p.setPen(Qt.NoPen)
                p.drawRoundedRect(
                    QRectF(ex - eye_w / 2, ey - open_h / 2, eye_w, open_h),
                    eye_w / 2, eye_w / 2)
                # bright catchlight
                p.setBrush(QBrush(QColor(255, 255, 255, 230)))
                p.drawEllipse(QPointF(ex - eye_w * 0.16, ey - open_h * 0.18),
                              eye_w * 0.13, eye_w * 0.13)

        # ── alert eyebrows (angry V) when alert ──
        if self._state == "alert":
            p.setPen(QPen(col, max(3, R * 0.10), Qt.SolidLine, Qt.RoundCap))
            for sign in (-1, 1):
                ex = cx + sign * eye_dx
                p.drawLine(
                    QPointF(ex - sign * eye_w * 0.55, eye_y - eye_h * 0.42),
                    QPointF(ex + sign * eye_w * 0.45, eye_y - eye_h * 0.20))

        # ── mouth ──
        my = cy + R * 0.62
        mw = R * 0.5
        if self._state == "speaking":
            mh = R * 0.06 + R * 0.34 * self._mouth_open
            mc = QColor(col)
            p.setBrush(QBrush(mc)); p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(cx - mw / 2, my - mh / 2, mw, mh),
                              mh / 2, mh / 2)
        elif self._state == "happy":
            # big smile
            p.setPen(QPen(col, max(3, R * 0.09)))
            p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(cx - mw * 0.7, my - R * 0.4, mw * 1.4, R * 0.7),
                      200 * 16, 140 * 16)
        elif self._state == "alert":
            # small worried O
            p.setBrush(QBrush(col)); p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(cx, my), mw * 0.18, mw * 0.20)
        else:
            # gentle neutral line with a hint of smile
            p.setPen(QPen(QColor(col.red(), col.green(), col.blue(), 200),
                          max(2, R * 0.055)))
            p.setBrush(Qt.NoBrush)
            p.drawArc(QRectF(cx - mw * 0.5, my - R * 0.18, mw, R * 0.32),
                      210 * 16, 120 * 16)

        # ── listening sound rings ──
        if self._state == "listening":
            for i in range(2):
                rr = R * (1.15 + i * 0.22) + self._pulse * R * 0.12
                ac = QColor(col)
                ac.setAlpha(int(70 * (1 - i * 0.4) * (0.5 + 0.5 * self._pulse)))
                p.setPen(QPen(ac, 2)); p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(cx, cy), rr, rr)

        p.end()


# ── standalone render test: dump each state to PNG ──────────────
if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtGui import QImage
    app = QApplication(sys.argv)

    states = ["idle", "listening", "speaking", "processing", "happy", "alert", "sleeping"]
    for st in states:
        w = DravixaAvatar()
        w.resize(260, 240)
        w.set_state(st)
        # advance animation a bit so it's not frame-0
        for _ in range(40):
            w._tick()
        img = QImage(260, 240, QImage.Format_ARGB32)
        img.fill(QColor("#05080F"))
        w.render(img)
        img.save(f"avatar_{st}.png")
        print(f"saved avatar_{st}.png")
