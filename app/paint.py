"""Paint: your own lighting, LED by LED, with animations that react to keys and sound.

Ported from Illumunda's paint scene, with its bugs fixed (see tests/test_paint.py):
  * every read and write of the painted colors goes through one lock, since the window paints
    while the render thread reads them;
  * a layout is written to a temporary file and swapped in, so a crash can't leave half a layout;
  * renaming keeps the open painting and its undo history, and fails loudly instead of silently;
  * Sparkle only lights LEDs that are painted;
  * Rainbow works on the whole frame at once instead of one LED at a time.
"""

import copy
import json
import math
import os
import re
import threading
from pathlib import Path

import numpy as np

ANIMATIONS = ("Static", "Breathe", "Blink", "Wave", "Rainbow", "Sparkle")
REACTIONS = ("Nothing", "Keystrokes", "Audio")
SHAPES = ("Circle", "Square")
TOOLS = ("Brush", "Erase", "Pick")
PALETTE = [(255, 40, 40), (255, 140, 0), (255, 230, 0), (60, 255, 60), (0, 230, 255), (40, 110, 255),
           (170, 40, 255), (255, 60, 190), (255, 255, 255)]
HOT = np.array([1.0, 0.25, 0.0])        # the heatmap's color
KEY_FLASH_S = 0.6                       # how long a pressed key stays lit under "Keystrokes"
HEAT_PER_PRESS = 0.25
MAX_UNDO = 40
NAME_OK = re.compile(r"[^A-Za-z0-9 _.-]")
# New layouts are named from the phonetic alphabet, then "Alpha 2", "Bravo 2", ...
CALL_SIGNS = ("Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel", "India", "Juliett",
              "Kilo", "Lima", "Mike", "November", "Oscar", "Papa", "Quebec", "Romeo", "Sierra", "Tango",
              "Uniform", "Victor", "Whiskey", "X-ray", "Yankee", "Zulu")

# The Paint settings, and the limits each numeric one is kept within (the sliders use the same).
DEFAULT_SETTINGS = {
    "tool": "Brush", "color": [255, 230, 0], "animation": "Static", "react": "Nothing",
    "intensity": 100, "reactivity": 60, "heatmap": 0, "size": 1, "weight": 100,
    "shape": "Circle", "cycle": 20, "heat_fade": 15,     # cycle and heat_fade are in tenths of a second
}
RANGES = {"intensity": (0, 100), "reactivity": (0, 100), "heatmap": (0, 100), "size": (1, 6),
          "weight": (0, 100), "cycle": (2, 100), "heat_fade": (1, 100)}
ENUMS = {"animation": ANIMATIONS, "react": REACTIONS, "tool": TOOLS, "shape": SHAPES}


def clamp(key, value):
    """A numeric setting's value, as an int within its range."""
    lo, hi = RANGES[key]
    return int(min(hi, max(lo, round(value))))


def clean_settings(loaded):
    """The Paint settings, with anything missing or out of range replaced by its default."""
    out = copy.deepcopy(DEFAULT_SETTINGS)
    if not isinstance(loaded, dict):
        return out
    for key, value in loaded.items():
        if key in ENUMS:
            if value in ENUMS[key]:
                out[key] = value
        elif key == "color":
            if isinstance(value, list) and len(value) == 3 and all(_is_number(c) for c in value):
                out[key] = [int(min(255, max(0, c))) for c in value]
        elif key in RANGES and _is_number(value):
            out[key] = clamp(key, value)
    return out


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def clean_name(name):
    """A safe layout name (it becomes a file name): letters, digits, space, _ . - only."""
    return NAME_OK.sub("", str(name)).strip(" .")[:40]


def _nonblank(devices):
    """{device id: pixel bytes} for devices with any paint on them (blank ones don't count)."""
    return {dev_id: px.tobytes() for dev_id, px in devices.items() if px.any()}


