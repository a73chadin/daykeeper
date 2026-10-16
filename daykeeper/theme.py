"""Modern look for ttk: rounded buttons, fields, checkboxes and thin scrollbars drawn as images.

Tk has no rounded corners, so every rounded shape is a small anti-aliased PNG made here
(signed distance, no Pillow) and stretched as a nine-slice image element.
"""

from __future__ import annotations

import base64
import math
import sys
import time
import tkinter as tk
from tkinter import ttk

from . import palette, win32
from .timers import Debounce
from .pngimage import png  # noqa: F401 — re-exported: the theme's callers import it from here

RGB = tuple[int, int, int]


def hex_rgb(colour: str) -> RGB:
    c = colour.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def blend(a: str, b: str, t: float) -> str:
    """Colour `a` mixed into `b` by `t` (0 — all b, 1 — all a)."""
    ra, rb = hex_rgb(a), hex_rgb(b)
    return "#" + "".join(f"{round(x * t + y * (1 - t)):02X}" for x, y in zip(ra, rb, strict=True))



def _box(px: float, py: float, hw: float, hh: float, r: float) -> float:
    """Signed distance from a point (relative to the centre) to a rounded box."""
    qx, qy = abs(px) - hw + r, abs(py) - hh + r
    return math.hypot(max(qx, 0.0), max(qy, 0.0)) + min(max(qx, qy), 0.0) - r


def _segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - ax - t * dx, py - ay - t * dy)


def _cover(d: float) -> float:
    return max(0.0, min(1.0, 0.5 - d))


def shape(
    width: int,
    height: int,
    radius: float,
    fill: str | None,
    border: str | None = None,
    border_width: float = 1.0,
    inset: float = 0.0,
    check: str | None = None,
    dot: str | None = None,
    shadow: float = 0.0,
    right: float = 0.0,
    matte: str | None = None,
) -> bytes:
    """A rounded box, optionally with a border, a tick, a dot or a soft shadow, as a PNG.
    `right` adds a transparent margin on the right (the gap between a checkbox and its text).
    `matte` is the colour the box always sits on: the image is drawn over it and has no
    transparency left. Tk on Windows blends an image with partial transparency by reading
    the screen back, for the whole area it draws — slow for a card; an opaque image is a
    plain copy."""
    kw = dict(radius=radius, fill=fill, border=border, border_width=border_width, inset=inset, check=check, dot=dot, shadow=shadow, matte=matte)
    if check or dot or right:
        rgba = _render(width, height, **kw)
        gap = bytes(4 * round(right))
        rows = [bytes(rgba[y * width * 4 : (y + 1) * width * 4]) + gap for y in range(height)]
        return png(width + round(right), height, bytearray(b"".join(rows)))
    # straight edges look the same all along, so draw a small box and stretch its middle
    rim = math.ceil(radius + border_width + shadow + inset + 2)
    ws, hs = min(width, 2 * rim + 1), min(height, 2 * rim + 1)
    small = _render(ws, hs, **kw)
    mx, my = ws // 2, hs // 2
    rows = []
    for y in range(hs):
        row = small[y * ws * 4 : (y + 1) * ws * 4]
        rows.append(row[: mx * 4] + row[mx * 4 : mx * 4 + 4] * (width - ws + 1) + row[mx * 4 + 4 :])
    out = rows[:my] + [rows[my]] * (height - hs + 1) + rows[my + 1 :]
    return png(width, height, bytearray(b"".join(out)))


def _render(
    width: int,
    height: int,
    radius: float,
    fill: str | None,
    border: str | None,
    border_width: float,
    inset: float,
    check: str | None,
    dot: str | None,
    shadow: float,
    matte: str | None = None,
) -> bytearray:
    rgba = bytearray(width * height * 4)
    cx, cy = width / 2, height / 2
    hw, hh = width / 2 - inset - shadow, height / 2 - inset - shadow
    f = hex_rgb(fill) if fill else None
    b = hex_rgb(border) if border else None
    k = hex_rgb(check) if check else None
    dt = hex_rgb(dot) if dot else None
    m = hex_rgb(matte) if matte else None
    s = min(hw, hh)
    for y in range(height):
        for x in range(width):
            px, py = x + 0.5 - cx, y + 0.5 - cy
            layers: list[tuple[RGB, float]] = []
            if shadow:
                outer = _box(px, py - shadow * 0.25, hw, hh, radius)
                layers.append(((15, 23, 42), max(0.0, 1.0 - max(outer, 0.0) / shadow) ** 2 * 0.10))
            co = _cover(_box(px, py, hw, hh, radius))
            if b is not None:
                ci = _cover(_box(px, py, hw - border_width, hh - border_width, max(radius - border_width, 0.0)))
                if f is not None:
                    layers.append((f, ci))
                layers.append((b, co - ci))
            elif f is not None:
                layers.append((f, co))
            if k is not None:
                dd = min(
                    _segment(px, py, -0.42 * s, 0.02 * s, -0.12 * s, 0.32 * s),
                    _segment(px, py, -0.12 * s, 0.32 * s, 0.44 * s, -0.30 * s),
                )
                layers.append((k, _cover(dd - 0.11 * s) * co))
            if dt is not None:
                layers.append((dt, _cover(math.hypot(px, py) - 0.36 * s)))
            r = g = bl = a = 0.0
            if m is not None:
                layers.insert(0, (m, 1.0))
            for (lr, lg, lb), la in layers:  # "over" compositing, back to front
                if la <= 0:
                    continue
                na = la + a * (1 - la)
                r = (lr * la + r * a * (1 - la)) / na
                g = (lg * la + g * a * (1 - la)) / na
                bl = (lb * la + bl * a * (1 - la)) / na
                a = na
            i = (y * width + x) * 4
            rgba[i : i + 4] = bytes((round(r), round(g), round(bl), round(a * 255)))
    return rgba


