"""LED effects.

Each frame the engine calls `update(analyzer)` once, then `render(layout, ...)`
for every device, which returns a (layout.n, 3) float array of RGB in 0..1.
Keys in a 2-D grid get the full effect; LEDs on strips (fans, ARGB, zones)
get a 1-D version of it.
"""

import colorsys
import time
import weakref

import numpy as np

import layout as layouts
import sprites

# Few-LED devices are rendered on this strip and averaged down (see render()).
VIRTUAL_STRIP = layouts.strip(16)


# ---------------------------------------------------------------- palettes

def _gradient(*stops):
    stops = np.array(stops, dtype=float) / 255.0
    pos = np.linspace(0, 1, len(stops))

    def fn(t):
        t = np.clip(np.asarray(t, dtype=float), 0, 1)
        return np.stack([np.interp(t, pos, stops[:, i]) for i in range(3)], axis=-1)
    return fn


def _rainbow(t):
    t = np.atleast_1d(np.asarray(t, dtype=float))
    return np.array([colorsys.hsv_to_rgb(h % 1.0, 1, 1) for h in t * 0.85])


PALETTES = {
    "Rainbow": _rainbow,
    "Fire": _gradient((255, 0, 0), (255, 60, 0), (255, 140, 0), (255, 220, 40)),
    "Ocean": _gradient((0, 20, 255), (0, 120, 255), (0, 230, 200), (180, 255, 255)),
    "Synthwave": _gradient((120, 0, 255), (255, 0, 180), (255, 60, 90), (0, 220, 255)),
    "Forest": _gradient((0, 120, 20), (60, 255, 0), (200, 255, 0), (255, 200, 0)),
    "Meter": _gradient((0, 255, 0), (160, 255, 0), (255, 200, 0), (255, 0, 0)),
}


def solid_palette(rgb):
    color = np.array(rgb, dtype=float) / 255.0

    def fn(t):
        t = np.atleast_1d(np.asarray(t, dtype=float))
        return np.tile(color, (len(t), 1))
    return fn


# ---------------------------------------------------------------- helpers

def _band_levels(a, pos):
    band = np.minimum((pos * a.num_bands).astype(int), a.num_bands - 1)
    return a.bands[band]


def _fill(level, height):
    """How lit a key is when a bar of `level` fills the grid from the bottom."""
    return np.clip((level * 1.1 - height * 0.9) * 4, 0, 1)


def _summarize(colors, n):
    """Averages a strip down to n LEDs, keeping each segment's peak brightness."""
    out = []
    for seg in np.array_split(colors, n):
        mean = seg.mean(axis=0)
        peak = seg.max()
        out.append(mean * (peak / mean.max()) if mean.max() > 1e-6 else mean)
    return np.array(out)


def render(effect, lay, a, palette, spatial):
    if lay.tiny:
        return _summarize(effect.render(VIRTUAL_STRIP, a, palette, spatial), lay.n)
    return effect.render(lay, a, palette, spatial)


# ---------------------------------------------------------------- effects

class Effect:
    def update(self, a):
        pass

    def render(self, lay, a, palette, spatial):
        raise NotImplementedError


class SpectrumBars(Effect):
    """Each key column is a frequency band - bass on the left, treble on the right."""

    def render(self, lay, a, palette, spatial):
        level = _band_levels(a, lay.xs)
        lit = np.where(lay.linear, level, _fill(level, lay.height))
        t = lay.xs if spatial else np.where(lay.linear, level, lay.height)
        return palette(t) * lit[:, None]


class MirrorSpectrum(Effect):
    """Bass in the middle, treble toward the outer edges of both halves."""

    def render(self, lay, a, palette, spatial):
        dist = np.abs(lay.xs - 0.5) * 2  # 0 center .. 1 edge
        level = _band_levels(a, dist)
        lit = np.where(lay.linear, level, _fill(level, lay.height))
        t = dist if spatial else np.where(lay.linear, level, lay.height)
        return palette(t) * lit[:, None]