class PaintState:
    """The painted colors of every device, and the saved layouts they're kept in.

    The window edits this while the render thread reads it, so every access to `pixels` happens
    under `lock`. Frames are read as copies, so nothing outside the lock holds on to a live array.
    """

    def __init__(self, folder):
        self.folder = Path(folder)
        self.pixels = {}      # device id -> (n, 3) uint8
        self.undo = {}        # undo key -> [{device id: pixels before a stroke}]
        self.name = ""        # the saved layout being edited ("" = none yet)
        self.lock = threading.RLock()

    # ---- painted colors

    def ensure(self, dev_id, n):
        """The device's painted colors (blank on first use, or when its LED count changed)."""
        with self.lock:
            px = self.pixels.get(dev_id)
            if px is None or len(px) != n:
                px = self.pixels[dev_id] = np.zeros((n, 3), dtype=np.uint8)
            return px

    def frame_base(self, dev_id, n):
        """A copy of the device's painted colors, safe to use outside the lock."""
        with self.lock:
            return self.ensure(dev_id, n).copy()

    def pixel(self, dev_id, led):
        with self.lock:
            px = self.pixels.get(dev_id)
            return [0, 0, 0] if px is None or led >= len(px) else [int(v) for v in px[led]]

    def snapshot(self, key, dev_ids):
        """Remembers the given devices' paint, so one Undo under `key` restores them."""
        with self.lock:
            stack = self.undo.setdefault(key, [])
            stack.append({i: self.pixels[i].copy() for i in dev_ids if i in self.pixels})
            del stack[:-MAX_UNDO]

    def undo_last(self, key):
        with self.lock:
            stack = self.undo.get(key)
            if not stack:
                return False
            for dev_id, px in stack.pop().items():
                self.pixels[dev_id] = px
            return True

    def apply(self, dev_id, hits, rgb):
        """hits: [(led, weight 0..1)]. Blends rgb into each LED by its weight."""
        with self.lock:
            px = self.pixels.get(dev_id)
            if px is None:
                return
            color = np.asarray(rgb, dtype=float)
            for led, w in hits:
                px[led] = np.clip(px[led] * (1 - w) + color * w + 0.5, 0, 255).astype(np.uint8)

    def fill(self, dev_id, rgb, n):
        with self.lock:
            self.ensure(dev_id, n)[:] = rgb

    def clear(self, dev_id, n):
        self.fill(dev_id, (0, 0, 0), n)

    def reset_all(self):
        with self.lock:
            self.pixels.clear()
            self.undo.clear()
            self.name = ""

    def set_name(self, name):
        with self.lock:
            self.name = name

    # ---- saved layouts

    def names(self):
        try:
            return sorted(p.stem for p in self.folder.glob("*.json") if not p.stem.startswith("_"))
        except OSError:
            return []

    def _path(self, name):
        return self.folder / f"{name}.json"

    def _write(self, name, devices_hex):
        """Writes a layout atomically: a crash mid-write leaves the old file, never a torn one."""
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self._path(name)
        tmp = self.folder / f"{name}.json.tmp"
        tmp.write_text(json.dumps({"devices": devices_hex}), encoding="utf-8")
        os.replace(tmp, path)

    def _hex(self):
        with self.lock:
            return {dev_id: px.tobytes().hex() for dev_id, px in self.pixels.items()}

    def save(self, name):
        """Saves the painting as layout `name` and opens it. Returns the saved name ("" if the name
        is empty). Raises OSError if the file can't be written."""
        name = clean_name(name)
        if not name:
            return ""
        self._write(name, self._hex())
        self.set_name(name)
        return name

    def autosave(self):
        """Keeps the painting across restarts, without changing which layout is open."""
        self._write("_autosave", self._hex())

    def read(self, name):
        """The painting stored in layout `name` as {device id: pixels}, or None if it's missing or
        damaged."""
        name = clean_name(name)
        if not name:
            return None
        try:
            raw = json.loads(self._path(name).read_text(encoding="utf-8"))["devices"]
            out = {}
            for dev_id, hexed in raw.items():
                px = np.frombuffer(bytes.fromhex(hexed), dtype=np.uint8)
                if px.size % 3:
                    return None
                out[str(dev_id)] = px.reshape(-1, 3).copy()
            return out
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return None

    def load(self, name):
        """Opens layout `name`, replacing the painting on screen. Returns False, and changes nothing,
        if the layout is missing or damaged."""
        data = self.read(name)
        if data is None:
            return False
        with self.lock:
            self.pixels, self.undo, self.name = data, {}, clean_name(name)
        return True

    def differs_from_saved(self):
        """True if the painting on screen isn't what the open layout holds on disk."""
        saved = self.read(self.name) if self.name else None
        with self.lock:
            current = _nonblank(self.pixels)
        return current != _nonblank(saved or {})

    def new_name(self):
        """The first call sign no saved layout uses."""
        taken = {n.lower() for n in self.names()}
        for round_ in range(1, 1000):
            for sign in CALL_SIGNS:
                name = sign if round_ == 1 else f"{sign} {round_}"
                if name.lower() not in taken:
                    return name
        return f"Layout {len(taken) + 1}"

    def rename(self, old, new):
        """Renames saved layout `old` to `new`. The painting on screen and its undo history are kept.
        Returns the new name, or "" if it can't be done: the new name is empty or used by another
        layout, or `old` doesn't exist."""
        old, new = clean_name(old), clean_name(new)
        if not old or not new:
            return ""
        same = new.lower() == old.lower()
        if not same and new.lower() in {n.lower() for n in self.names()}:
            return ""
        data = self.read(old)
        if data is None:
            return ""
        self._write(new, {dev_id: px.tobytes().hex() for dev_id, px in data.items()})
        if not same:
            try:
                self._path(old).unlink()
            except OSError:
                pass
        with self.lock:
            if self.name.lower() == old.lower():
                self.name = new
        return new

    def delete(self, name):
        name = clean_name(name)
        try:
            self._path(name).unlink()
        except OSError:
            return False
        with self.lock:
            if self.name.lower() == name.lower():
                self.name = ""
        return True