STRETCH_W, STRETCH_H = 600, 60


class Painter:
    """Makes images for ttk elements and keeps them alive (Tk forgets images nobody holds)."""

    def __init__(self, root: tk.Misc):
        self.root = root
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        self.images: dict[tuple, tk.PhotoImage] = {}
        self.styles: set[str] = set()
        self.tag = f"x{round(self.scale * 100)}"  # in every element name: ttk cannot redefine an element

    def el(self, name: str) -> str:
        """The name of element `name` at this scale. A move to a monitor of another scale
        makes a new Painter, new elements under new names, and points the styles at them."""
        return f"{name}{self.tag}"

    def px(self, n: float) -> int:
        return max(1, round(n * self.scale))

    def image(self, *key, **kw) -> tk.PhotoImage:
        k = (*key, *sorted(kw.items()))
        if k not in self.images:
            w, h, r = key
            data = shape(self.px(w), self.px(h), r * self.scale, **{n: (v * self.scale if n in ("border_width", "inset", "shadow", "right") else v) for n, v in kw.items()})
            self.images[k] = tk.PhotoImage(master=self.root, data=base64.b64encode(data))
        return self.images[k]

    def box(self, radius: float, fill: str | None, border: str | None = None, **kw) -> tk.PhotoImage:
        # a wide middle: Tk tiles the middle of a nine-slice image, and on Windows every
        # tile of an image with transparency is a separate slow blend
        return self.image(radius * 2 + 4 + STRETCH_W, radius * 2 + 4 + STRETCH_H, radius, fill=fill, border=border, **kw)

    def element(self, style: ttk.Style, name: str, radius: float, states: list[tuple[str, tk.PhotoImage]], normal: tk.PhotoImage, **kw) -> None:
        if name in style.element_names():
            return
        spec = [(s, img) for s, img in states]
        b = self.px(radius + 1)
        # the image is large (see box), so the element asks only for its corners
        style.element_create(name, "image", normal, *spec, border=b, sticky="nsew", width=2 * b, height=2 * b, **kw)


def button(style: ttk.Style, p: Painter, name: str, radius: float, normal: tuple, hover: tuple, pressed: tuple, disabled: tuple | None = None, focus: tuple | None = None) -> None:
    """A rounded button-like style: each state is (fill, border) or (fill, border, shadow) —
    a raised button carries a soft shadow under it; the corners of the nine-slice take the
    widest shadow of the states, so every state lines up."""
    looks = [x for x in (normal, hover, pressed, disabled, focus) if x]
    lift = max((x[2] for x in looks if len(x) > 2), default=0.0)

    def img(look: tuple) -> tk.PhotoImage:
        shadow = look[2] if len(look) > 2 else 0.0
        # a state without a shadow is drawn inset by the same margin, so it does not jump
        return p.box(radius, look[0], look[1], shadow=shadow, inset=lift - shadow) if lift else p.box(radius, look[0], look[1])

    states = []
    if disabled:
        states.append(("disabled", img(disabled)))
    states += [("pressed", img(pressed)), ("active", img(hover))]
    if focus:
        states.append(("focus", img(focus)))
    p.element(style, p.el(f"{name}.rbox"), radius + lift, states, img(normal))


