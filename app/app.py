"""Music visualizer for RGB keyboards, mice and ARGB: the main window.

Two modes: Music (the effects react to the sound) and Paint (you paint each device's LEDs, and the
animations, key presses and sound act on your painting). See paint.py.
"""

import copy
import json
import os
import sys
import threading
from pathlib import Path
from tkinter import colorchooser, messagebox

import customtkinter as ctk
import numpy as np

import audio
import effects
import keyhook
import paint
from devices import scan_all
from engine import CUSTOM, Engine, palette_for
from version import __version__
from widgets import (ACCENT, ACCENT_HOVER, BG, CARD, CARD_BORDER, FAINT, FIELD, FIELD_HOVER,
                     FONT, FONT_DISPLAY, MUTED, STAGE, TEXT, TRACK, DevicePreview, Equalizer,
                     app_icon, gradient_image)

# Packaged builds keep settings in %APPDATA%, since the install folder may be read-only.
if getattr(sys, "frozen", False):
    APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "MusicVisualizer"
    APP_DIR.mkdir(parents=True, exist_ok=True)
else:
    APP_DIR = Path(__file__).parent
SETTINGS_FILE = APP_DIR / "settings.json"
LAYOUT_DIR = APP_DIR / "layouts"
ICON_FILE = APP_DIR / "icon.ico"
IS_WINDOWS = keyhook.IS_WINDOWS
EQ_RANGE_DB = 24
NO_KEYBOARD = "None (preview only)"
MODES = ("music", "paint")
SAVE_DELAY_MS = 800
DEFAULTS = {
    "mode": "music",
    "effect": "Bars",
    "palette": "Rainbow",
    "custom_color": [0, 180, 255],
    "sensitivity": 50,
    "smoothing": 50,
    "brightness": 70,
    "fps": 50,
    "release_on_silence": True,
    "eq": [0] * audio.NUM_BANDS,
    "paint": paint.DEFAULT_SETTINGS,
    "paint_layout": "",       # the saved layout being edited
    "paint_newest": "",       # the most recently created layout (shown with "(New)")
    "device": "",             # audio device
    "disabled_devices": [],   # ids of lighting devices switched off
    "preview_device": "",
}
# Names used by earlier versions of the app.
LEGACY_EFFECTS = {"Spectrum bars": "Bars", "Mirrored spectrum": "Mirror",
                  "Spectrum glow": "Glow", "Beat pulse": "Pulse"}

# state: (pill text, text color, pill color)
STATES = {
    "stopped": ("Stopped", MUTED, FIELD),
    "starting": ("Connecting", MUTED, FIELD),
    "streaming": ("Live", "#4ade80", "#13261a"),
    "resting": ("Waiting for music", "#7dd3fc", "#12222e"),
    "preview": ("Preview only", "#fbbf24", "#29220f"),
    "error": ("Device error", "#f87171", "#2c1616"),
}
# device (worker) state: dot color
DOTS = {"live": "#4ade80", "resting": "#7dd3fc", "connecting": "#fbbf24", "error": "#f87171"}


def load_settings():
    settings = copy.deepcopy(DEFAULTS)
    try:
        loaded = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            settings.update(loaded)
    except (OSError, ValueError):
        pass
    settings.pop("port", None)  # replaced by the device list
    settings["effect"] = LEGACY_EFFECTS.get(settings["effect"], settings["effect"])
    if settings["effect"] not in effects.EFFECTS:
        settings["effect"] = DEFAULTS["effect"]
    if settings["palette"] == "Custom color":
        settings["palette"] = CUSTOM
    if settings["palette"] not in effects.PALETTES and settings["palette"] != CUSTOM:
        settings["palette"] = DEFAULTS["palette"]
    if len(settings["eq"]) != audio.NUM_BANDS:
        settings["eq"] = list(DEFAULTS["eq"])
    if settings["mode"] not in MODES:
        settings["mode"] = DEFAULTS["mode"]
    settings["paint"] = paint.clean_settings(settings["paint"])
    for key in ("paint_layout", "paint_newest"):
        if not isinstance(settings[key], str):
            settings[key] = ""
    return settings