# ---------------------------------------------------------------- keystrokes

class KeyMemory:
    """When each key was last pressed, and how hot it is (in memory only, fading in seconds).
    Only which key was pressed is kept, never what was typed."""

    def __init__(self):
        self.lock = threading.Lock()
        self.last = {}        # key id -> when it was last pressed
        self.heat = {}        # key id -> (heat 0..1, when it was set)
        self.fade_s = 1.5     # heat halves every ~0.7 of this; the engine keeps it in step with the setting

    def press(self, key, now):
        with self.lock:
            h, at = self.heat.get(key, (0.0, now))
            self.heat[key] = (min(1.0, self._decayed(h, at, now) + HEAT_PER_PRESS), now)
            self.last[key] = now

    def _decayed(self, h, at, now):
        return h * math.exp(-(now - at) / max(0.1, self.fade_s))

    def maps(self, keymap, n, now):
        """(heat, pressed) per LED for a device whose keys map to LEDs as `keymap` says."""
        heat, pressed = np.zeros(n), np.zeros(n)
        with self.lock:
            heats, last = dict(self.heat), dict(self.last)
        for key, leds in keymap.items():
            if key in heats:
                h, at = heats[key]
                heat[leds] = self._decayed(h, at, now)
            if key in last:
                pressed[leds] = max(0.0, 1 - (now - last[key]) / KEY_FLASH_S)
        return heat, pressed


# ---------------------------------------------------------------- animation

