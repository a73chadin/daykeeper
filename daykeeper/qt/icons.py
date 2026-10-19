"""Vector icons drawn with QPainter (decision 5 of the port: no emoji glyphs, which Windows
paints in colour and macOS in another shape). An icon is painted at whatever size and
scale the screen asks, in the current theme's colours, so it stays sharp at 125 / 150 %
and follows a theme switch without being made again.

Every picture is drawn in a unit square (0..1 both ways) centred in the rect it is given.
The shared models and the catalogue still speak in glyphs ("🔑  Password", the field icons
of fieldicons.py); GLYPHS turns such a glyph into the picture that stands for it, so the
painted widgets (`paint`), the buttons (`button`) and the texts with a leading glyph
(`split`) all show the same drawings."""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPainterPath, QPen, QPixmap

from . import theme
from .errors import guard_events

Draw = Callable[[QPainter, QRectF], None]
Colour = Callable[[], str]


class _Unit:
    """Unit-square coordinates in a rect: the drawings below are written once for 0..1."""

    def __init__(self, r: QRectF):
        side = min(r.width(), r.height())
        self.x0 = r.center().x() - side / 2
        self.y0 = r.center().y() - side / 2
        self.s = side

    def pt(self, x: float, y: float) -> QPointF:
        return QPointF(self.x0 + x * self.s, self.y0 + y * self.s)

    def rect(self, x: float, y: float, w: float, h: float) -> QRectF:
        return QRectF(self.x0 + x * self.s, self.y0 + y * self.s, w * self.s, h * self.s)

    def len(self, d: float) -> float:
        return d * self.s

    def path(self, *points: tuple[float, float], close: bool = True) -> QPainterPath:
        path = QPainterPath(self.pt(*points[0]))
        for x, y in points[1:]:
            path.lineTo(self.pt(x, y))
        if close:
            path.closeSubpath()
        return path


def _line(p: QPainter, u: _Unit, *points: tuple[float, float]) -> None:
    for a, b in zip(points, points[1:], strict=False):
        p.drawLine(u.pt(*a), u.pt(*b))


def _dot(p: QPainter, u: _Unit, x: float, y: float, rad: float) -> None:
    p.save()
    p.setBrush(p.pen().color())
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(u.pt(x, y), u.len(rad), u.len(rad))
    p.restore()


def _fill(p: QPainter, path: QPainterPath) -> None:
    p.save()
    p.setBrush(p.pen().color())
    p.drawPath(path)
    p.restore()


# ---------------------------------------------------------------- the pictures