def _write_json(path, data):
    """Writes atomically, so a crash mid-write can't leave a torn file behind."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class App:
    def __init__(self, root):
        self.root = root
        self.engine = None
        self.settings = load_settings()
        self.scale = ctk.ScalingTracker.get_window_scaling(root)
        self.outputs = []       # every device found by the last scan
        self.rows = {}          # device id -> widgets of its row in the list
        self.selected = None    # device shown in the preview (and painted on)
        self.devices = []       # (label, index) of the audio devices
        self.ports = []
        self._scan_result = None
        self._scanning = False
        self._pill_state = None
        self._color_buttons = []
        self._layout_labels = {}
        self._layout_msg = ""
        self._paint_note_shown = None
        self._save_job = None
        self._dirty = False     # the painting on screen differs from the saved layout
        self.paint = paint.PaintState(LAYOUT_DIR)
        self.keymem = paint.KeyMemory()
        self.keymon = keyhook.KeyMonitor(self.keymem)

        root.title(f"Music Visualizer {__version__}")
        root.configure(fg_color=BG)
        root.geometry("1180x1000")
        root.minsize(1040, 960)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self._set_icon()

        self.f_title = ctk.CTkFont(FONT_DISPLAY, 22, "bold")
        self.f_body = ctk.CTkFont(FONT, 13)
        self.f_bold = ctk.CTkFont(FONT, 13, "bold")
        self.f_small = ctk.CTkFont(FONT, 12)
        self.f_section = ctk.CTkFont(FONT, 11, "bold")

        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(1, weight=1)
        self._load_paint_autosave()
        self._build_header()
        self._build_main()
        self._build_controls()
        self._build_paint_panel()
        self._build_equalizer()
        self._build_footer()
        self._on_palette_changed()
        self._apply_mode()
        self._update_run_state()
        self.refresh_audio()
        self.scan()
        self.tick()

    # ------------------------------------------------------------ helpers

    def _set_icon(self):
        try:
            if not ICON_FILE.exists():
                app_icon().save(ICON_FILE, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
            self.root.iconbitmap(str(ICON_FILE))
        except OSError:
            pass

    def _card(self, parent, title=None, **grid):
        card = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14, border_width=1,
                            border_color=CARD_BORDER)
        card.grid(**grid)
        card.grid_columnconfigure(0, weight=1)
        if title:
            self._section(card, title).grid(row=0, column=0, sticky="w", padx=18, pady=(14, 8))
        return card

    def _section(self, parent, title):
        return ctk.CTkLabel(parent, text=title.upper(), font=self.f_section, text_color=MUTED,
                            height=18)

    def _small_button(self, parent, text, command, width=64):
        return ctk.CTkButton(parent, text=text, font=self.f_small, width=width, height=26,
                             corner_radius=8, fg_color=FIELD, hover_color=FIELD_HOVER,
                             text_color=TEXT, command=command)

    def _menu(self, parent, values, command, width=200):
        return ctk.CTkOptionMenu(
            parent, values=values, command=command, width=width, font=self.f_body,
            dropdown_font=self.f_body, height=32, corner_radius=8, fg_color=FIELD, button_color=FIELD,
            button_hover_color=FIELD_HOVER, dropdown_fg_color=CARD, dropdown_hover_color=FIELD_HOVER,
            text_color=TEXT, dropdown_text_color=TEXT, dynamic_resizing=False)

    def _seg(self, parent, values, command):
        return ctk.CTkSegmentedButton(
            parent, values=list(values), command=command, font=self.f_body, height=34,
            corner_radius=10, fg_color=FIELD, selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
            unselected_color=FIELD, unselected_hover_color=FIELD_HOVER, text_color=TEXT)

    # ------------------------------------------------------------ layout

    def _build_header(self):
        hdr = ctk.CTkFrame(self.root, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 14))
        hdr.grid_columnconfigure(1, weight=1)

        logo = ctk.CTkImage(app_icon(128), size=(42, 42))
        ctk.CTkLabel(hdr, image=logo, text="").grid(row=0, column=0, rowspan=2, padx=(0, 14))
        ctk.CTkLabel(hdr, text="Music Visualizer", font=self.f_title, text_color=TEXT,
                     height=28).grid(row=0, column=1, sticky="sw")
        ctk.CTkLabel(hdr, text="Keyboards, mice and ARGB lighting that react to your music",
                     font=self.f_small, text_color=MUTED, height=18).grid(row=1, column=1,
                                                                          sticky="nw")
        self.mode_seg = self._seg(hdr, ("Music", "Paint"), self._on_mode)
        self.mode_seg.set("Paint" if self.settings["mode"] == "paint" else "Music")
        self.mode_seg.grid(row=0, column=2, rowspan=2, padx=14)
        self.pill = ctk.CTkLabel(hdr, text="", font=self.f_bold, corner_radius=15, height=30)
        self.pill.grid(row=0, column=3, rowspan=2, padx=14)
        self.start_btn = ctk.CTkButton(hdr, text="Start", font=self.f_bold, width=120, height=40,
                                       corner_radius=12, command=self.toggle)
        self.start_btn.grid(row=0, column=4, rowspan=2)

    def _build_main(self):
        main = ctk.CTkFrame(self.root, fg_color="transparent")
        main.grid(row=1, column=0, sticky="nsew", padx=24, pady=(0, 12))
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=1)

        stage = ctk.CTkFrame(main, fg_color=STAGE, corner_radius=16, border_width=1,
                             border_color=CARD_BORDER)
        stage.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        stage.grid_columnconfigure(0, weight=1)
        stage.grid_rowconfigure(0, weight=1)
        self.preview = DevicePreview(stage, scale=self.scale, height=int(200 * self.scale),
                                     on_paint=self._paint_at)
        self.preview.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)

        card = self._card(main, row=0, column=1, sticky="nsew")
        card.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=(18, 12), pady=(12, 4))
        head.grid_columnconfigure(0, weight=1)
        self._section(head, "Devices").grid(row=0, column=0, sticky="w")
        self.scan_btn = self._small_button(head, "Scan", self.scan)
        self.scan_btn.grid(row=0, column=1, sticky="e")
        self.dev_list = ctk.CTkScrollableFrame(
            card, fg_color="transparent", width=int(290), height=10,
            scrollbar_button_color=FIELD, scrollbar_button_hover_color=FIELD_HOVER)
        self.dev_list.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 10))
        self.dev_list.grid_columnconfigure(0, weight=1)

    def _build_controls(self):
        row = ctk.CTkFrame(self.root, fg_color="transparent")
        self.music_row = row
        row.grid_columnconfigure(0, weight=3, uniform="controls")
        row.grid_columnconfigure(1, weight=2, uniform="controls")

        # Effect + colors
        look = self._card(row, "Effect", row=0, column=0, sticky="nsew", padx=(0, 12))
        self.effect_btn = ctk.CTkSegmentedButton(
            look, values=list(effects.EFFECTS), command=self._on_effect, font=self.f_body,
            height=36, corner_radius=10, fg_color=FIELD, selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER, unselected_color=FIELD,
            unselected_hover_color=FIELD_HOVER, text_color=TEXT)
        self.effect_btn.set(self.settings["effect"])
        self.effect_btn.grid(row=1, column=0, sticky="ew", padx=18)
        self.effect_desc = ctk.CTkLabel(look, text="", font=self.f_small, text_color=MUTED,
                                        anchor="w", height=22)
        self.effect_desc.grid(row=2, column=0, sticky="ew", padx=20, pady=(6, 0))
        self._update_effect_desc()

        self._section(look, "Colors").grid(row=3, column=0, sticky="w", padx=18, pady=(12, 6))
        chips = ctk.CTkFrame(look, fg_color="transparent")
        chips.grid(row=4, column=0, sticky="w", padx=12, pady=(0, 14))
        self.chips = {}
        for i, name in enumerate(list(effects.PALETTES) + [CUSTOM]):
            chip = ctk.CTkButton(chips, text=name, compound="top", font=self.f_small,
                                 width=64, height=60, corner_radius=10, border_width=2,
                                 fg_color="transparent", hover_color=FIELD_HOVER,
                                 text_color=TEXT, border_spacing=4,
                                 command=lambda n=name: self._on_palette(n))
            chip.grid(row=0, column=i, padx=2)
            self.chips[name] = chip
        self._update_chip_images()

        # Levels
        levels = self._card(row, "Levels", row=0, column=1, sticky="nsew")
        grid = ctk.CTkFrame(levels, fg_color="transparent")
        grid.grid(row=1, column=0, sticky="ew", padx=18)
        grid.grid_columnconfigure(1, weight=1)
        for r, (key, label, lo, hi, unit) in enumerate([
                ("sensitivity", "Sensitivity", 0, 100, "%"),
                ("smoothing", "Smoothing", 0, 100, "%"),
                ("brightness", "Brightness", 5, 100, "%"),
                ("fps", "Frame rate", 10, 60, " fps")]):
            ctk.CTkLabel(grid, text=label, font=self.f_body, text_color=TEXT, anchor="w",
                         width=92).grid(row=r, column=0, sticky="w", pady=3)
            value = ctk.CTkLabel(grid, text=f"{self.settings[key]}{unit}", font=self.f_bold,
                                 text_color=TEXT, anchor="e", width=58)
            value.grid(row=r, column=2, sticky="e")
            slider = ctk.CTkSlider(
                grid, from_=lo, to=hi, number_of_steps=hi - lo, height=18, fg_color=TRACK,
                progress_color=ACCENT, button_color="#f4f4f8", button_hover_color="#ffffff",
                command=lambda v, k=key, lbl=value, u=unit: self._on_level(k, v, lbl, u))
            slider.set(self.settings[key])
            slider.grid(row=r, column=1, sticky="ew", padx=10)

        self.release_switch = ctk.CTkSwitch(
            levels, text="Normal lighting when the music stops", font=self.f_body,
            text_color=TEXT, progress_color=ACCENT, fg_color=TRACK, button_color="#f4f4f8",
            button_hover_color="#ffffff", command=self._on_release)
        if self.settings["release_on_silence"]:
            self.release_switch.select()
        self.release_switch.grid(row=2, column=0, sticky="w", padx=18, pady=(10, 14))

    def _build_paint_panel(self):
        """The Paint controls: brush, what the paint does, and the saved layouts."""
        row = ctk.CTkFrame(self.root, fg_color="transparent")
        self.paint_row = row
        row.grid_rowconfigure(0, weight=1)
        for c in range(3):
            row.grid_columnconfigure(c, weight=1, uniform="paint")
        p = self.settings["paint"]

        # Brush
        brush = self._card(row, "Brush", row=0, column=0, sticky="nsew", padx=(0, 12))
        self.tool_seg = self._seg(brush, paint.TOOLS, self._on_tool)
        self.tool_seg.set(p["tool"])
        self.tool_seg.grid(row=1, column=0, sticky="ew", padx=18)
        colors = ctk.CTkFrame(brush, fg_color="transparent")
        colors.grid(row=2, column=0, sticky="w", padx=18, pady=(12, 6))
        self.color_swatch = ctk.CTkButton(colors, text="", width=40, height=28, corner_radius=8,
                                          border_width=2, border_color=TEXT, command=self._choose_color)
        self.color_swatch.grid(row=0, column=0, padx=(0, 10))
        for i, rgb in enumerate(paint.PALETTE):
            dot = ctk.CTkButton(colors, text="", width=22, height=22, corner_radius=11, border_width=0,
                                fg_color=_hex(rgb), hover_color=_hex(rgb),
                                command=lambda c=rgb: self._set_color(list(c)))
            dot.grid(row=0, column=1 + i, padx=2)
        self._refresh_color_swatch()
        self.shape_seg = self._seg(brush, paint.SHAPES, self._on_shape)
        self.shape_seg.set(p["shape"])
        self.shape_seg.grid(row=3, column=0, sticky="w", padx=18, pady=(6, 4))
        sliders = ctk.CTkFrame(brush, fg_color="transparent")
        sliders.grid(row=4, column=0, sticky="ew", padx=18, pady=(4, 14))
        sliders.grid_columnconfigure(1, weight=1)
        self._pslider(sliders, 0, "Size", "size", lambda v: f"{v} key{'s' if v != 1 else ''}")
        self._pslider(sliders, 1, "Weight", "weight", lambda v: f"{v}%")

        # What the paint does
        look = self._card(row, "Animation", row=0, column=1, sticky="nsew", padx=(0, 12))
        pick = ctk.CTkFrame(look, fg_color="transparent")
        pick.grid(row=1, column=0, sticky="ew", padx=18)
        pick.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(pick, text="Animation", font=self.f_body, text_color=TEXT, width=84,
                     anchor="w").grid(row=0, column=0, sticky="w", pady=3)
        self.anim_menu = self._menu(pick, list(paint.ANIMATIONS), self._on_animation, width=170)
        self.anim_menu.set(p["animation"])
        self.anim_menu.grid(row=0, column=1, sticky="w", pady=3)
        ctk.CTkLabel(pick, text="React to", font=self.f_body, text_color=TEXT, width=84,
                     anchor="w").grid(row=1, column=0, sticky="w", pady=3)
        self.react_menu = self._menu(pick, list(paint.REACTIONS), self._on_react, width=170)
        self.react_menu.set(p["react"])
        self.react_menu.grid(row=1, column=1, sticky="w", pady=3)
        tune = ctk.CTkFrame(look, fg_color="transparent")
        tune.grid(row=2, column=0, sticky="ew", padx=18, pady=(8, 0))
        tune.grid_columnconfigure(1, weight=1)
        pct = lambda v: f"{v}%"
        secs = lambda v: f"{v / 10:.1f} s"
        self._pslider(tune, 0, "Intensity", "intensity", pct)
        self._pslider(tune, 1, "Reactivity", "reactivity", pct)
        self._pslider(tune, 2, "Heatmap", "heatmap", pct, on_change=lambda _: self._sync_keys())
        self._pslider(tune, 3, "Cycle", "cycle", secs)
        self._pslider(tune, 4, "Heat fade", "heat_fade", secs)
        self.paint_note_label = ctk.CTkLabel(look, text="", font=self.f_small, text_color=FAINT,
                                             anchor="w", justify="left", wraplength=340)
        self.paint_note_label.grid(row=3, column=0, sticky="ew", padx=18, pady=(10, 14))

        # The canvas and its saved layouts
        canvas = self._card(row, "Canvas", row=0, column=2, sticky="nsew")
        self.paint_target = ctk.CTkLabel(canvas, text="", font=self.f_small, text_color=MUTED,
                                         anchor="w", justify="left", wraplength=320)
        self.paint_target.grid(row=1, column=0, sticky="ew", padx=18)
        acts = ctk.CTkFrame(canvas, fg_color="transparent")
        acts.grid(row=2, column=0, sticky="w", padx=18, pady=(6, 0))
        for i, (text, fn) in enumerate((("Fill", self._fill), ("Clear", self._clear), ("Undo", self._undo))):
            self._small_button(acts, text, fn, width=70).grid(row=0, column=i, padx=(0, 6))
        ctk.CTkLabel(canvas, text="Layout", font=self.f_body, text_color=TEXT, anchor="w").grid(
            row=3, column=0, sticky="w", padx=18, pady=(12, 4))
        self.layout_menu = self._menu(canvas, ["-"], self._layout_picked, width=250)
        self.layout_menu.grid(row=4, column=0, sticky="w", padx=18)
        lay_btns = ctk.CTkFrame(canvas, fg_color="transparent")
        lay_btns.grid(row=5, column=0, sticky="w", padx=18, pady=(8, 0))
        for i, (text, fn) in enumerate((("Save", self._save_layout), ("New", self._new_layout),
                                        ("Rename", self._rename_layout), ("Delete", self._delete_layout))):
            self._small_button(lay_btns, text, fn, width=64).grid(row=0, column=i, padx=(0, 6))
        self.layout_status = ctk.CTkLabel(canvas, text="", font=self.f_small, text_color=MUTED,
                                          anchor="w", justify="left", wraplength=320)
        self.layout_status.grid(row=6, column=0, sticky="ew", padx=18, pady=(10, 14))
        self._fill_layout_menu()

    def _pslider(self, parent, row, label, key, fmt, on_change=None):
        """A labelled slider for one Paint setting."""
        ctk.CTkLabel(parent, text=label, font=self.f_body, text_color=TEXT, anchor="w", width=84).grid(
            row=row, column=0, sticky="w", pady=3)
        value = ctk.CTkLabel(parent, text=fmt(self.settings["paint"][key]), font=self.f_bold,
                             text_color=TEXT, anchor="e", width=70)
        value.grid(row=row, column=2, sticky="e")

        def changed(v):
            val = paint.clamp(key, v)
            self.settings["paint"][key] = val
            value.configure(text=fmt(val))
            if on_change:
                on_change(val)
            self._save_soon()

        lo, hi = paint.RANGES[key]
        slider = ctk.CTkSlider(parent, from_=lo, to=hi, number_of_steps=hi - lo, height=18, fg_color=TRACK,
                               progress_color=ACCENT, button_color="#f4f4f8", button_hover_color="#ffffff",
                               command=changed)
        slider.set(self.settings["paint"][key])
        slider.grid(row=row, column=1, sticky="ew", padx=10)

    def _build_equalizer(self):
        card = self._card(self.root, row=3, column=0, sticky="ew", padx=24, pady=(0, 12))
        self.eq_card = card
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(12, 0))
        head.grid_columnconfigure(1, weight=1)
        self._section(head, "Band sensitivity").grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(head, text="Drag to boost or cut a band  ·  double-click to zero  ·  "
                                "scroll to fine-tune", font=self.f_small, text_color=FAINT,
                     height=18).grid(row=0, column=1, sticky="w", padx=14)
        self._small_button(head, "Reset", self._reset_eq).grid(row=0, column=2, sticky="e")
        self.eq = Equalizer(card, self.settings["eq"], EQ_RANGE_DB, self.scale,
                            height=int(150 * self.scale))
        self.eq.grid(row=1, column=0, sticky="ew", padx=12, pady=(4, 12))

    def _build_footer(self):
        foot = ctk.CTkFrame(self.root, fg_color="transparent")
        foot.grid(row=4, column=0, sticky="ew", padx=24, pady=(0, 16))
        foot.grid_columnconfigure(3, weight=1)
        ctk.CTkLabel(foot, text="Audio", font=self.f_small, text_color=MUTED).grid(
            row=0, column=0, padx=(2, 8))
        self.dev_menu = ctk.CTkOptionMenu(
            foot, width=360, values=[""], font=self.f_body, dropdown_font=self.f_body,
            height=32, corner_radius=8, fg_color=FIELD, button_color=FIELD,
            button_hover_color=FIELD_HOVER, dropdown_fg_color=CARD,
            dropdown_hover_color=FIELD_HOVER, text_color=TEXT, dropdown_text_color=TEXT,
            dynamic_resizing=False, command=lambda _: self._restart())
        self.dev_menu.grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(foot, text="⟳", font=ctk.CTkFont(FONT, 16), width=32, height=32,
                      corner_radius=8, fg_color=FIELD, hover_color=FIELD_HOVER, text_color=TEXT,
                      command=self.refresh_audio).grid(row=0, column=2)
        self.detail = ctk.CTkLabel(foot, text="", font=self.f_small, text_color=FAINT,
                                   anchor="e", justify="right", wraplength=560)
        self.detail.grid(row=0, column=3, sticky="e", padx=(16, 2))

    def _apply_mode(self):
        """Shows the controls for the current mode: music or paint."""
        if self.settings["mode"] == "paint":
            self.music_row.grid_remove()
            self.eq_card.grid_remove()
            self.paint_row.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 12))
        else:
            self.paint_row.grid_remove()
            self.music_row.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 12))
            self.eq_card.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 12))

    # ------------------------------------------------------------ devices

    def scan(self):
        if self._scanning:
            return
        self._scanning = True
        self._was_running = self.engine is not None
        if self.engine:  # devices get re-created, so stop streaming meanwhile
            self._stop_engine()
        self.scan_btn.configure(text="Scanning", state="disabled")
        threading.Thread(target=lambda: setattr(self, "_scan_result", scan_all()),
                         daemon=True).start()

    def _apply_scan(self, outputs, problems):
        self._scanning = False
        self.scan_btn.configure(text="Scan", state="normal")
        self.outputs = outputs
        for child in self.dev_list.winfo_children():
            child.destroy()
        self.rows = {}
        if not outputs:
            ctk.CTkLabel(self.dev_list, text="No lighting devices found.\nSee the README for "
                                             "what each brand needs.", font=self.f_small,
                         text_color=MUTED, justify="left").grid(row=0, column=0, sticky="w",
                                                                padx=12, pady=8)
        for i, out in enumerate(outputs):
            self._device_row(out, i)
        wanted = [o for o in outputs if o.id == self.settings["preview_device"]]
        self._select(wanted[0] if wanted else (self._enabled()[:1] or outputs[:1] or [None])[0])
        self.detail.configure(text="   ".join(problems))
        if self._was_running:
            self.toggle()

    def _device_row(self, out, i):
        row = ctk.CTkFrame(self.dev_list, fg_color="transparent", corner_radius=10)
        row.grid(row=i, column=0, sticky="ew", pady=2)
        row.grid_columnconfigure(1, weight=1)
        dot = ctk.CTkLabel(row, text="●", width=14, font=self.f_small, text_color=FAINT)
        dot.grid(row=0, column=0, rowspan=2, padx=(10, 6))
        name = ctk.CTkLabel(row, text=out.name, font=self.f_bold, text_color=TEXT, anchor="w",
                            justify="left", wraplength=190, height=18)
        name.grid(row=0, column=1, sticky="ew", pady=(8, 0))
        detail = ctk.CTkLabel(row, text=out.detail, font=self.f_small, text_color=MUTED,
                              anchor="w", justify="left", wraplength=190, height=16)
        detail.grid(row=1, column=1, sticky="ew", pady=(0, 8))
        switch = ctk.CTkSwitch(row, text="", width=44, progress_color=ACCENT, fg_color=TRACK,
                               button_color="#f4f4f8", button_hover_color="#ffffff")
        switch.configure(command=lambda: self._on_toggle_device(out, switch.get()))
        if out.id not in self.settings["disabled_devices"]:
            switch.select()
        switch.grid(row=0, column=2, rowspan=2, padx=(4, 6))
        for w in (row, dot, name, detail):
            w.bind("<Button-1>", lambda e, o=out: self._select(o))
        self.rows[out.id] = {"frame": row, "dot": dot, "detail": detail, "out": out,
                             "shown": None}

    def _enabled(self):
        return [o for o in self.outputs if o.id not in self.settings["disabled_devices"]]

    def _select(self, out):
        self.selected = out
        if out:
            self.settings["preview_device"] = out.id
        for dev_id, r in self.rows.items():
            r["frame"].configure(fg_color=FIELD if out and dev_id == out.id else "transparent")
        self.preview.set_layout(out.layout if out else None, out.name if out else "")
        if self.engine:
            self.engine.preview = out
        self._refresh_paint_target()
        self._refresh_paint_note()

    def _on_toggle_device(self, out, on):
        disabled = self.settings["disabled_devices"]
        if on and out.id in disabled:
            disabled.remove(out.id)
        elif not on and out.id not in disabled:
            disabled.append(out.id)
        if self.engine:  # only this device starts/stops; the others keep streaming
            if on:
                self.engine.add_output(out)
            else:
                self.engine.remove_output(out.id)
                r = self.rows.get(out.id)
                if r:
                    self._set_row(r, FAINT, out.detail, MUTED)

    # ------------------------------------------------------------ mode

    def _on_mode(self, label):
        self.settings["mode"] = "paint" if label == "Paint" else "music"
        self._apply_mode()
        self._sync_keys()
        self._refresh_paint_note()
        self._save_soon()

    # ------------------------------------------------------------ music events

    def _on_effect(self, name):
        self.settings["effect"] = name
        self._update_effect_desc()

    def _update_effect_desc(self):
        self.effect_desc.configure(text=effects.EFFECTS[self.settings["effect"]].__doc__.strip())

    def _on_palette(self, name):
        if name == CUSTOM:
            r, g, b = self.settings["custom_color"]
            rgb, _ = colorchooser.askcolor(color=f"#{r:02x}{g:02x}{b:02x}", parent=self.root,
                                           title="Pick a color")
            if rgb:
                self.settings["custom_color"] = [int(c) for c in rgb]
                self._update_chip_images()
        self.settings["palette"] = name
        self._on_palette_changed()

    def _on_palette_changed(self):
        for name, chip in self.chips.items():
            chip.configure(border_color=ACCENT if name == self.settings["palette"] else CARD)
        palette, _ = palette_for(self.settings)
        self.band_colors = palette(np.linspace(0, 1, audio.NUM_BANDS))

    def _update_chip_images(self):
        for name, chip in self.chips.items():
            if name == CUSTOM:
                colors = np.array([self.settings["custom_color"]] * 2) / 255
            else:
                colors = effects.PALETTES[name](np.linspace(0, 1, 32))
            chip.configure(image=ctk.CTkImage(gradient_image(colors, (128, 48), 12),
                                              size=(50, 20)))

    def _on_level(self, key, value, label, unit):
        self.settings[key] = int(round(value))
        label.configure(text=f"{self.settings[key]}{unit}")

    def _on_release(self):
        self.settings["release_on_silence"] = bool(self.release_switch.get())

    def _reset_eq(self):
        self.settings["eq"][:] = [0] * audio.NUM_BANDS
        self.eq.redraw()

    def refresh_audio(self):
        try:
            self.devices = audio.list_devices()
        except OSError:
            self.devices = []
        names = [d[0] for d in self.devices] or ["No audio devices found"]
        self.dev_menu.configure(values=names)
        match = [d[0] for d in self.devices if d[0] == self.settings["device"]]
        self.dev_menu.set(match[0] if match else names[0])

    # ------------------------------------------------------------ paint events

    def _on_tool(self, value):
        self.settings["paint"]["tool"] = value
        self._save_soon()

    def _on_shape(self, value):
        self.settings["paint"]["shape"] = value
        self._save_soon()

    def _on_animation(self, value):
        self.settings["paint"]["animation"] = value
        self._save_soon()

    def _on_react(self, value):
        self.settings["paint"]["react"] = value
        self._sync_keys()
        self._refresh_paint_note()
        self._save_soon()

    def _choose_color(self):
        r, g, b = self.settings["paint"]["color"]
        got = colorchooser.askcolor(color=f"#{r:02x}{g:02x}{b:02x}", parent=self.root, title="Paint color")[0]
        if got:
            self._set_color([int(c) for c in got])

    def _set_color(self, rgb):
        self.settings["paint"]["color"] = list(rgb)
        self._refresh_color_swatch()
        self._save_soon()

    def _refresh_color_swatch(self):
        hex_ = _hex(self.settings["paint"]["color"])
        self.color_swatch.configure(fg_color=hex_, hover_color=hex_)

    def _paint_at(self, x, y, start):
        """A click or drag on the preview: paints, erases or picks the LEDs under the brush."""
        if self.settings["mode"] != "paint" or self.selected is None:
            return
        out, p = self.selected, self.settings["paint"]
        self.paint.ensure(out.id, out.layout.n)
        hits = self.preview.hits(x, y, p["size"], p["weight"] / 100, p["shape"] == "Square")
        if not hits:
            return
        if p["tool"] == "Pick":
            if start:
                rgb = self.paint.pixel(out.id, hits[0][0])
                if any(rgb):
                    self._set_color(rgb)
            return
        if start:
            self.paint.snapshot(out.id, [out.id])
        color = (0, 0, 0) if p["tool"] == "Erase" else p["color"]
        self.paint.apply(out.id, hits, color)
        self._painted()

    def _painted(self):
        self._dirty = True
        self._refresh_layout_status()
        self._save_soon()

    def _fill(self):
        if self.selected is None:
            return
        out, n = self.selected, self.selected.layout.n
        self.paint.ensure(out.id, n)
        self.paint.snapshot(out.id, [out.id])
        self.paint.fill(out.id, self.settings["paint"]["color"], n)
        self._painted()

    def _clear(self):
        if self.selected is None:
            return
        out, n = self.selected, self.selected.layout.n
        self.paint.ensure(out.id, n)
        self.paint.snapshot(out.id, [out.id])
        self.paint.clear(out.id, n)
        self._painted()

    def _undo(self):
        if self.selected is not None and self.paint.undo_last(self.selected.id):
            self._painted()

    # ------------------------------------------------------------ paint layouts

    def _fill_layout_menu(self):
        names = self.paint.names()
        newest = self.settings["paint_newest"]
        self._layout_labels = {(n + " (New)" if n == newest else n): n for n in names}
        shown = next((label for label, n in self._layout_labels.items() if n == self.paint.name),
                     "No layout yet" if not names else "Choose a layout")
        self.layout_menu.configure(values=list(self._layout_labels) or ["-"])
        self.layout_menu.set(shown)
        self._refresh_layout_status()

    def _refresh_layout_status(self):
        text = self._layout_msg or ("Changes not saved yet" if self._dirty else "Saved")
        self.layout_status.configure(text=text, text_color=("#f87171" if self._layout_msg else MUTED))

    def _status(self, msg=""):
        self._layout_msg = msg
        self._refresh_layout_status()

    def _ensure_layout(self):
        """Gives the current painting a saved layout the first time one is needed."""
        if self.paint.name and self.paint.name in self.paint.names():
            return True
        name = self.paint.new_name()
        try:
            self.paint.save(name)
        except OSError as e:
            self._status(f"Couldn't save the layout ({e}).")
            return False
        self.settings["paint_layout"] = self.settings["paint_newest"] = name
        self._dirty = False
        return True

    def _save_layout(self):
        if not self._ensure_layout():
            return
        try:
            self.paint.save(self.paint.name)
        except OSError as e:
            self._status(f"Couldn't save the layout ({e}).")
            return
        self.settings["paint_layout"] = self.paint.name
        self._dirty = False
        self._status("")
        self._fill_layout_menu()
        self._save_soon()

    def _layout_picked(self, label):
        name = self._layout_labels.get(label, label)
        if name == self.paint.name and not self._dirty:
            return
        if self._dirty and self.paint.name:
            try:
                self.paint.save(self.paint.name)     # keep what was painted before switching
            except OSError as e:
                self._status(f"Couldn't save the layout you were on ({e}). Nothing was switched.")
                self._fill_layout_menu()
                return
        if self.paint.load(name):
            self._dirty = False
            self.settings["paint_layout"] = name
            self._status("")
            self._save_soon()
        else:
            self._status(f"Couldn't open \"{name}\": the file is missing or damaged.")
        self._fill_layout_menu()

    def _new_layout(self):
        if self._dirty and self.paint.name:
            try:
                self.paint.save(self.paint.name)
            except OSError as e:
                self._status(f"Couldn't save the layout you were on ({e}).")
                return
        self.paint.reset_all()
        name = self.paint.new_name()
        try:
            self.paint.save(name)
        except OSError as e:
            self._status(f"Couldn't create the layout ({e}).")
            return
        self.settings["paint_layout"] = self.settings["paint_newest"] = name
        self._dirty = False
        self._status("")
        self._fill_layout_menu()
        self._save_soon()

    def _rename_layout(self):
        if not self._ensure_layout():
            return
        old = self.paint.name
        new = ctk.CTkInputDialog(text="New name for this layout:", title="Rename layout").get_input()
        if not new:
            return
        if self._dirty:
            try:
                self.paint.save(old)                 # rename what's on disk, with today's painting in it
            except OSError as e:
                self._status(f"Couldn't save before renaming ({e}).")
                return
            self._dirty = False
        got = self.paint.rename(old, new)
        if not got:
            self._status(f"Can't use \"{new.strip()}\": the name is empty, already used, or has no "
                         f"letters or numbers.")
        else:
            if self.settings["paint_newest"] == old:
                self.settings["paint_newest"] = got
            self.settings["paint_layout"] = got
            self._status("")
            self._save_soon()
        self._fill_layout_menu()

    def _delete_layout(self):
        gone = self.paint.name
        if not gone:
            return
        if not messagebox.askyesno("Delete layout", f"Delete \"{gone}\"?\n\nThe painting in it will be gone for good.",
                                   parent=self.root):
            return
        self.paint.delete(gone)
        if self.settings["paint_newest"] == gone:
            self.settings["paint_newest"] = ""
        rest = self.paint.names()
        if not (rest and self.paint.load(rest[0])):
            self.paint.reset_all()
        self.settings["paint_layout"] = self.paint.name
        self._dirty = False
        self._status("")
        self._fill_layout_menu()
        self._save_soon()

    def _load_paint_autosave(self):
        """What was on screen last time: the autosave, shown under the layout it was saved in."""
        self.paint.load("_autosave")
        name = self.settings["paint_layout"]
        self.paint.set_name(name if name in self.paint.names() else "")
        self._dirty = self.paint.differs_from_saved()

    def _save_soon(self):
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(SAVE_DELAY_MS, self._save_now)

    def _save_now(self):
        self._save_job = None
        self._write_settings()
        if self.paint.pixels:
            try:
                self.paint.autosave()
            except OSError as e:
                self._status(f"Couldn't autosave ({e}).")

    def _write_settings(self):
        try:
            _write_json(SETTINGS_FILE, self.settings)
        except OSError:
            pass

    # ------------------------------------------------------------ keystrokes

    def _keys_wanted(self):
        p = self.settings["paint"]
        return (self.engine is not None and self.settings["mode"] == "paint"
                and (p["react"] == "Keystrokes" or p["heatmap"] > 0))

    def _sync_keys(self):
        """Listens to the keyboard only while a Paint setting needs it (and stops again after)."""
        try:
            if self._keys_wanted():
                if not self.keymon.running:
                    self.keymon.start()
            elif self.keymon.running:
                self.keymon.stop()
        except OSError:
            pass      # keymon.error says why; the paint note shows it
        self._refresh_paint_note()

    def _paint_note(self):
        p = self.settings["paint"]
        if self.settings["mode"] != "paint" or (p["react"] != "Keystrokes" and p["heatmap"] == 0):
            return ""
        if not IS_WINDOWS:
            return "Keystroke reactions and the heatmap are Windows-only for now."
        if self.keymon.error:
            return self.keymon.error
        if self.selected is not None and not self.selected.keymap:
            return "Keys only react on keyboards whose keys are mapped (the Model 100 for now)."
        return ""

    def _refresh_paint_note(self):
        note = self._paint_note()
        if note != self._paint_note_shown:
            self._paint_note_shown = note
            self.paint_note_label.configure(text=note)

    def _refresh_paint_target(self):
        if self.selected is None:
            text = "Pick a device in the list to paint on it."
        else:
            text = f"Painting on: {self.selected.name}. Click or drag on the preview."
        self.paint_target.configure(text=text)

    # ------------------------------------------------------------ running

    def _restart(self):
        if self.engine:  # pick up changed devices
            self.toggle()
            self.toggle()

    def _stop_engine(self):
        self.engine.stop()
        self.engine.join(timeout=3)
        self.engine = None
        self._sync_keys()
        self._update_run_state()

    def toggle(self):
        if self.engine:
            self._stop_engine()
            return
        device = dict(self.devices).get(self.dev_menu.get())
        if device is None:
            self.detail.configure(text="Pick an audio device first.")
            return
        self.settings["device"] = self.dev_menu.get()
        self.engine = Engine(self._enabled(), device, self.settings, self.paint, self.keymem)
        self.engine.preview = self.selected
        self.engine.start()
        self._sync_keys()
        self._update_run_state()

    def _update_run_state(self):
        if self.engine:
            self.start_btn.configure(text="Stop", fg_color=FIELD, hover_color=FIELD_HOVER,
                                     text_color=TEXT)
        else:
            self.start_btn.configure(text="Start", fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                     text_color="#ffffff")
            self._set_pill("stopped")
            self.eq.set_levels(np.zeros(audio.NUM_BANDS), self.band_colors)
            for r in self.rows.values():
                self._set_row(r, FAINT, r["out"].detail, MUTED)

    def _set_pill(self, state, extra=""):
        text, fg, bg = STATES[state]
        text += extra
        if (state, text) != self._pill_state:
            self._pill_state = (state, text)
            self.pill.configure(text=f"   ●  {text}   ", text_color=fg, fg_color=bg)

    @staticmethod
    def _set_row(r, dot, detail, detail_color):
        if r["shown"] != (dot, detail):
            r["shown"] = (dot, detail)
            r["dot"].configure(text_color=dot)
            r["detail"].configure(text=detail, text_color=detail_color)

    def _update_status(self, eng):
        ws = eng.workers
        for w in ws:
            r = self.rows.get(w.output.id)
            if r:
                error = w.state == "error"
                self._set_row(r, DOTS[w.state], w.status if error else r["out"].detail,
                              "#f87171" if error else MUTED)
        live = sum(w.state == "live" for w in ws)
        if not ws:
            self._set_pill("preview", f"  ·  {eng.fps} fps")
        elif live:
            devices = f"{live} device{'s' if live > 1 else ''}"
            self._set_pill("streaming", f"  ·  {devices}  ·  {eng.fps} fps")
        elif all(w.state == "resting" for w in ws):
            self._set_pill("resting")
        elif any(w.state == "connecting" for w in ws):
            self._set_pill("starting")
        else:
            self._set_pill("error")

    def _preview_frame(self, out):
        """What the preview shows: the live frame, or, while not lighting, the painting itself."""
        eng = self.engine
        if eng is not None:
            frame = eng.frames.get(out.id)
            if frame is not None:
                return frame
        if self.settings["mode"] == "paint":
            return self.paint.frame_base(out.id, out.layout.n) / 255.0
        return np.zeros((out.layout.n, 3))

    def tick(self):
        if self._scan_result is not None:
            result, self._scan_result = self._scan_result, None
            self._apply_scan(*result)
        eng = self.engine
        if eng and not eng.is_alive():  # the engine gave up (audio error)
            self._stop_engine()
            self.detail.configure(text=eng.error or "")
        elif eng:
            self.eq.set_levels(eng.analyzer.bands, self.band_colors)
            self._update_status(eng)
        if self.selected is not None:
            self.preview.show(self._preview_frame(self.selected))
        self.root.after(33, self.tick)

    def on_close(self):
        if self.engine:
            self._stop_engine()
        self.keymon.stop()
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
            self._save_job = None
        self._write_settings()
        if self.paint.pixels:
            try:
                self.paint.autosave()
            except OSError:
                pass
        self.root.destroy()


def _hex(rgb):
    r, g, b = (int(c) for c in rgb)
    return f"#{r:02x}{g:02x}{b:02x}"


def main():
    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