def hsv_rotate(rgb, shift):
    """Rotates the hue of (n, 3) floats 0..1 by `shift` (in turns). Black stays black."""
    rgb = np.asarray(rgb, dtype=float)
    r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    v = rgb.max(axis=1)
    delta = v - rgb.min(axis=1)
    safe = np.where(delta > 0, delta, 1.0)
    h = np.where(v == r, (g - b) / safe % 6, np.where(v == g, (b - r) / safe + 2, (r - g) / safe + 4))
    h = np.where(delta > 0, h / 6, 0.0)
    s = np.where(v > 0, delta / np.where(v > 0, v, 1.0), 0.0)
    h = (h + shift) % 1.0
    i = np.floor(h * 6).astype(int) % 6
    f = h * 6 - np.floor(h * 6)
    p = v * (1 - s)
    q = v * (1 - s * f)
    t = v * (1 - s * (1 - f))
    sectors = ((v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q))
    out = np.empty_like(rgb)
    for k, (R, G, B) in enumerate(sectors):
        m = i == k
        out[m] = np.stack([R[m], G[m], B[m]], axis=-1)
    return out


def animate(base, lay, t, p, level=0.0, heat=None, pressed=None):
    """base (n, 3) uint8 painted colors -> (n, 3) floats 0..1 for this moment.

    level    audio level 0..1 (used when react is "Audio")
    heat     (n,) 0..1 per LED: how recently and how often its key was pressed
    pressed  (n,) 0..1 per LED: how fresh a key press is (react is "Keystrokes")
    """
    out = np.asarray(base, dtype=float) / 255.0
    cycle = max(0.2, p["cycle"] / 10.0)
    ph = t / cycle
    anim = p["animation"]
    if anim == "Breathe":
        out = out * (0.55 + 0.45 * math.sin(ph * 2 * math.pi))
    elif anim == "Blink":
        out = out * (1.0 if ph % 1.0 < 0.5 else 0.06)
    elif anim == "Wave":
        out = out * (0.55 + 0.45 * np.sin((ph - lay.xs) * 2 * math.pi))[:, None]
    elif anim == "Rainbow":
        out = hsv_rotate(out, ph % 1.0)
    elif anim == "Sparkle":
        spark = np.random.default_rng(int(t * 8)).random(len(out)) < 0.09
        lit = spark & (out.max(axis=1) > 0)          # only painted LEDs sparkle
        out[lit] = out[lit] * 0.3 + 0.7

    gain = p["reactivity"] / 100.0
    if p["react"] == "Audio":
        out = out * (1.0 - gain * (1.0 - min(1.0, max(0.0, level))))
    elif p["react"] == "Keystrokes" and pressed is not None:
        k = np.clip(pressed, 0, 1)[:, None] * gain
        out = out * (1 - k) + k

    if heat is not None and p["heatmap"] > 0:
        k = (np.clip(heat, 0, 1) * p["heatmap"] / 100.0)[:, None]
        out = out * (1 - k) + HOT * k
    return np.clip(out * p["intensity"] / 100.0, 0, 1)


def spacing(centers):
    """The typical distance between neighbouring LEDs: the median of each LED's nearest neighbour.
    40 for a single LED, so a brush still has a size."""
    if len(centers) < 2:
        return 40.0
    diff = centers[:, None, :] - centers[None, :, :]
    dist = np.hypot(diff[..., 0], diff[..., 1])
    np.fill_diagonal(dist, np.inf)
    return max(1.0, float(np.median(dist.min(axis=1))))


def brush_hits(centers, unit, x, y, size, weight, square):
    """[(led, weight)] under a brush at (x, y). `centers` is (n, 2) LED positions, `unit` the typical
    spacing between them. The LED under the brush's center comes first, at full strength."""
    if len(centers) == 0:
        return []
    dx, dy = np.abs(centers[:, 0] - x), np.abs(centers[:, 1] - y)
    dist = np.maximum(dx, dy) if square else np.hypot(dx, dy)
    nearest = int(np.argmin(dist))
    if dist[nearest] > unit * (size + 0.2):
        return []
    out = [(nearest, 1.0)]
    reach = unit * (size - 0.5) + 1e-6
    for i in np.where(dist <= reach)[0]:
        if i != nearest:
            out.append((int(i), weight))
    return out