def _eye(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    path = QPainterPath(u.pt(0.06, 0.5))
    path.quadTo(u.pt(0.5, 0.06), u.pt(0.94, 0.5))
    path.quadTo(u.pt(0.5, 0.94), u.pt(0.06, 0.5))
    p.drawPath(path)
    p.drawEllipse(u.pt(0.5, 0.5), u.len(0.14), u.len(0.14))


def _eye_off(p: QPainter, r: QRectF) -> None:
    _eye(p, r)
    u = _Unit(r)
    _line(p, u, (0.18, 0.86), (0.82, 0.14))


def _power(p: QPainter, r: QRectF) -> None:
    """The lock-the-app button: the power sign, as the Tk bar had in a glyph."""
    u = _Unit(r)
    p.drawArc(u.rect(0.11, 0.13, 0.78, 0.78), 60 * 16, -300 * 16)  # from 1 o'clock round to 11: open at the top
    _line(p, u, (0.5, 0.06), (0.5, 0.5))


def _gear(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    c = u.pt(0.5, 0.5)
    outer, inner = u.len(0.46), u.len(0.355)
    path = QPainterPath()
    teeth = 8
    for i in range(teeth * 2):
        a0 = math.pi * 2 * i / (teeth * 2)
        a1 = math.pi * 2 * (i + 1) / (teeth * 2)
        rad = outer if i % 2 == 0 else inner
        pts = [QPointF(c.x() + rad * math.cos(a), c.y() + rad * math.sin(a)) for a in (a0, a1)]
        if i == 0:
            path.moveTo(pts[0])
        else:
            path.lineTo(pts[0])
        path.lineTo(pts[1])
    path.closeSubpath()
    p.drawPath(path)
    p.drawEllipse(c, u.len(0.15), u.len(0.15))


def _padlock(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawRoundedRect(u.rect(0.2, 0.45, 0.6, 0.45), u.len(0.08), u.len(0.08))
    p.drawArc(u.rect(0.32, 0.12, 0.36, 0.6), 0, 180 * 16)
    _line(p, u, (0.32, 0.42), (0.32, 0.45))
    _line(p, u, (0.68, 0.42), (0.68, 0.45))


def _turn(p: QPainter, r: QRectF, back: bool) -> None:
    """A curved arrow: back (undo) or forward (redo)."""
    u = _Unit(r)
    arc = u.rect(0.2, 0.28, 0.6, 0.55)
    start, span = (180 * 16, -200 * 16) if back else (0, 200 * 16)
    p.drawArc(arc, start, span)
    tip = QPointF(arc.left(), arc.center().y()) if back else QPointF(arc.right(), arc.center().y())
    d = u.len(0.16)
    side = 1 if back else -1
    p.drawLine(tip, QPointF(tip.x() - side * d * 0.2, tip.y() - d))
    p.drawLine(tip, QPointF(tip.x() + side * d, tip.y() - d * 0.2))


def _key(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawEllipse(u.pt(0.3, 0.7), u.len(0.17), u.len(0.17))
    _line(p, u, (0.42, 0.58), (0.86, 0.14))
    _line(p, u, (0.74, 0.26), (0.84, 0.36))
    _line(p, u, (0.63, 0.37), (0.7, 0.44))


def _old_key(p: QPainter, r: QRectF) -> None:
    """A private key: the key with a full bow and a bit, told apart from a password."""
    u = _Unit(r)
    p.drawEllipse(u.pt(0.5, 0.24), u.len(0.15), u.len(0.15))
    _line(p, u, (0.5, 0.39), (0.5, 0.9))
    _line(p, u, (0.5, 0.74), (0.66, 0.74), (0.66, 0.64))
    _line(p, u, (0.5, 0.86), (0.62, 0.86))


def _bookmark(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawPath(u.path((0.27, 0.12), (0.73, 0.12), (0.73, 0.88), (0.5, 0.7), (0.27, 0.88)))


def _journal(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawRoundedRect(u.rect(0.2, 0.1, 0.6, 0.8), u.len(0.07), u.len(0.07))
    _line(p, u, (0.33, 0.1), (0.33, 0.9))
    _line(p, u, (0.45, 0.34), (0.68, 0.34))
    _line(p, u, (0.45, 0.5), (0.68, 0.5))


def _page(p: QPainter, u: _Unit) -> None:
    p.drawPath(u.path((0.22, 0.1), (0.6, 0.1), (0.78, 0.28), (0.78, 0.9), (0.22, 0.9)))
    _line(p, u, (0.6, 0.1), (0.6, 0.28), (0.78, 0.28))


def _note(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    _page(p, u)
    for y in (0.48, 0.62, 0.76):
        _line(p, u, (0.34, y), (0.66, y))


def _file(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    _page(p, u)
    _line(p, u, (0.34, 0.56), (0.66, 0.56))
    _line(p, u, (0.34, 0.72), (0.58, 0.72))


def _image(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawRoundedRect(u.rect(0.1, 0.18, 0.8, 0.64), u.len(0.07), u.len(0.07))
    p.drawPath(u.path((0.16, 0.76), (0.4, 0.48), (0.56, 0.64), (0.66, 0.54), (0.84, 0.76), close=False))
    p.drawEllipse(u.pt(0.66, 0.36), u.len(0.06), u.len(0.06))


def _bolt(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawPath(u.path((0.58, 0.07), (0.22, 0.56), (0.48, 0.56), (0.42, 0.93), (0.78, 0.42), (0.52, 0.42)))


def _plug(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    _line(p, u, (0.4, 0.1), (0.4, 0.3))
    _line(p, u, (0.6, 0.1), (0.6, 0.3))
    path = QPainterPath(u.pt(0.26, 0.3))
    path.lineTo(u.pt(0.74, 0.3))
    path.lineTo(u.pt(0.74, 0.46))
    path.quadTo(u.pt(0.74, 0.66), u.pt(0.5, 0.66))
    path.quadTo(u.pt(0.26, 0.66), u.pt(0.26, 0.46))
    path.closeSubpath()
    p.drawPath(path)
    cable = QPainterPath(u.pt(0.5, 0.66))
    cable.lineTo(u.pt(0.5, 0.78))
    cable.quadTo(u.pt(0.5, 0.92), u.pt(0.66, 0.92))
    p.drawPath(cable)


def _tabs(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawPath(u.path((0.1, 0.86), (0.1, 0.2), (0.38, 0.2), (0.44, 0.32), (0.9, 0.32), (0.9, 0.86)))
    _line(p, u, (0.1, 0.42), (0.9, 0.42))


def _shield(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    path = QPainterPath(u.pt(0.5, 0.08))
    path.lineTo(u.pt(0.82, 0.2))
    path.cubicTo(u.pt(0.84, 0.56), u.pt(0.7, 0.8), u.pt(0.5, 0.92))
    path.cubicTo(u.pt(0.3, 0.8), u.pt(0.16, 0.56), u.pt(0.18, 0.2))
    path.closeSubpath()
    p.drawPath(path)


def _link(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.save()
    c = u.pt(0.5, 0.5)
    p.translate(c)
    p.rotate(-45)
    p.translate(-c)
    rad = u.len(0.12)
    p.drawRoundedRect(u.rect(0.06, 0.38, 0.48, 0.24), rad, rad)
    p.drawRoundedRect(u.rect(0.46, 0.38, 0.48, 0.24), rad, rad)
    p.restore()


def _assistant(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawRoundedRect(u.rect(0.18, 0.3, 0.64, 0.52), u.len(0.13), u.len(0.13))
    _line(p, u, (0.5, 0.3), (0.5, 0.17))
    _dot(p, u, 0.5, 0.13, 0.05)
    _dot(p, u, 0.38, 0.54, 0.06)
    _dot(p, u, 0.62, 0.54, 0.06)
    _line(p, u, (0.4, 0.69), (0.6, 0.69))


def _database(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawEllipse(u.rect(0.2, 0.1, 0.6, 0.22))
    _line(p, u, (0.2, 0.21), (0.2, 0.79))
    _line(p, u, (0.8, 0.21), (0.8, 0.79))
    p.drawArc(u.rect(0.2, 0.39, 0.6, 0.22), 180 * 16, 180 * 16)
    p.drawArc(u.rect(0.2, 0.68, 0.6, 0.22), 180 * 16, 180 * 16)


def _dice(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawRoundedRect(u.rect(0.14, 0.14, 0.72, 0.72), u.len(0.14), u.len(0.14))
    for x, y in ((0.34, 0.34), (0.5, 0.5), (0.66, 0.66)):
        _dot(p, u, x, y, 0.065)


def _cross(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    _line(p, u, (0.26, 0.26), (0.74, 0.74))
    _line(p, u, (0.74, 0.26), (0.26, 0.74))


def _up(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    _line(p, u, (0.22, 0.64), (0.5, 0.36), (0.78, 0.64))


def _down(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    _line(p, u, (0.22, 0.36), (0.5, 0.64), (0.78, 0.36))


def _prev(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    _line(p, u, (0.62, 0.22), (0.36, 0.5), (0.62, 0.78))


def _next(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    _line(p, u, (0.38, 0.22), (0.64, 0.5), (0.38, 0.78))


def _trash(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    _line(p, u, (0.16, 0.26), (0.84, 0.26))
    _line(p, u, (0.4, 0.26), (0.42, 0.13), (0.58, 0.13), (0.6, 0.26))
    _line(p, u, (0.25, 0.26), (0.3, 0.88), (0.7, 0.88), (0.75, 0.26))
    _line(p, u, (0.43, 0.42), (0.44, 0.74))
    _line(p, u, (0.57, 0.42), (0.56, 0.74))


def _more(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    for x in (0.2, 0.5, 0.8):
        _dot(p, u, x, 0.5, 0.075)


def _search(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawEllipse(u.pt(0.42, 0.42), u.len(0.26), u.len(0.26))
    _line(p, u, (0.61, 0.61), (0.86, 0.86))


def _pin(p: QPainter, r: QRectF) -> None:
    """A push pin, upright: the note is pinned (filled in by the caller's colour)."""
    u = _Unit(r)
    head = u.path((0.34, 0.1), (0.66, 0.1), (0.6, 0.18), (0.6, 0.42), (0.74, 0.58), (0.26, 0.58), (0.4, 0.42), (0.4, 0.18))
    _fill(p, head)
    _line(p, u, (0.5, 0.58), (0.5, 0.92))


def _pin_off(p: QPainter, r: QRectF) -> None:
    """The same pin tilted and hollow: pin this note."""
    u = _Unit(r)
    p.save()
    c = u.pt(0.5, 0.5)
    p.translate(c)
    p.rotate(35)
    p.translate(-c)
    p.drawPath(u.path((0.34, 0.1), (0.66, 0.1), (0.6, 0.18), (0.6, 0.42), (0.74, 0.58), (0.26, 0.58), (0.4, 0.42), (0.4, 0.18)))
    _line(p, u, (0.5, 0.58), (0.5, 0.92))
    p.restore()


def _palette(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    path = QPainterPath(u.pt(0.5, 0.1))
    path.cubicTo(u.pt(0.84, 0.1), u.pt(0.94, 0.4), u.pt(0.86, 0.56))
    path.cubicTo(u.pt(0.8, 0.68), u.pt(0.62, 0.6), u.pt(0.6, 0.72))
    path.cubicTo(u.pt(0.58, 0.86), u.pt(0.54, 0.9), u.pt(0.46, 0.9))
    path.cubicTo(u.pt(0.2, 0.9), u.pt(0.08, 0.7), u.pt(0.08, 0.5))
    path.cubicTo(u.pt(0.08, 0.26), u.pt(0.26, 0.1), u.pt(0.5, 0.1))
    p.drawPath(path)
    for x, y in ((0.3, 0.42), (0.46, 0.28), (0.66, 0.32), (0.3, 0.64)):
        _dot(p, u, x, y, 0.055)


def _clip(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    path = QPainterPath(u.pt(0.66, 0.34))
    path.lineTo(u.pt(0.66, 0.72))
    path.arcTo(u.rect(0.34, 0.56, 0.32, 0.32), 0, -180)
    path.lineTo(u.pt(0.34, 0.26))
    path.arcTo(u.rect(0.34, 0.1, 0.24, 0.24), 180, -180)
    path.lineTo(u.pt(0.58, 0.66))
    path.arcTo(u.rect(0.42, 0.58, 0.16, 0.16), 0, -180)
    path.lineTo(u.pt(0.42, 0.34))
    p.drawPath(path)


def _warning(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawPath(u.path((0.5, 0.12), (0.9, 0.86), (0.1, 0.86)))
    _line(p, u, (0.5, 0.4), (0.5, 0.6))
    _dot(p, u, 0.5, 0.73, 0.045)


def _external(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    _line(p, u, (0.44, 0.18), (0.18, 0.18), (0.18, 0.82), (0.82, 0.82), (0.82, 0.56))
    _line(p, u, (0.48, 0.52), (0.84, 0.16))
    _line(p, u, (0.6, 0.16), (0.84, 0.16), (0.84, 0.4))


def _copy(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawRoundedRect(u.rect(0.32, 0.32, 0.54, 0.54), u.len(0.08), u.len(0.08))
    _line(p, u, (0.18, 0.66), (0.14, 0.66), (0.14, 0.14), (0.66, 0.14), (0.66, 0.18))


def _user(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawEllipse(u.pt(0.5, 0.34), u.len(0.17), u.len(0.17))
    p.drawArc(u.rect(0.18, 0.6, 0.64, 0.56), 0, 180 * 16)


def _play(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    _fill(p, u.path((0.3, 0.18), (0.82, 0.5), (0.3, 0.82)))


def _stop(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    path = QPainterPath()
    path.addRoundedRect(u.rect(0.24, 0.24, 0.52, 0.52), u.len(0.06), u.len(0.06))
    _fill(p, path)


def _pencil(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawPath(u.path((0.16, 0.84), (0.2, 0.64), (0.66, 0.18), (0.82, 0.34), (0.36, 0.8)))
    _line(p, u, (0.58, 0.26), (0.74, 0.42))


def _ticket(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    path = QPainterPath(u.pt(0.1, 0.26))
    path.lineTo(u.pt(0.9, 0.26))
    path.lineTo(u.pt(0.9, 0.42))
    path.arcTo(u.rect(0.82, 0.42, 0.16, 0.16), 90, 180)
    path.lineTo(u.pt(0.9, 0.74))
    path.lineTo(u.pt(0.1, 0.74))
    path.lineTo(u.pt(0.1, 0.58))
    path.arcTo(u.rect(0.02, 0.42, 0.16, 0.16), 270, 180)
    path.closeSubpath()
    p.drawPath(path)
    for y in (0.36, 0.5, 0.64):
        _dot(p, u, 0.64, y, 0.035)


def _card(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawRoundedRect(u.rect(0.08, 0.22, 0.84, 0.56), u.len(0.08), u.len(0.08))
    _line(p, u, (0.08, 0.38), (0.92, 0.38))
    _line(p, u, (0.2, 0.62), (0.42, 0.62))


def _box(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawPath(u.path((0.5, 0.1), (0.86, 0.28), (0.86, 0.72), (0.5, 0.9), (0.14, 0.72), (0.14, 0.28)))
    _line(p, u, (0.14, 0.28), (0.5, 0.46), (0.86, 0.28))
    _line(p, u, (0.5, 0.46), (0.5, 0.9))


def _globe(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawEllipse(u.pt(0.5, 0.5), u.len(0.4), u.len(0.4))
    p.drawEllipse(u.pt(0.5, 0.5), u.len(0.17), u.len(0.4))
    _line(p, u, (0.1, 0.5), (0.9, 0.5))


def _mail(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawRoundedRect(u.rect(0.1, 0.22, 0.8, 0.56), u.len(0.06), u.len(0.06))
    _line(p, u, (0.12, 0.26), (0.5, 0.54), (0.88, 0.26))


def _phone(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawRoundedRect(u.rect(0.28, 0.08, 0.44, 0.84), u.len(0.09), u.len(0.09))
    _line(p, u, (0.44, 0.78), (0.56, 0.78))


def _mic(p: QPainter, r: QRectF) -> None:
    """A call to make a note of: a microphone on its stand."""
    u = _Unit(r)
    p.drawRoundedRect(u.rect(0.36, 0.08, 0.28, 0.5), u.len(0.14), u.len(0.14))
    p.drawArc(u.rect(0.22, 0.24, 0.56, 0.5), 180 * 16, 180 * 16)
    _line(p, u, (0.5, 0.74), (0.5, 0.88))
    _line(p, u, (0.36, 0.9), (0.64, 0.9))


def _calendar(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawRoundedRect(u.rect(0.12, 0.2, 0.76, 0.68), u.len(0.08), u.len(0.08))
    _line(p, u, (0.12, 0.4), (0.88, 0.4))
    _line(p, u, (0.34, 0.1), (0.34, 0.28))
    _line(p, u, (0.66, 0.1), (0.66, 0.28))
    for x, y in ((0.32, 0.56), (0.5, 0.56), (0.68, 0.56), (0.32, 0.72), (0.5, 0.72)):
        _dot(p, u, x, y, 0.04)


def _digits(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    _line(p, u, (0.4, 0.12), (0.32, 0.88))
    _line(p, u, (0.68, 0.12), (0.6, 0.88))
    _line(p, u, (0.16, 0.36), (0.86, 0.36))
    _line(p, u, (0.14, 0.64), (0.84, 0.64))


def _fox(p: QPainter, r: QRectF) -> None:
    """GitLab: the tanuki in outline."""
    u = _Unit(r)
    p.drawPath(u.path((0.5, 0.88), (0.1, 0.56), (0.21, 0.14), (0.34, 0.46), (0.66, 0.46), (0.79, 0.14), (0.9, 0.56)))


def _compass(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawEllipse(u.pt(0.5, 0.5), u.len(0.4), u.len(0.4))
    _fill(p, u.path((0.66, 0.34), (0.56, 0.56), (0.34, 0.66), (0.44, 0.44)))


def _bell(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    path = QPainterPath(u.pt(0.16, 0.72))
    path.lineTo(u.pt(0.84, 0.72))
    path.cubicTo(u.pt(0.74, 0.62), u.pt(0.76, 0.5), u.pt(0.76, 0.42))
    path.cubicTo(u.pt(0.76, 0.24), u.pt(0.64, 0.14), u.pt(0.5, 0.14))
    path.cubicTo(u.pt(0.36, 0.14), u.pt(0.24, 0.24), u.pt(0.24, 0.42))
    path.cubicTo(u.pt(0.24, 0.5), u.pt(0.26, 0.62), u.pt(0.16, 0.72))
    p.drawPath(path)
    p.drawArc(u.rect(0.4, 0.74, 0.2, 0.16), 180 * 16, 180 * 16)


def _clock(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawEllipse(u.pt(0.5, 0.52), u.len(0.38), u.len(0.38))
    _line(p, u, (0.5, 0.3), (0.5, 0.52), (0.66, 0.6))


def _hourglass(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawPath(u.path((0.26, 0.12), (0.74, 0.12), (0.5, 0.5), (0.74, 0.88), (0.26, 0.88), (0.5, 0.5)))


def _check(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    _line(p, u, (0.2, 0.52), (0.42, 0.74), (0.82, 0.28))


def _check_circle(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawEllipse(u.pt(0.5, 0.5), u.len(0.4), u.len(0.4))
    _line(p, u, (0.32, 0.52), (0.45, 0.65), (0.68, 0.38))


def _cross_circle(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawEllipse(u.pt(0.5, 0.5), u.len(0.4), u.len(0.4))
    _line(p, u, (0.36, 0.36), (0.64, 0.64))
    _line(p, u, (0.64, 0.36), (0.36, 0.64))


def _comment(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    path = QPainterPath()
    path.addRoundedRect(u.rect(0.1, 0.16, 0.8, 0.54), u.len(0.12), u.len(0.12))
    tail = u.path((0.3, 0.7), (0.26, 0.88), (0.48, 0.7), close=False)
    p.drawPath(path)
    p.drawPath(tail)


def _merged(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawEllipse(u.pt(0.3, 0.2), u.len(0.09), u.len(0.09))
    p.drawEllipse(u.pt(0.3, 0.8), u.len(0.09), u.len(0.09))
    p.drawEllipse(u.pt(0.72, 0.56), u.len(0.09), u.len(0.09))
    _line(p, u, (0.3, 0.29), (0.3, 0.71))
    path = QPainterPath(u.pt(0.3, 0.3))
    path.quadTo(u.pt(0.32, 0.56), u.pt(0.63, 0.56))
    p.drawPath(path)


def _reply(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    path = QPainterPath(u.pt(0.2, 0.42))
    path.lineTo(u.pt(0.62, 0.42))
    path.quadTo(u.pt(0.86, 0.42), u.pt(0.86, 0.66))
    path.lineTo(u.pt(0.86, 0.82))
    p.drawPath(path)
    _line(p, u, (0.38, 0.24), (0.2, 0.42), (0.38, 0.6))


def _script(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    _page(p, u)
    _line(p, u, (0.44, 0.48), (0.34, 0.6), (0.44, 0.72))
    _line(p, u, (0.56, 0.48), (0.66, 0.6), (0.56, 0.72))


def _terminal(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawRoundedRect(u.rect(0.08, 0.16, 0.84, 0.68), u.len(0.08), u.len(0.08))
    _line(p, u, (0.24, 0.38), (0.38, 0.5), (0.24, 0.62))
    _line(p, u, (0.46, 0.64), (0.7, 0.64))


def _folder(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawPath(u.path((0.08, 0.82), (0.08, 0.2), (0.38, 0.2), (0.46, 0.3), (0.92, 0.3), (0.92, 0.82)))


def _refresh(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawArc(u.rect(0.16, 0.16, 0.68, 0.68), 40 * 16, 290 * 16)
    _line(p, u, (0.62, 0.12), (0.8, 0.26), (0.62, 0.38))


def _clipboard(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawRoundedRect(u.rect(0.2, 0.16, 0.6, 0.76), u.len(0.07), u.len(0.07))
    p.drawRoundedRect(u.rect(0.36, 0.08, 0.28, 0.14), u.len(0.04), u.len(0.04))
    _line(p, u, (0.34, 0.44), (0.66, 0.44))
    _line(p, u, (0.34, 0.6), (0.66, 0.6))
    _line(p, u, (0.34, 0.76), (0.54, 0.76))


def _clip_history(p: QPainter, r: QRectF) -> None:
    """The clipboard history: a clipboard whose lower corner gives way to a clock."""
    u = _Unit(r)
    p.drawPath(u.path((0.46, 0.86), (0.12, 0.86), (0.12, 0.14), (0.66, 0.14), (0.66, 0.44), close=False))
    p.drawRoundedRect(u.rect(0.26, 0.06, 0.26, 0.13), u.len(0.04), u.len(0.04))
    _line(p, u, (0.24, 0.36), (0.54, 0.36))
    _line(p, u, (0.24, 0.52), (0.4, 0.52))
    p.drawEllipse(u.pt(0.72, 0.72), u.len(0.2), u.len(0.2))
    _line(p, u, (0.72, 0.61), (0.72, 0.73), (0.8, 0.79))


def _pause(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    _line(p, u, (0.36, 0.22), (0.36, 0.78))
    _line(p, u, (0.64, 0.22), (0.64, 0.78))


def _bug(p: QPainter, r: QRectF) -> None:
    u = _Unit(r)
    p.drawEllipse(u.rect(0.32, 0.34, 0.36, 0.54))
    p.drawArc(u.rect(0.38, 0.18, 0.24, 0.24), 0, 180 * 16)
    _line(p, u, (0.5, 0.46), (0.5, 0.88))
    for y, dx in ((0.5, 0.16), (0.64, 0.18), (0.78, 0.14)):
        _line(p, u, (0.33, y), (0.33 - dx, y - 0.06))
        _line(p, u, (0.67, y), (0.67 + dx, y - 0.06))
    _line(p, u, (0.42, 0.2), (0.34, 0.1))
    _line(p, u, (0.58, 0.2), (0.66, 0.1))


def _broadcast(p: QPainter, r: QRectF) -> None:
    """A routine that starts on an integration event: a mast sending waves both ways."""
    u = _Unit(r)
    _dot(p, u, 0.5, 0.42, 0.08)
    _line(p, u, (0.5, 0.5), (0.5, 0.9))
    for rad in (0.2, 0.36):
        box = u.rect(0.5 - rad, 0.42 - rad, 2 * rad, 2 * rad)
        p.drawArc(box, 135 * 16, 90 * 16)
        p.drawArc(box, -45 * 16, 90 * 16)


def _template(p: QPainter, r: QRectF) -> None:
    """A text template: a page with a placeholder in braces and lines under it."""
    u = _Unit(r)
    _page(p, u)
    brace = QPainterPath(u.pt(0.42, 0.3))
    brace.quadTo(u.pt(0.34, 0.3), u.pt(0.34, 0.38))
    brace.quadTo(u.pt(0.34, 0.44), u.pt(0.29, 0.44))
    brace.quadTo(u.pt(0.34, 0.44), u.pt(0.34, 0.5))
    brace.quadTo(u.pt(0.34, 0.58), u.pt(0.42, 0.58))
    p.drawPath(brace)
    brace = QPainterPath(u.pt(0.56, 0.3))
    brace.quadTo(u.pt(0.64, 0.3), u.pt(0.64, 0.38))
    brace.quadTo(u.pt(0.64, 0.44), u.pt(0.69, 0.44))
    brace.quadTo(u.pt(0.64, 0.44), u.pt(0.64, 0.5))
    brace.quadTo(u.pt(0.64, 0.58), u.pt(0.56, 0.58))
    p.drawPath(brace)
    _line(p, u, (0.32, 0.72), (0.68, 0.72))


def _chart(p: QPainter, r: QRectF) -> None:
    """A bar chart: the journal over a period."""
    u = _Unit(r)
    _line(p, u, (0.1, 0.1), (0.1, 0.9), (0.92, 0.9))
    for x, top in ((0.26, 0.56), (0.46, 0.3), (0.66, 0.44), (0.84, 0.18)):
        p.drawRoundedRect(u.rect(x - 0.06, top, 0.12, 0.9 - top - 0.06), u.len(0.02), u.len(0.02))


def _stages(p: QPainter, u: _Unit) -> None:
    """A pipeline: two small finished stages leading to the last one, drawn by the caller."""
    p.drawEllipse(u.pt(0.12, 0.5), u.len(0.07), u.len(0.07))
    p.drawEllipse(u.pt(0.34, 0.5), u.len(0.07), u.len(0.07))
    _line(p, u, (0.19, 0.5), (0.27, 0.5))
    _line(p, u, (0.41, 0.5), (0.5, 0.5))
    p.drawEllipse(u.pt(0.71, 0.5), u.len(0.21), u.len(0.21))


def _pipe_failed(p: QPainter, r: QRectF) -> None:
    """A watched pipeline failed: the last stage crossed."""
    u = _Unit(r)
    _stages(p, u)
    _line(p, u, (0.63, 0.42), (0.79, 0.58))
    _line(p, u, (0.79, 0.42), (0.63, 0.58))


def _pipe_fixed(p: QPainter, r: QRectF) -> None:
    """A watched pipeline is green again: the last stage ticked."""
    u = _Unit(r)
    _stages(p, u)
    _line(p, u, (0.61, 0.51), (0.68, 0.58), (0.81, 0.43))


def _commit(p: QPainter, r: QRectF) -> None:
    """New commits on a watched branch: a commit on its line."""
    u = _Unit(r)
    p.drawEllipse(u.pt(0.5, 0.5), u.len(0.17), u.len(0.17))
    _line(p, u, (0.06, 0.5), (0.33, 0.5))
    _line(p, u, (0.67, 0.5), (0.94, 0.5))


def _branch(p: QPainter, r: QRectF) -> None:
    """A watched branch: the trunk and a branch leaving it."""
    u = _Unit(r)
    p.drawEllipse(u.pt(0.3, 0.18), u.len(0.09), u.len(0.09))
    p.drawEllipse(u.pt(0.3, 0.82), u.len(0.09), u.len(0.09))
    p.drawEllipse(u.pt(0.72, 0.3), u.len(0.09), u.len(0.09))
    _line(p, u, (0.3, 0.27), (0.3, 0.73))
    path = QPainterPath(u.pt(0.72, 0.39))
    path.quadTo(u.pt(0.72, 0.62), u.pt(0.3, 0.66))
    p.drawPath(path)


def _sun(p: QPainter, r: QRectF) -> None:
    """The morning digest: a disc and eight short rays."""
    u = _Unit(r)
    p.drawEllipse(u.pt(0.5, 0.5), u.len(0.17), u.len(0.17))
    for k in range(8):
        a = k * math.pi / 4
        c, s = math.cos(a), math.sin(a)
        _line(p, u, (0.5 + 0.29 * c, 0.5 + 0.29 * s), (0.5 + 0.42 * c, 0.5 + 0.42 * s))


def _plain(p: QPainter, r: QRectF) -> None:
    """A filled field with no preset name: a small square."""
    u = _Unit(r)
    p.drawRoundedRect(u.rect(0.32, 0.32, 0.36, 0.36), u.len(0.05), u.len(0.05))


def _hotkey(p: QPainter, r: QRectF) -> None:
    """Quick access: a key cap with a spark of lightning on it."""
    u = _Unit(r)
    p.drawRoundedRect(u.rect(0.1, 0.14, 0.8, 0.72), u.len(0.14), u.len(0.14))
    _line(p, u, (0.56, 0.28), (0.4, 0.52), (0.6, 0.52), (0.44, 0.74))


def _slash(p: QPainter, r: QRectF) -> None:
    """A command of the quick window: a slash in a rounded frame."""
    u = _Unit(r)
    p.drawRoundedRect(u.rect(0.12, 0.12, 0.76, 0.76), u.len(0.14), u.len(0.14))
    _line(p, u, (0.6, 0.28), (0.4, 0.72))


def _send(p: QPainter, r: QRectF) -> None:
    """Send to an AI assistant: a paper plane, its fold down the middle."""
    u = _Unit(r)
    p.drawPath(u.path((0.08, 0.46), (0.92, 0.12), (0.66, 0.88), (0.46, 0.6)))
    _line(p, u, (0.92, 0.12), (0.46, 0.6), (0.4, 0.84), (0.56, 0.7))


DRAW: dict[str, Draw] = {
    "eye": _eye, "eye_off": _eye_off, "power": _power, "gear": _gear, "padlock": _padlock,
    "undo": lambda p, r: _turn(p, r, back=True), "redo": lambda p, r: _turn(p, r, back=False),
    "key": _key, "old_key": _old_key, "bookmark": _bookmark, "journal": _journal, "note": _note,
    "file": _file, "image": _image, "bolt": _bolt, "plug": _plug, "tabs": _tabs, "shield": _shield,
    "link": _link, "assistant": _assistant, "database": _database, "dice": _dice, "cross": _cross,
    "up": _up, "down": _down, "prev": _prev, "next": _next, "trash": _trash, "more": _more, "search": _search, "pin": _pin,
    "pin_off": _pin_off, "palette": _palette, "clip": _clip, "warning": _warning,
    "external": _external, "copy": _copy, "user": _user, "play": _play, "stop": _stop,
    "pencil": _pencil, "ticket": _ticket, "card": _card, "box": _box, "globe": _globe,
    "mail": _mail, "phone": _phone, "calendar": _calendar, "digits": _digits, "fox": _fox,
    "compass": _compass, "bell": _bell, "clock": _clock, "hourglass": _hourglass,
    "check": _check, "check_circle": _check_circle, "cross_circle": _cross_circle,
    "comment": _comment, "merged": _merged, "reply": _reply, "script": _script,
    "terminal": _terminal, "folder": _folder, "refresh": _refresh, "clipboard": _clipboard, "bug": _bug,
    "clip_history": _clip_history, "pause": _pause,
    "broadcast": _broadcast, "template": _template, "chart": _chart, "pipe_failed": _pipe_failed,
    "pipe_fixed": _pipe_fixed, "commit": _commit, "branch": _branch, "sun": _sun, "hotkey": _hotkey,
    "slash": _slash, "mic": _mic, "send": _send, "plain": _plain,
}

# the glyph the shared code and the catalogue use → the picture that stands for it
GLYPHS: dict[str, str] = {
    "🔐": "padlock", "🔒": "padlock", "🔖": "bookmark", "📝": "journal", "📓": "journal", "🗒": "note",
    "📄": "file", "📕": "file", "🖼": "image", "⚡": "bolt", "🔌": "plug", "⚙": "gear", "🗂": "tabs",
    "🛡": "shield", "🔑": "key", "🗝": "old_key", "🔗": "link", "🤖": "assistant", "🗄": "database",
    "🎲": "dice", "✕": "cross", "▲": "up", "▼": "down", "🗑": "trash", "⋯": "more", "🔍": "search",
    "📌": "pin", "📍": "pin_off", "🎨": "palette", "📎": "clip", "⚠": "warning", "↗": "external",
    "⧉": "copy", "👤": "user", "▶": "play", "■": "stop", "✎": "pencil", "🎟": "ticket", "💳": "card",
    "📦": "box", "🌐": "globe", "✉": "mail", "📞": "phone", "📅": "calendar", "📆": "calendar", "🔢": "digits",
    "🦊": "fox", "🧭": "compass", "🔔": "bell", "⏰": "clock", "⏳": "hourglass", "✔": "check",
    "✅": "check_circle", "❌": "cross_circle", "💬": "comment", "🎉": "merged", "👀": "eye",
    "👁": "eye", "🙈": "eye_off", "↩": "reply", "📜": "script", "⌨": "terminal", "📁": "folder",
    "⟳": "refresh", "📋": "clipboard", "🐞": "bug", "🐛": "bug", "✓": "check", "✗": "cross", "▫": "plain",
    "📡": "broadcast", "📑": "template", "📊": "chart", "☀": "sun", "✨": "hotkey", "🎙": "mic", "📤": "send",
}


def name_of(glyph_or_name: str) -> str | None:
    key = glyph_or_name.strip().replace("️", "")
    if key in DRAW:
        return key
    return GLYPHS.get(key)


def split(text: str) -> tuple[str | None, str]:
    """"🔑  Password" → ("key", "Password"); a text with no known glyph in front is
    (None, text) unchanged."""
    head, sep, rest = text.lstrip().partition(" ")
    name = name_of(head) if sep else None
    if name is None:
        return None, text
    return name, rest.lstrip()


def stroke(size: float) -> float:
    return max(1.25, size / 12)


def paint(p: QPainter, name: str, rect: QRectF | QRect, colour: str | QColor, size: float | None = None) -> None:
    """Draw the picture `name` (or the glyph that stands for it) inside `rect` — for the
    painted tiles, notes and lists. `size` makes a square of that side in the rect's
    centre (the rect stays the click zone). A picture is drawn once per colour, size and
    screen scale and then copied (300 tiles repaint on every resize)."""
    key_name = name_of(name)
    if key_name is None:
        return
    r = QRectF(rect)
    side = size if size is not None else min(r.width(), r.height())
    square = QRectF(0, 0, side, side)
    square.moveCenter(r.center())
    device = p.device()
    ratio = device.devicePixelRatioF() if device is not None else 1.0
    p.drawPixmap(square, _picture(key_name, QColor(colour).rgba(), max(1, round(side * ratio)), ratio), QRectF())


_pictures: dict[tuple[str, int, int], QPixmap] = {}


def _picture(name: str, rgba: int, px: int, ratio: float) -> QPixmap:
    key = (name, rgba, px)
    pm = _pictures.get(key)
    if pm is None:
        if len(_pictures) > 4000:  # many sizes over a long day: start again
            _pictures.clear()
        pm = QPixmap(px, px)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor.fromRgba(rgba), stroke(px))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        DRAW[name](painter, QRectF(0, 0, px, px))
        painter.end()
        _pictures[key] = pm
    pm.setDevicePixelRatio(ratio)
    return pm


@guard_events
class _Engine(QIconEngine):
    def __init__(self, draw: Draw, colour: Colour, active: Colour | None = None, disabled: Colour | None = None, checked: Colour | None = None):
        super().__init__()
        self._draw, self._colour, self._active, self._disabled, self._checked = draw, colour, active, disabled, checked

    def paint(self, painter: QPainter, rect: QRect, mode: QIcon.Mode, state: QIcon.State) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if mode == QIcon.Mode.Disabled:
            colour = QColor(self._disabled() if self._disabled is not None else theme.current().DISABLED_TEXT)
        elif state == QIcon.State.On and self._checked is not None:
            colour = QColor(self._checked())
        elif mode in (QIcon.Mode.Active, QIcon.Mode.Selected) and self._active is not None:  # under the pointer
            colour = QColor(self._active())
        else:
            colour = QColor(self._colour())
        pen = QPen(colour, stroke(rect.width()))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        self._draw(painter, QRectF(rect).adjusted(1, 1, -1, -1))
        painter.restore()

    def pixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State) -> QPixmap:
        pm = QPixmap(size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        self.paint(p, QRect(0, 0, size.width(), size.height()), mode, state)
        p.end()
        return pm

    def scaledPixmap(self, size: QSize, mode: QIcon.Mode, state: QIcon.State, scale: float) -> QPixmap:
        """Drawn at the device pixels of the screen (125 / 150 %), not drawn small and stretched."""
        pm = QPixmap(QSize(max(1, round(size.width() * scale)), max(1, round(size.height() * scale))))
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        self.paint(p, QRect(0, 0, pm.width(), pm.height()), mode, state)
        p.end()
        pm.setDevicePixelRatio(scale)
        return pm

    def clone(self) -> QIconEngine:
        return _Engine(self._draw, self._colour, self._active, self._disabled, self._checked)


def icon(draw: Draw | str, colour: Colour, active: Colour | None = None, disabled: Colour | None = None, checked: Colour | None = None) -> QIcon:
    if isinstance(draw, str):
        draw = DRAW[name_of(draw) or "plain"]
    return QIcon(_Engine(draw, colour, active, disabled, checked))


def eye(shown: bool = False) -> QIcon:
    """Inside a password field: the eye to show it, the crossed eye to hide it again."""
    return icon(_eye_off if shown else _eye, lambda: theme.current().MUTED)


def _rail(draw: Draw | str) -> QIcon:
    """On the dark rail: grey, white under the pointer and when picked, nearly the rail
    itself when off."""
    t = theme.current
    return icon(draw, lambda: t().RAIL_TEXT, lambda: t().ON_RAIL, lambda: theme.palette_mix(t().RAIL_TEXT, t().RAIL, 0.35), lambda: t().ON_ACCENT)


def rail(name: str) -> QIcon:
    return _rail(name)


def power() -> QIcon:
    return _rail(_power)


def gear() -> QIcon:
    return _rail(_gear)


def padlock() -> QIcon:
    return _rail(_padlock)


def undo() -> QIcon:
    return _rail("undo")


def redo() -> QIcon:
    return _rail("redo")


def button(name: str, role: str = "icon") -> QIcon:
    """For a button on a card: muted, the text colour under the pointer, the accent when
    switched on; red for a "danger" button."""
    t = theme.current
    if role == "danger":
        return icon(name, lambda: t().DANGER, lambda: t().DANGER)
    return icon(name, lambda: t().MUTED, lambda: t().TEXT, checked=lambda: t().ACCENT)


def plain(name: str) -> QIcon:
    """Beside a text (a menu item, a list row, a check box): the text colour."""
    t = theme.current
    return icon(name, lambda: t().TEXT, lambda: t().TEXT)
