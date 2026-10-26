"""Pixel-art sprites for the Madness effect, 4 pixels tall so they fit the
Model 100's four key rows. '#' is lit. Figures and animals face left; they are
mirrored when they scroll right. Text always scrolls right to left."""

import numpy as np


def _bitmap(rows):
    return np.array([[ch == "#" for ch in row] for row in rows], dtype=bool)


SHAPES = {
    "heart": [".#.#.", "#####", ".###.", "..#.."],
    "star": ["..#..", "#####", ".###.", ".#.#."],
    "gem": [".##.", "####", "####", ".##."],
    "bolt": ["..##", ".##.", "###.", "#..."],
    "note": ["..##", "..#.", "###.", "###."],
    "arrow": [".#...", "#####", "#####", ".#..."],
}

FIGURES = {
    "smiley": [".#.#.", ".....", "#...#", ".###."],
    "invader": ["..#.#..", ".#####.", "##.#.##", "#.#.#.#"],
    "ghost": [".###.", "#.#.#", "#####", "#.#.#"],
    "pacman": [".###.", "..###", "..###", ".###."],
    "stick figure": [".#.", "###", ".#.", "#.#"],
    "robot": ["#.#", "###", "###", "#.#"],
}

ANIMALS = {
    "cat": ["#.#....", "###...#", ".#####.", ".#.#.#."],
    "fish": ["..###.#", ".#.####", ".######", "..###.#"],
    "bird": ["#...#", ".#.#.", "..#..", "....."],
    "dog": ["##.....", "###...#", ".######", ".#...#."],
    "snake": ["......##", ".##..#.#", "#..##...", "........"],
    "butterfly": ["#...#", "##.##", ".###.", "##.##"],
    "duck": [".##..", "##...", ".####", "..##."],
}

FONT = {
    "A": [".#.", "#.#", "###", "#.#"], "B": ["##.", "###", "#.#", "##."],
    "C": [".##", "#..", "#..", ".##"], "D": ["##.", "#.#", "#.#", "##."],
    "E": ["###", "##.", "#..", "###"], "F": ["###", "#..", "##.", "#.."],
    "G": [".##", "#..", "#.#", ".##"], "H": ["#.#", "###", "#.#", "#.#"],
    "I": ["###", ".#.", ".#.", "###"], "J": ["..#", "..#", "#.#", ".#."],
    "K": ["#.#", "##.", "#.#", "#.#"], "L": ["#..", "#..", "#..", "###"],
    "M": ["#...#", "##.##", "#.#.#", "#...#"], "N": ["#..#", "##.#", "#.##", "#..#"],
    "O": [".#.", "#.#", "#.#", ".#."], "P": ["##.", "#.#", "##.", "#.."],
    "Q": [".#.", "#.#", "##.", ".##"], "R": ["##.", "#.#", "##.", "#.#"],
    "S": [".##", "#..", "..#", "##."], "T": ["###", ".#.", ".#.", ".#."],
    "U": ["#.#", "#.#", "#.#", "###"], "V": ["#.#", "#.#", "#.#", ".#."],
    "W": ["#...#", "#.#.#", "##.##", "#...#"], "X": ["#.#", ".#.", ".#.", "#.#"],
    "Y": ["#.#", "#.#", ".#.", ".#."], "Z": ["###", "..#", ".#.", "###"],
    "0": ["###", "#.#", "#.#", "###"], "1": [".#.", "##.", ".#.", "###"],
    "2": ["##.", "..#", ".#.", "###"], "3": ["##.", ".##", "..#", "##."],
    "4": ["#.#", "#.#", "###", "..#"], "5": ["###", "##.", "..#", "##."],
    "6": ["#..", "###", "#.#", "###"], "7": ["###", "..#", ".#.", ".#."],
    "8": ["###", "###", "#.#", "###"], "9": ["###", "#.#", "###", "..#"],
    "!": ["#", "#", ".", "#"], "?": ["##.", "..#", ".#.", ".#."],
    " ": ["..", "..", "..", ".."],
}

WORDS = ["MADNESS", "BASS", "DROP", "WOW", "LOUD", "YEAH", "HYPE", "BOOM", "VIBE",
         "PARTY", "OMG", "WILD", "NOISE", "MORE!", "UP!"]

SHAPE_BITMAPS = {name: _bitmap(rows) for name, rows in SHAPES.items()}
FIGURE_BITMAPS = {name: _bitmap(rows) for name, rows in FIGURES.items()}
ANIMAL_BITMAPS = {name: _bitmap(rows) for name, rows in ANIMALS.items()}


def text(s):
    """Bitmap of a string in the 4-pixel font, one blank column between glyphs."""
    cols = []
    for ch in s.upper():
        glyph = _bitmap(FONT.get(ch, FONT["?"]))
        cols.append(glyph)
        cols.append(np.zeros((4, 1), dtype=bool))
    return np.hstack(cols[:-1])


def random_sprite(rng):
    """Returns (kind, name, bitmap). Kinds: shape, figure, characters, animal."""
    kind = str(rng.choice(["shape", "figure", "characters", "animal"]))
    if kind == "characters":
        if rng.random() < 0.6:
            s = str(rng.choice(WORDS))
        else:
            chars = [c for c in FONT if c != " "]
            s = "".join(rng.choice(chars, size=rng.integers(1, 4)))
        return kind, s, text(s)
    pool = {"shape": SHAPE_BITMAPS, "figure": FIGURE_BITMAPS, "animal": ANIMAL_BITMAPS}[kind]
    name = str(rng.choice(list(pool)))
    return kind, name, pool[name]
