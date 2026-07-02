"""
CarVisual — BYD/XPeng-style top-down vehicle visualization for the HMI.

Design research notes:
  - Chinese EV HMIs (BYD DiLink, XPeng Xmart) show a clean 3/4 or top-down
    car render front-and-center, with live status overlays: doors,
    charging, lights, tyre pressure, and an ADAS "bubble" showing nearby
    detected vehicles/lanes.
  - The car should feel premium: glossy body, soft ground shadow, subtle
    ambient sweep of light. Status changes animate (pulse, glow) rather
    than just toggling.
  - Surrounding ADAS rings/lane lines reinforce the "this car senses the
    world around it" message that's central to Dravixa's pitch.
"""

import sys, math
from PyQt5.QtWidgets import QWidget
from PyQt5.QtCore import Qt, QTimer, QRectF, QPointF
from PyQt5.QtGui import (QPainter, QColor, QBrush, QPen, QLinearGradient,
                         QRadialGradient, QPainterPath)

_C = {
    "void": "#020408", "surface": "#080E1A", "raised": "#0C1524",
    "cyan": "#00C8FF", "cyan2": "#00A8D8", "crimson": "#FF2952",
    "amber": "#FFA820", "green": "#00E896", "purple": "#A78BFA",
    "white": "#EAF2FF", "dim": "#4A6A88",
}


