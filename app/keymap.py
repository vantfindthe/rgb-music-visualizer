"""Which keyboard key each Windows key code is, and which LEDs sit under each key.

A key id is the character the key types without Shift (US layout), or "space" / "enter".
Ported from Illumunda, with Enter (VK_RETURN) added: the original left it out, so Enter never reacted.
"""

VK = {0x20: "space", 0x0D: "enter"}
VK.update({0x30 + i: str(i) for i in range(10)})
VK.update({0x41 + i: chr(ord("a") + i) for i in range(26)})
VK.update({0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".", 0xBF: "/", 0xC0: "`",
           0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'"})
VK_OF = {key: vk for vk, key in VK.items()}

# Key id -> (row, column) in the Model 100's firmware matrix (4 rows x 16 columns), default QWERTY layer.
MODEL100_MATRIX = {"`": (1, 0), "space": (1, 8), "enter": (1, 9), "=": (1, 15), "'": (2, 15), "-": (3, 15)}
for _row, _cols, _chars in (
        (0, range(1, 6), "12345"), (0, range(10, 15), "67890"),
        (1, range(1, 6), "qwert"), (1, range(10, 15), "yuiop"),
        (2, range(1, 6), "asdfg"), (2, range(10, 15), "hjkl;"),
        (3, range(1, 6), "zxcvb"), (3, range(10, 15), "nm,./")):
    MODEL100_MATRIX.update({ch: (_row, _c) for ch, _c in zip(_chars, _cols)})


def model100_keymap():
    """Key id -> LED indices, in the Model 100's frame order (row * 16 + column)."""
    return {key: [row * 16 + col] for key, (row, col) in MODEL100_MATRIX.items()}
