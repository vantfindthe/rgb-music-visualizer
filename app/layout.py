"""Device LED layouts.

A Layout describes one device's LEDs, in the order the device expects them:
  rects   (x, y, w, h) per LED in key units, used to draw the preview
  xs, ys  normalized positions 0..1 used by effects (x: left->right, y: top->bottom)
  height  0 at the bottom row .. 1 at the top row
  linear  per-LED flag: LED is part of a strip (fan ring, ARGB strip, zone)
          rather than a 2-D key grid; effects use 1-D variants for these
  round   draw LEDs as dots instead of keycaps
  sprite_x, sprite_width
          key-unit x positions used to scroll pixel-art sprites (Madness);
          split keyboards close the gap between their halves here
"""

import numpy as np


class Layout:
    def __init__(self, rects, xs, ys, linear, round_leds=False, left=None):
        self.rects = list(rects)
        self.n = len(self.rects)
        self.xs = np.asarray(xs, dtype=float)
        self.ys = np.asarray(ys, dtype=float)
        self.linear = np.broadcast_to(np.asarray(linear, dtype=bool), (self.n,)).copy()
        self.round = round_leds
        span = self.ys.max() - self.ys.min() if self.n else 0
        self.height = (self.ys.max() - self.ys) / span if span > 0 else np.full(self.n, 0.5)
        self.left = self.xs < 0.5 if left is None else np.asarray(left, dtype=bool)
        xs_r = [x + w for x, y, w, h in self.rects] or [1]
        ys_r = [y + h for x, y, w, h in self.rects] or [1]
        self.width, self.depth = max(xs_r), max(ys_r)
        self.sprite_x = np.array([x + w / 2 for x, y, w, h in self.rects])
        self.sprite_width = self.width

    @property
    def tiny(self):
        """Few-LED strips (mouse zones, a single-color keyboard) are rendered on
        a virtual strip and averaged down, so they still show the whole mix."""
        return self.n < 6 and bool(self.linear.all())


def _norm(i, n):
    return i / (n - 1) if n > 1 else 0.5


def grid(cols, rows, cells=None):
    """A cols x rows key grid (row-major). cells: optional list of (col, row)."""
    cells = cells or [(c, r) for r in range(rows) for c in range(cols)]
    return Layout([(c, r, 1, 1) for c, r in cells],
                  [_norm(c, cols) for c, r in cells], [_norm(r, rows) for c, r in cells],
                  linear=False)


def strip(n, per_row=36):
    """n LEDs in a line (wrapped into rows for display)."""
    rects = [(i % per_row, i // per_row, 1, 1) for i in range(n)]
    return Layout(rects, [_norm(i, n) for i in range(n)], [0.5] * n, linear=True,
                  round_leds=True)


def stack(parts):
    """Stacks layouts vertically into one (keeping each part's own x/y range)."""
    rects, xs, ys, linear, left, y = [], [], [], [], [], 0.0
    for p in parts:
        rects += [(x, ry + y, w, h) for x, ry, w, h in p.rects]
        xs += list(p.xs)
        ys += list(p.ys)
        linear += list(p.linear)
        left += list(p.left)
        y += p.depth + 0.8
    return Layout(rects, xs, ys, linear, round_leds=all(p.round for p in parts), left=left)


# ---------------------------------------------------------------- Model 100

# Column stagger (y offset) for the finger columns 0-6 of each half.
_STAGGER = [0.35, 0.35, 0.15, 0.0, 0.1, 0.2, 0.3]


def model100():
    """Keyboardio Model 100 in firmware matrix order (4 x 16, row-major).
    Left hand is columns 0-7, right hand 8-15 (mirrored: col 15 is the outer
    edge). Positions are approximate - good enough for effects and preview."""
    left = {}
    for r in range(4):
        for c in range(6):
            left[(r, c)] = (c, r + _STAGGER[c], 1, 1)
    left[(0, 6)] = (6, _STAGGER[6], 1, 1)            # LED key
    left[(1, 6)] = (6, 1 + _STAGGER[6], 1, 1.5)      # Tab (tall)
    left[(2, 6)] = (6, 2.5 + _STAGGER[6], 1, 1.5)    # Esc (tall)
    left[(3, 6)] = (3.3, 5.0, 2.2, 0.9)              # Fn palm key
    left[(0, 7)] = (5.6, 4.1, 1, 1)                  # thumb arc: Ctrl
    left[(1, 7)] = (6.6, 4.25, 1, 1)                 # Backspace
    left[(2, 7)] = (7.6, 4.5, 1, 1)                  # Gui
    left[(3, 7)] = (8.55, 4.9, 1, 1)                 # Shift

    width, height = 19.4, 6.0
    rects = [None] * 64
    for (r, c), (x, y, w, h) in left.items():
        rects[r * 16 + c] = (x, y, w, h)
        rects[r * 16 + (15 - c)] = (width - x - w, y, w, h)
    xs = [(x + w / 2) / width for x, y, w, h in rects]
    ys = [(y + h / 2) / height for x, y, w, h in rects]
    lay = Layout(rects, xs, ys, linear=False)
    # Sprites jump the gap between the halves (left half ends at x=7, right starts at 12.4).
    gap = 5.4
    lay.sprite_x = np.where(lay.sprite_x > width / 2, lay.sprite_x - gap, lay.sprite_x)
    lay.sprite_width = width - gap
    return lay