class CarVisual(QWidget):
    """Top-down animated EV with live status. Update via set_status(**kw):
    locked(bool), charging(bool), lights(bool), doors_open(list of
    'fl','fr','rl','rr'), adas(bool)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(240, 320)
        self._t          = 0.0
        self._sweep      = 0.0
        self._locked     = True
        self._charging   = False
        self._lights     = False
        self._doors      = []          # subset of fl,fr,rl,rr
        self._adas       = True
        self._scan       = 0.0         # adas scan sweep 0..1
        self._charge_pct = 72

        t = QTimer(self); t.timeout.connect(self._tick); t.start(33)

    def set_status(self, **kw):
        if "locked"     in kw: self._locked   = kw["locked"]
        if "charging"   in kw: self._charging = kw["charging"]
        if "lights"     in kw: self._lights   = kw["lights"]
        if "doors_open" in kw: self._doors    = kw["doors_open"]
        if "adas"       in kw: self._adas     = kw["adas"]
        if "charge_pct" in kw: self._charge_pct = kw["charge_pct"]

    def _tick(self):
        self._t    += 0.033
        self._sweep = (self._sweep + 0.006) % 1.0
        self._scan  = (self._scan + 0.012) % 1.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        W, H = self.width(), self.height()
        cx = W / 2

        # car body metrics
        cw = min(W * 0.46, H * 0.34)       # car width
        ch = cw * 2.3                       # car length
        cy = H / 2
        body_rect = QRectF(cx - cw / 2, cy - ch / 2, cw, ch)

        # ── ADAS sensing bubble (behind car) ──
        if self._adas:
            for i in range(3):
                rr = cw * (0.95 + i * 0.42)
                a = int(46 * (1 - i * 0.28))
                ac = QColor(_C["cyan"]); ac.setAlpha(a)
                p.setPen(QPen(ac, 1.5)); p.setBrush(Qt.NoBrush)
                p.drawEllipse(QPointF(cx, cy), rr, rr * 1.18)

            # scanning sweep line
            sweep_y = cy - ch * 0.7 + self._scan * ch * 1.4
            grad = QLinearGradient(0, sweep_y - 18, 0, sweep_y + 18)
            grad.setColorAt(0.0, QColor(0, 200, 255, 0))
            grad.setColorAt(0.5, QColor(0, 200, 255, 70))
            grad.setColorAt(1.0, QColor(0, 200, 255, 0))
            p.setPen(Qt.NoPen); p.setBrush(QBrush(grad))
            p.drawRect(QRectF(cx - cw * 1.3, sweep_y - 18, cw * 2.6, 36))

            # nearby detected vehicles (little blips)
            for (bx, by, ph) in [(-1.05, -0.55, 0.0), (1.05, 0.15, 2.0), (-0.95, 0.6, 4.0)]:
                pulse = 0.5 + 0.5 * math.sin(self._t * 2 + ph)
                vx = cx + bx * cw
                vy = cy + by * ch * 0.4
                bc = QColor(_C["amber"]); bc.setAlpha(int(120 + 100 * pulse))
                p.setBrush(QBrush(bc)); p.setPen(Qt.NoPen)
                p.drawRoundedRect(QRectF(vx - cw * 0.16, vy - cw * 0.28,
                                          cw * 0.32, cw * 0.56),
                                  cw * 0.08, cw * 0.08)

        # ── ground shadow ──
        sh = QRadialGradient(cx, cy + ch * 0.1, cw)
        sh.setColorAt(0.0, QColor(0, 0, 0, 130))
        sh.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen); p.setBrush(QBrush(sh))
        p.drawEllipse(QRectF(cx - cw * 0.85, cy - ch * 0.42, cw * 1.7, ch * 0.95))

        # ── car body (glossy, rounded) ──
        body = QLinearGradient(body_rect.left(), 0, body_rect.right(), 0)
        body.setColorAt(0.0, QColor("#0A1220"))
        body.setColorAt(0.5, QColor("#1A2C44"))
        body.setColorAt(1.0, QColor("#0A1220"))
        p.setBrush(QBrush(body))
        rim = QColor(_C["cyan"]); rim.setAlpha(110)
        p.setPen(QPen(rim, 2))
        p.drawRoundedRect(body_rect, cw * 0.32, cw * 0.32)

        # windshield + roof glass (darker inset)
        glass = QRectF(cx - cw * 0.36, cy - ch * 0.28, cw * 0.72, ch * 0.56)
        gg = QLinearGradient(0, glass.top(), 0, glass.bottom())
        gg.setColorAt(0.0, QColor("#16263C"))
        gg.setColorAt(0.5, QColor("#0C1828"))
        gg.setColorAt(1.0, QColor("#16263C"))
        p.setBrush(QBrush(gg)); p.setPen(Qt.NoPen)
        p.drawRoundedRect(glass, cw * 0.18, cw * 0.18)

        # roof-length sheen sweep (animated)
        sweep_pos = self._sweep
        sy = body_rect.top() + sweep_pos * body_rect.height()
        sg = QLinearGradient(0, sy - 30, 0, sy + 30)
        sg.setColorAt(0.0, QColor(255, 255, 255, 0))
        sg.setColorAt(0.5, QColor(255, 255, 255, 22))
        sg.setColorAt(1.0, QColor(255, 255, 255, 0))
        path = QPainterPath()
        path.addRoundedRect(body_rect, cw * 0.32, cw * 0.32)
        p.setClipPath(path)
        p.setBrush(QBrush(sg)); p.setPen(Qt.NoPen)
        p.drawRect(QRectF(body_rect.left(), sy - 30, body_rect.width(), 60))
        p.setClipping(False)

        # ── headlights / taillights ──
        # front (top)
        front_col = QColor(_C["white"]) if self._lights else QColor("#22384E")
        if self._lights:
            # light cones
            for sgn in (-1, 1):
                lx = cx + sgn * cw * 0.28
                cone = QLinearGradient(lx, body_rect.top(), lx, body_rect.top() - ch * 0.35)
                cone.setColorAt(0.0, QColor(255, 245, 200, 90))
                cone.setColorAt(1.0, QColor(255, 245, 200, 0))
                p.setBrush(QBrush(cone)); p.setPen(Qt.NoPen)
                tri = QPainterPath()
                tri.moveTo(lx - cw * 0.10, body_rect.top())
                tri.lineTo(lx - cw * 0.34, body_rect.top() - ch * 0.34)
                tri.lineTo(lx + cw * 0.34, body_rect.top() - ch * 0.34)
                tri.lineTo(lx + cw * 0.10, body_rect.top())
                tri.closeSubpath()
                p.drawPath(tri)
        for sgn in (-1, 1):
            lx = cx + sgn * cw * 0.26
            p.setBrush(QBrush(front_col)); p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRectF(lx - cw * 0.13, body_rect.top() + cw * 0.04,
                                      cw * 0.26, cw * 0.10), 3, 3)
        # rear (bottom) — red light bar
        rear = QColor(_C["crimson"]); rear.setAlpha(200 if self._lights else 120)
        p.setBrush(QBrush(rear)); p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(cx - cw * 0.34, body_rect.bottom() - cw * 0.14,
                                  cw * 0.68, cw * 0.07), 3, 3)

        # ── door indicators (open = glowing outward chevron) ──
        door_map = {
            "fl": (-1, -0.18), "fr": (1, -0.18),
            "rl": (-1, 0.22),  "rr": (1, 0.22),
        }
        for d in self._doors:
            sgn, fy = door_map[d]
            dx = cx + sgn * cw * 0.52
            dy = cy + fy * ch
            pulse = 0.5 + 0.5 * math.sin(self._t * 3)
            dc = QColor(_C["amber"]); dc.setAlpha(int(150 + 100 * pulse))
            p.setPen(QPen(dc, 3, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(dx, dy - cw * 0.12), QPointF(dx + sgn * cw * 0.14, dy))
            p.drawLine(QPointF(dx + sgn * cw * 0.14, dy), QPointF(dx, dy + cw * 0.12))

        # ── charging bolt + ring (if charging) ──
        if self._charging:
            bolt_y = cy
            pulse = 0.5 + 0.5 * math.sin(self._t * 4)
            gc = QColor(_C["green"]); gc.setAlpha(int(120 + 110 * pulse))
            p.setPen(QPen(gc, 3)); p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, bolt_y), cw * 0.26, cw * 0.26)
            # lightning bolt
            p.setBrush(QBrush(QColor(_C["green"]))); p.setPen(Qt.NoPen)
            b = QPainterPath()
            s = cw * 0.22
            b.moveTo(cx + s * 0.12, bolt_y - s * 0.5)
            b.lineTo(cx - s * 0.18, bolt_y + s * 0.08)
            b.lineTo(cx + s * 0.02, bolt_y + s * 0.08)
            b.lineTo(cx - s * 0.12, bolt_y + s * 0.5)
            b.lineTo(cx + s * 0.22, bolt_y - s * 0.10)
            b.lineTo(cx - s * 0.02, bolt_y - s * 0.10)
            b.closeSubpath()
            p.drawPath(b)

        # ── lock indicator (top-center, small) ──
        lock_c = QColor(_C["green"]) if self._locked else QColor(_C["amber"])
        p.setPen(QPen(lock_c, 2.5)); p.setBrush(Qt.NoBrush)
        lkx, lky = cx, body_rect.top() - 22
        p.drawRoundedRect(QRectF(lkx - 7, lky, 14, 11), 2, 2)
        if self._locked:
            p.drawArc(QRectF(lkx - 5, lky - 9, 10, 14), 0, 180 * 16)
        else:
            p.drawArc(QRectF(lkx - 5, lky - 9, 10, 14), 30 * 16, 150 * 16)

        p.end()


# ── standalone render test ─────────────────────────────────────
if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication
    from PyQt5.QtGui import QImage
    app = QApplication(sys.argv)

    scenarios = [
        ("idle",      dict(locked=True,  charging=False, lights=False, doors_open=[],            adas=True)),
        ("charging",  dict(locked=True,  charging=True,  lights=False, doors_open=[],            adas=True)),
        ("lights_on", dict(locked=False, charging=False, lights=True,  doors_open=[],            adas=True)),
        ("doors",     dict(locked=False, charging=False, lights=False, doors_open=["fl","rr"],   adas=True)),
    ]
    for name, st in scenarios:
        w = CarVisual(); w.resize(280, 360); w.set_status(**st)
        for _ in range(50): w._tick()
        img = QImage(280, 360, QImage.Format_ARGB32)
        img.fill(QColor("#05080F"))
        w.render(img); img.save(f"car_{name}.png")
        print(f"saved car_{name}.png")