class SpectrumGlow(Effect):
    """Every key glows with the level of its column's frequency band."""

    def render(self, lay, a, palette, spatial):
        level = _band_levels(a, lay.xs)
        return palette(lay.xs if spatial else level) * level[:, None]


class BeatPulse(Effect):
    """The whole keyboard pulses with the bass and changes color on every beat."""

    def __init__(self):
        self.pos = 0.0
        self.env = 0.0

    def update(self, a):
        if a.beat:
            self.pos = (self.pos + 0.17) % 1.0
            self.env = 1.0
        self.env = max(self.env * 0.86, a.bass)

    def render(self, lay, a, palette, spatial):
        falloff = 1 - np.hypot(lay.xs - 0.5, (lay.ys - 0.5) * 0.6) * (1 - self.env)
        return palette(np.full(lay.n, self.pos)) * (self.env * falloff)[:, None]


class Ripples(Effect):
    """Every beat sends a ring of color rippling out from the center."""

    def __init__(self):
        self.rings = []  # (start_time, palette position)
        self.pos = 0.0

    def update(self, a):
        now = time.monotonic()
        if a.beat:
            self.pos = (self.pos + 0.13) % 1.0
            self.rings.append((now, self.pos))
        self.rings = [r for r in self.rings if now - r[0] < 1.2]

    def render(self, lay, a, palette, spatial):
        now = time.monotonic()
        level = _band_levels(a, lay.xs)
        out = palette(lay.xs if spatial else level) * (level * 0.2)[:, None]
        dist = np.hypot(lay.xs - 0.5, (lay.ys - 0.45) * 0.35)
        for start, pos in self.rings:
            age = now - start
            ring = np.clip(1 - np.abs(dist - age * 0.55) / 0.07, 0, 1) * (1 - age / 1.2)
            out = np.maximum(out, palette(np.full(lay.n, pos)) * ring[:, None])
        return out


class VUMeter(Effect):
    """A volume meter: the left half shows the left channel, the right half the right."""

    def render(self, lay, a, palette, spatial):
        grid_lit = _fill(np.where(lay.left, a.vu[0], a.vu[1]), lay.height)
        strip_lit = _fill(np.full(lay.n, a.vu.mean()), lay.xs)  # strips fill from the start
        lit = np.where(lay.linear, strip_lit, grid_lit)
        return palette(np.where(lay.linear, lay.xs, lay.height)) * lit[:, None]


def _hue_colors(hues):
    """Fully saturated colors for an array of hues (0..1), vectorized."""
    h6 = (np.asarray(hues) % 1.0) * 6
    return np.stack([np.clip(np.abs(h6 - 3) - 1, 0, 1),
                     np.clip(2 - np.abs(h6 - 2), 0, 1),
                     np.clip(2 - np.abs(h6 - 4), 0, 1)], axis=-1)


class _Sprite:
    def __init__(self, kind, name, bitmap, direction, color, row_pick):
        self.kind, self.name = kind, name
        # Figures and animals face left: mirror them when they travel right.
        self.bitmap = bitmap[:, ::-1] if direction > 0 else bitmap
        self.h, self.w = bitmap.shape
        self.direction = direction
        self.color = color
        self.row_pick = row_pick  # 0..1, which rows it runs along on tall keyboards
        self.progress = 0.0       # 0 = entering, 1 = gone

    def mask(self, lay, cx, cy):
        """Which LEDs this sprite lights on `lay` (key centers cx, cy in key units)."""
        span = lay.sprite_width + self.w
        sx = (-self.w + self.progress * span if self.direction > 0
              else lay.sprite_width - self.progress * span)
        spare_rows = max(0, int(lay.depth) - self.h)
        sy = int(self.row_pick ** 2 * (spare_rows + 1))  # mostly the top rows
        col = np.floor(cx - sx).astype(int)
        row = np.floor(cy - sy).astype(int)
        inside = (col >= 0) & (col < self.w)
        col = np.clip(col, 0, self.w - 1)
        on_grid = inside & (row >= 0) & (row < self.h) & self.bitmap[np.clip(row, 0, self.h - 1), col]
        on_strip = inside & self.bitmap.any(axis=0)[col]  # strips show its silhouette
        return np.where(lay.linear, on_strip, on_grid)


