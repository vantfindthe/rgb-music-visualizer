"""Custom-drawn widgets and the color theme."""

import tkinter as tk
from tkinter import font as tkfont

import numpy as np
from PIL import Image, ImageDraw

import audio
import paint

# ---------------------------------------------------------------- theme

BG = "#0e0e13"
CARD = "#16161d"
CARD_BORDER = "#23232d"
STAGE = "#0a0a0e"
KEY_OFF = "#20202a"
KEY_OUTLINE = "#2b2b37"
TEXT = "#ececf1"
MUTED = "#8b8b9a"
FAINT = "#555563"
ACCENT = "#7c5cff"
ACCENT_HOVER = "#6947f5"
FIELD = "#1e1e27"
FIELD_HOVER = "#2a2a35"
TRACK = "#1f1f29"
FONT = "Segoe UI"
FONT_DISPLAY = "Segoe UI Variable Display"


def rgb01(hex_color):
    return np.array([int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]) / 255.0


def to_hex(rgb):
    r, g, b = (int(round(float(np.clip(c, 0, 1)) * 255)) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def rounded_points(x0, y0, x1, y1, r):
    """Polygon points for a rounded rectangle (draw with smooth=True)."""
    r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    return [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1,
            x1 - r, y1, x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]


def gradient_image(colors, size, radius):
    """A rounded, horizontally graded swatch. colors: (n, 3) floats 0..1."""
    w, h = size
    colors = np.asarray(colors, dtype=float)
    xs = np.linspace(0, 1, w)
    pos = np.linspace(0, 1, len(colors))
    row = np.stack([np.interp(xs, pos, colors[:, i]) for i in range(3)], axis=-1)
    pixels = (np.repeat(row[None, :, :], h, axis=0) * 255).astype(np.uint8)
    img = Image.fromarray(pixels, "RGB").convert("RGBA")
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    img.putalpha(mask)
    return img


def app_icon(size=256):
    """Rounded accent square with four equalizer bars."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 4, fill=ACCENT)
    bar_w, gap = size * 0.12, size * 0.07
    x = (size - (4 * bar_w + 3 * gap)) / 2
    for h in (0.35, 0.62, 0.48, 0.25):
        top = size * (0.78 - h * 0.9)
        d.rounded_rectangle((x, top, x + bar_w, size * 0.78), radius=bar_w / 2, fill="white")
        x += bar_w + gap
    return img


# ---------------------------------------------------------------- device preview

class DevicePreview(tk.Canvas):
    """Draws a device's LEDs - keycaps for key grids, dots for strips - glowing
    in the current colors."""

    def __init__(self, master, scale=1.0, on_paint=None, **kw):
        super().__init__(master, bg=STAGE, highlightthickness=0, **kw)
        self.scale = scale
        self.layout = None
        self.title = ""
        self.items = []
        self.colors = np.zeros((0, 3))
        self.centers = np.zeros((0, 2))  # where each LED is drawn, in canvas pixels
        self.unit = 40.0                 # typical spacing between LEDs, in canvas pixels
        self.on_paint = on_paint         # called as on_paint(x, y, start) for a click (start) or drag
        self._base = rgb01(KEY_OFF)
        self._stage = rgb01(STAGE)
        self.font = tkfont.Font(family=FONT, size=-round(12 * scale))
        self.bind("<Configure>", lambda e: self._layout())
        self.bind("<Button-1>", lambda e: self._press(e, True))
        self.bind("<B1-Motion>", lambda e: self._press(e, False))

    def set_layout(self, lay, title=""):
        self.layout, self.title = lay, title
        self.colors = np.zeros((lay.n if lay else 0, 3))
        self._layout()

    def hits(self, x, y, size, weight, square):
        """[(led, weight)] under a brush at canvas (x, y): see paint.brush_hits."""
        return paint.brush_hits(self.centers, self.unit, x, y, size, weight, square)

    def _press(self, e, start):
        if self.on_paint is not None and self.layout is not None:
            self.on_paint(e.x, e.y, start)

    def _layout(self):
        self.delete("all")
        self.items = []
        self.centers = np.zeros((0, 2))
        w, h = self.winfo_width(), self.winfo_height()
        lay = self.layout
        if lay is None:
            self.create_text(w / 2, h / 2, text="No device selected", fill=FAINT, font=self.font)
            return
        self.create_text(14 * self.scale, 12 * self.scale, text=self.title, anchor="nw",
                         fill=FAINT, font=self.font)
        margin = 0.06 * min(w, h) + 22 * self.scale
        s = min((w - 2 * margin) / lay.width, (h - 2 * margin) / lay.depth, 60 * self.scale)
        ox, oy = (w - lay.width * s) / 2, (h - lay.depth * s) / 2
        gap, radius, glow = 0.07 * s, 0.2 * s, 0.13 * s
        for x, y, kw, kh in lay.rects:
            x0, y0 = ox + x * s + gap, oy + y * s + gap
            x1, y1 = ox + (x + kw) * s - gap, oy + (y + kh) * s - gap
            if lay.round:
                halo = self.create_oval(x0 - glow, y0 - glow, x1 + glow, y1 + glow,
                                        fill=STAGE, outline="")
                key = self.create_oval(x0, y0, x1, y1, fill=KEY_OFF, outline=KEY_OUTLINE)
            else:
                halo = self.create_polygon(
                    rounded_points(x0 - glow, y0 - glow, x1 + glow, y1 + glow, radius + glow),
                    smooth=True, fill=STAGE, outline="")
                key = self.create_polygon(rounded_points(x0, y0, x1, y1, radius), smooth=True,
                                          fill=KEY_OFF, outline=KEY_OUTLINE, width=1)
            self.items.append((halo, key))
        if self.items:
            self.centers = np.array([[ox + (x + kw / 2) * s, oy + (y + kh / 2) * s]
                                     for x, y, kw, kh in lay.rects])
            self.unit = paint.spacing(self.centers)
        self.show(self.colors)

    def show(self, colors):
        if colors is None or len(colors) != len(self.items):
            return  # frame for a different layout (the selection just changed)
        self.colors = colors
        for (halo, key), c in zip(self.items, colors):
            if float(c.max()) < 0.02:
                self.itemconfigure(halo, fill=STAGE)
                self.itemconfigure(key, fill=KEY_OFF, outline=KEY_OUTLINE)
                continue
            fill = np.maximum(self._base, c)
            self.itemconfigure(halo, fill=to_hex(self._stage + (c - self._stage) * 0.28))
            self.itemconfigure(key, fill=to_hex(fill), outline=to_hex(fill + (1 - fill) * 0.35))


# ---------------------------------------------------------------- equalizer

class Equalizer(tk.Canvas):
    """Graphic EQ: one column per band, with a live level meter behind a
    draggable gain handle. Drag across columns to paint a curve,
    double-click to zero a band, scroll to nudge it by 1 dB."""

    def __init__(self, master, gains, range_db, scale, on_change=None, **kw):
        super().__init__(master, bg=CARD, highlightthickness=0, cursor="hand2", **kw)
        self.gains = gains  # list of ints, edited in place
        self.range_db = range_db
        self.scale = scale
        self.on_change = on_change
        self.levels = np.zeros(audio.NUM_BANDS)
        self.band_colors = np.tile(rgb01(ACCENT), (audio.NUM_BANDS, 1))
        self._last_col = None
        self.meters, self.handles, self.values = [], [], []  # created in _layout()
        self.font_small = tkfont.Font(family=FONT, size=-round(11 * scale))
        self.font_value = tkfont.Font(family=FONT, size=-round(11 * scale), weight="bold")
        self.bind("<Configure>", lambda e: self._layout())
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_last_col", None))
        self.bind("<Double-Button-1>", self._reset_band)
        self.bind("<MouseWheel>", self._wheel)

    # geometry -----------------------------------------------------------

    def _layout(self):
        self.delete("all")
        sc = self.scale
        w, h = self.winfo_width(), self.winfo_height()
        self.x0, self.x1 = 40 * sc, w - 8 * sc
        self.y0, self.y1 = 10 * sc, h - 40 * sc
        self.col_w = (self.x1 - self.x0) / audio.NUM_BANDS
        self.bar_w = min(self.col_w * 0.55, 30 * sc)
        mid = self._gain_y(0)

        for db in (self.range_db, 0, -self.range_db):
            self.create_text(self.x0 - 10 * sc, self._gain_y(db), text=f"{db:+d}" if db else "0 dB",
                             anchor="e", fill=FAINT, font=self.font_small)
        self.create_line(self.x0, mid, self.x1, mid, fill="#343442", dash=(2, 4))

        self.meters, self.handles, self.values = [], [], []
        for i, fc in enumerate(audio.band_centers()):
            cx = self._cx(i)
            bw = self.bar_w / 2
            self.create_polygon(rounded_points(cx - bw, self.y0, cx + bw, self.y1, bw),
                                smooth=True, fill=TRACK, outline="")
            self.meters.append(self.create_polygon(0, 0, 0, 0, 0, 0, smooth=True, outline=""))
            label = f"{fc / 1000:.1f}k" if 1000 <= fc < 10000 else (
                f"{fc / 1000:.0f}k" if fc >= 10000 else f"{fc:.0f}")
            self.create_text(cx, self.y1 + 13 * sc, text=label, fill=MUTED, font=self.font_small)
            self.values.append(self.create_text(cx, self.y1 + 29 * sc, font=self.font_value))

        self.curve = self.create_line(0, 0, 0, 0, fill=ACCENT, width=max(2, round(2 * sc)),
                                      smooth=True, capstyle="round")
        r = 6.5 * sc
        for i in range(audio.NUM_BANDS):
            self.handles.append(self.create_oval(0, 0, r, r, fill="#f4f4f8", outline=ACCENT,
                                                 width=max(2, round(2 * sc))))
        self._draw_gains()
        self.set_levels(self.levels, self.band_colors)

    def _cx(self, i):
        return self.x0 + (i + 0.5) * self.col_w

    def _gain_y(self, db):
        mid, half = (self.y0 + self.y1) / 2, (self.y1 - self.y0) / 2 - 7 * self.scale
        return mid - db / self.range_db * half

    def _y_gain(self, y):
        mid, half = (self.y0 + self.y1) / 2, (self.y1 - self.y0) / 2 - 7 * self.scale
        db = round((mid - y) / half * self.range_db)
        db = max(-self.range_db, min(self.range_db, db))
        return 0 if abs(db) <= 1 else db

    # drawing ------------------------------------------------------------

    def _draw_gains(self):
        if not self.handles:
            return
        r = 6.5 * self.scale
        pts = []
        for i, g in enumerate(self.gains):
            cx, cy = self._cx(i), self._gain_y(g)
            pts += [cx, cy]
            self.coords(self.handles[i], cx - r, cy - r, cx + r, cy + r)
            self.itemconfigure(self.values[i], text=f"{g:+d}" if g else "0",
                               fill=ACCENT if g else FAINT)
        self.coords(self.curve, *pts)

    def set_levels(self, levels, band_colors):
        self.levels, self.band_colors = levels, band_colors
        if not self.meters:
            return
        track = rgb01(TRACK)
        bw = self.bar_w / 2
        for i, (m, level) in enumerate(zip(self.meters, levels)):
            cx = self._cx(i)
            top = self.y1 - float(level) * (self.y1 - self.y0)
            if level < 0.01:
                self.coords(m, 0, 0, 0, 0, 0, 0)
                continue
            self.coords(m, *rounded_points(cx - bw, top, cx + bw, self.y1, bw))
            self.itemconfigure(m, fill=to_hex(track + (band_colors[i] - track) * 0.7))

    # interaction ----------------------------------------------------------

    def _col(self, x):
        return max(0, min(audio.NUM_BANDS - 1, int((x - self.x0) // self.col_w)))

    def _set(self, i, db):
        if self.gains[i] != db:
            self.gains[i] = db
            if self.on_change:
                self.on_change()

    def _press(self, e):
        self._last_col = self._col(e.x)
        self._set(self._last_col, self._y_gain(e.y))
        self._draw_gains()

    def _drag(self, e):
        col, db = self._col(e.x), self._y_gain(e.y)
        last = self._last_col if self._last_col is not None else col
        step = 1 if col >= last else -1
        for i in range(last, col + step, step):  # fill columns skipped by a fast drag
            self._set(i, db)
        self._last_col = col
        self._draw_gains()

    def _reset_band(self, e):
        self._set(self._col(e.x), 0)
        self._draw_gains()

    def _wheel(self, e):
        i = self._col(e.x)
        db = self.gains[i] + (1 if e.delta > 0 else -1)
        self._set(i, max(-self.range_db, min(self.range_db, db)))
        self._draw_gains()

    def redraw(self):
        self._draw_gains()