class QuietResize:
    """Windows: while the window is dragged to a new size, `area()` is not repainted on
    every step of the drag — a tab of cards takes about a fifth of a second to paint, so
    the drag stuttered and the cards shook. It is painted once, `quiet_ms` after the drag
    pauses. A single change of size (maximize, restore) paints at once as usual."""

    def __init__(self, window: tk.Tk, area, quiet_ms: int, pin=None, unpin=None):
        """`pin()` / `unpin()` — called when a drag starts and when it pauses: the window
        keeps the content at its old size meanwhile, so nothing inside is laid out again on
        every step (laying out a tab of cards took 0.2–0.4 s a step)."""
        self.window, self.area, self.quiet_ms = window, area, quiet_ms
        self.pin, self.unpin = pin, unpin
        self.size: tuple[int, int] | None = None
        self.last = 0.0
        self.hwnd = 0
        self.thaw = Debounce(window, quiet_ms, self._thaw)
        if sys.platform == "win32":
            # the window's own size, not every widget's: a root binding fires for all of them
            bind = getattr(window, "bind_root", None)
            if bind is not None:
                bind("<Configure>", self._on_configure)
            else:
                window.bind("<Configure>", self._on_configure, add="+")

    def _on_configure(self, event: tk.Event) -> None:
        if event.widget is not self.window:
            return
        size = (event.width, event.height)
        if size == self.size:
            return
        first = self.size is None
        self.size = size
        now = time.monotonic()
        # a drag: the mouse holds the border, or sizes come close together (a step slower than
        # quiet_ms must not end the drag, or every later step is laid out in full)
        dragging = not first and (win32.mouse_held() or now - self.last < self.quiet_ms / 1000)
        self.last = now
        if dragging and not self.hwnd:
            area = self.area()
            if area is None or not area.winfo_exists():
                return
            self.hwnd = area.winfo_id()
            if not win32.set_redraw(self.hwnd, False):
                self.hwnd = 0
                return
            if self.pin is not None:
                self.pin()
        if self.hwnd:
            self.thaw.touch()

    def _thaw(self) -> None:
        hwnd, self.hwnd = self.hwnd, 0
        if hwnd:
            if self.unpin is not None:
                self.unpin()
                self.window.update_idletasks()  # the one layout at the new size, before the paint
            win32.set_redraw(hwnd, True)
            win32.redraw_now(hwnd)


def caption_colour(window: tk.Misc, background: str, text: str) -> None:
    """Paint the Windows 11 title bar; older systems just ignore it."""
    win32.caption_colour(win32.window_handle(window.winfo_id()), background, text)


def round_style(style: ttk.Style, p: Painter, fill: str, border: str | None, radius: float = 12, outer: str = palette.CARD, width: float = 1.0) -> str:
    """A ttk frame style drawn as a rounded box; made once per look and kept."""
    name = f"R{radius:g}x{fill.lstrip('#')}x{(border or 'none').lstrip('#')}x{width:g}x{outer.lstrip('#')}".replace(".", "_") + ".TFrame"
    if name not in p.styles:
        element = p.el(name[: -len(".TFrame")] + ".rbox")
        p.element(style, element, radius, [], p.box(radius, fill, border, border_width=width, matte=outer))
        style.layout(name, [(element, {"sticky": "nswe"})])
        style.configure(name, background=outer)
        p.styles.add(name)
    return name


def corner_inset(p: Painter, radius: float, outline: float = 1.5) -> int:
    """How far a rectangle must stay from the edges to clear a corner of this radius."""
    return p.px(radius - (radius - outline) / math.sqrt(2) + 1)


class RoundCard(ttk.Frame):
    """A card with rounded corners. Content goes into `.body`, a plain tk frame of the fill
    colour; `set_border` recolours the outline (hover, selection)."""

    def __init__(self, master, app, fill: str, border: str | None, radius: float = 12, outer: str = palette.CARD, padx: int = 6, pady: int = 4, **kw):
        self._look = dict(fill=fill, radius=radius, outer=outer)
        self._app = app
        # the body keeps clear of the corners and the outline: ttk does not inset a frame's
        # content by its image, so the inset is the frame's own padding
        super().__init__(master, style=app.round_style(fill, border, radius=radius, outer=outer), padding=corner_inset(app.painter, radius), **kw)
        self.body = tk.Frame(self, bg=fill, padx=padx, pady=pady)
        self.body.pack(fill="both", expand=True)

    def set_border(self, border: str | None, width: float = 1.0) -> None:
        self.configure(style=self._app.round_style(self._look["fill"], border, radius=self._look["radius"], outer=self._look["outer"], width=width))


class RoundText(tk.Text):
    """A multi-line field in a rounded box that lights up on focus. Placing the widget
    (pack, grid, place) places the box around it."""

    def __init__(self, master, app, outer: str = palette.WHITE, fill: str = palette.WHITE, border: str = palette.BORDER, focus: str = palette.ACCENT, **kw):
        self.box = ttk.Frame(master, style=app.round_style(fill, border, radius=8, outer=outer), padding=corner_inset(app.painter, 8))
        self._styles = (self.box.cget("style"), app.round_style(fill, focus, radius=8, outer=outer, width=1.5))
        kw.setdefault("padx", 4)
        kw.setdefault("pady", 2)
        super().__init__(self.box, bd=0, relief="flat", highlightthickness=0, bg=fill, **kw)
        tk.Text.pack_configure(self, fill="both", expand=True)
        self.bind("<FocusIn>", lambda _e: self.box.configure(style=self._styles[1]), add="+")
        self.bind("<FocusOut>", lambda _e: self.box.winfo_exists() and self.box.configure(style=self._styles[0]), add="+")

    def pack_configure(self, *args, **kw):
        return self.box.pack_configure(*args, **kw)

    def grid_configure(self, *args, **kw):
        return self.box.grid_configure(*args, **kw)

    def place_configure(self, *args, **kw):
        return self.box.place_configure(*args, **kw)

    def pack_forget(self):
        return self.box.pack_forget()

    def grid_forget(self):
        return self.box.grid_forget()

    def destroy(self):
        box = self.box
        super().destroy()
        if box.winfo_exists():
            box.destroy()

    pack = pack_configure
    grid = grid_configure
    place = place_configure