class Madness(Effect):
    """Random colors everywhere, keys follow random frequencies, and shapes, figures, words and animals scroll across."""

    REF_WIDTH = 20.0   # sprites cross every device in the time they'd take on a 20-key board
    MAX_SPRITES = 2
    SPAWN_EVERY_S = 5.0

    def __init__(self):
        self.rng = np.random.default_rng()
        self.state = weakref.WeakKeyDictionary()  # layout -> per-key colors, bands, centers
        self.beats = 0
        self.reroll = 0.0  # chance per frame that a key gets a new color
        self.kick = 0.0    # brightness surge after a beat
        self.sprites = []
        self.last = time.monotonic()
        self.last_spawn = self.last

    def update(self, a):
        now = time.monotonic()
        dt, self.last = min(0.1, now - self.last), now
        if a.beat:
            self.beats += 1
            self.kick = 1.0
        self.kick *= 0.8
        self.reroll = 0.02 + 0.35 * float(a.bands[-5:].mean())

        speed = 6 + 16 * float(a.bands.mean())  # key widths per second
        for sp in self.sprites:
            sp.progress += speed * dt / (self.REF_WIDTH + sp.w)
        self.sprites = [sp for sp in self.sprites if sp.progress < 1]
        if not a.silent and len(self.sprites) < self.MAX_SPRITES and (
                (a.beat and self.rng.random() < 0.35) or now - self.last_spawn > self.SPAWN_EVERY_S):
            self._spawn()
            self.last_spawn = now

    def _spawn(self):
        kind, name, bitmap = sprites.random_sprite(self.rng)
        direction = -1 if kind == "characters" else int(self.rng.choice([-1, 1]))
        color = 0.55 * _hue_colors(self.rng.random()) + 0.45  # bright pastel, stands out
        self.sprites.append(_Sprite(kind, name, bitmap, direction, color, self.rng.random()))

    def render(self, lay, a, palette, spatial):
        st = self.state.get(lay)
        if st is None:
            st = {"beats": None,
                  "cx": lay.sprite_x,
                  "cy": np.array([y + h / 2 for x, y, w, h in lay.rects])}
            self.state[lay] = st
        if st["beats"] != self.beats:  # new device, or a beat: reshuffle everything
            st["colors"] = _hue_colors(self.rng.random(lay.n))
            st["bands"] = self.rng.integers(0, a.num_bands, lay.n)
            st["beats"] = self.beats
        else:
            redo = self.rng.random(lay.n) < self.reroll
            st["colors"][redo] = _hue_colors(self.rng.random(int(redo.sum())))

        level = a.bands[st["bands"]]
        floor = 0.0 if a.silent else 0.12  # keep color everywhere while music plays
        bright = np.clip(floor + level * 1.3 + self.kick * 0.5, 0, 1)
        if self.sprites:
            bright *= 0.5  # dim the background so the sprites read clearly
        out = st["colors"] * bright[:, None]
        for sp in self.sprites:
            hit = sp.mask(lay, st["cx"], st["cy"])
            out[hit] = sp.color * (0.75 + 0.25 * self.kick)
        return out


EFFECTS = {
    "Bars": SpectrumBars,
    "Mirror": MirrorSpectrum,
    "Glow": SpectrumGlow,
    "Pulse": BeatPulse,
    "Ripples": Ripples,
    "VU meter": VUMeter,
    "Madness": Madness,
}
