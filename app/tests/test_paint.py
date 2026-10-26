"""Tests for Paint: the painted colors, layouts, animations, key memory and the key hook.

Each test that guards a bug found in Illumunda's paint scene says which one in its name.
"""

import colorsys
import ctypes
import itertools
import random
import threading
import time

import numpy as np
import pytest

import keyhook
import keymap
import layout
import paint
from devices.base import Output


class FakeOutput(Output):
    """A device for the engine to render for; no hardware."""

    def __init__(self, dev_id="fake:model100"):
        self.id = dev_id
        self.name = "Fake Model 100"
        self.layout = layout.model100()
        self.keymap = keymap.model100_keymap()


@pytest.fixture
def state(tmp_path):
    return paint.PaintState(tmp_path / "layouts")


def settings(**changes):
    p = dict(paint.DEFAULT_SETTINGS)
    p.update(changes)
    return p


def painted(state, dev_id, n, rgb, led=0):
    state.ensure(dev_id, n)
    state.apply(dev_id, [(led, 1.0)], rgb)


# ---------------------------------------------------------------- keys

def test_enter_key_is_mapped_so_it_can_react():
    # Illumunda left Enter out of its key table, so Enter never lit up.
    assert keymap.VK[0x0D] == "enter"
    assert keymap.VK_OF["enter"] == 0x0D
    assert keymap.model100_keymap()["enter"] == [1 * 16 + 9]


def test_model100_keymap_matches_the_firmware_layout():
    km = keymap.model100_keymap()
    assert km["q"] == [17] and km["a"] == [33] and km["p"] == [30]     # row * 16 + column
    assert km["space"] == [24] and km["`"] == [16]
    assert all(0 <= i < 64 for leds in km.values() for i in leds)
    # the Model 100 has no [ ] \ keys on its default layer
    assert {k for k in keymap.VK.values() if k not in km} == {"[", "]", "\\"}


def test_key_memory_counts_presses_fades_and_flashes():
    mem = paint.KeyMemory()
    mem.fade_s = 1.0
    mem.press("a", 0.0)
    km = {"a": [5]}
    heat, pressed = mem.maps(km, 16, 0.0)
    assert heat[5] == pytest.approx(0.25) and pressed[5] == pytest.approx(1.0)
    heat, pressed = mem.maps(km, 16, 1.0)
    assert heat[5] == pytest.approx(0.25 * np.exp(-1.0))
    assert pressed[5] == 0.0 and heat[4] == 0.0
    for _ in range(10):
        mem.press("a", 2.0)
    assert mem.maps(km, 16, 2.0)[0][5] == pytest.approx(1.0)        # capped at full heat


# ---------------------------------------------------------------- painted colors

def test_static_plays_the_painted_color_exactly():
    base = np.zeros((4, 3), dtype=np.uint8)
    base[1] = (255, 0, 0)
    out = paint.animate(base, layout.model100(), 0.0, settings(), 0.0)
    assert out.tolist()[1] == [1.0, 0.0, 0.0] and not out[0].any()


def test_sparkle_never_lights_unpainted_leds():
    # Illumunda lit empty keys to 70% white when they sparkled.
    base = np.zeros((64, 3), dtype=np.uint8)
    p = settings(animation="Sparkle")
    for t in np.linspace(0, 5, 60):
        assert not paint.animate(base, layout.model100(), t, p).any()


def test_sparkle_does_brighten_painted_leds():
    base = np.full((64, 3), 100, dtype=np.uint8)
    p = settings(animation="Sparkle")
    lights = [paint.animate(base, layout.model100(), t, p) for t in np.linspace(0, 3, 30)]
    assert any(np.any(f > 100 / 255 + 1e-6) for f in lights)


def test_hue_rotation_matches_colorsys_and_keeps_black():
    rng = random.Random(7)
    rgb = np.array([[rng.random() for _ in range(3)] for _ in range(200)] + [[0, 0, 0]])
    for shift in (0.0, 0.31, 0.77):
        got = paint.hsv_rotate(rgb, shift)
        want = np.array([colorsys.hsv_to_rgb((colorsys.rgb_to_hsv(*c)[0] + shift) % 1.0,
                                             *colorsys.rgb_to_hsv(*c)[1:]) for c in rgb])
        assert np.allclose(got, want, atol=1e-9)
    assert not paint.hsv_rotate(np.zeros((3, 3)), 0.5).any()


def test_every_animation_and_reaction_gives_valid_frames():
    rng = np.random.default_rng(1)
    base = rng.integers(0, 256, (64, 3)).astype(np.uint8)
    heat = rng.random(64)
    pressed = rng.random(64)
    for anim, react, heatmap in itertools.product(paint.ANIMATIONS, paint.REACTIONS, (0, 60)):
        out = paint.animate(base, layout.model100(), 1.7, settings(animation=anim, react=react, heatmap=heatmap),
                            0.6, heat, pressed)
        assert out.shape == (64, 3) and np.isfinite(out).all() and out.min() >= 0 and out.max() <= 1


def test_heatmap_warms_unpainted_keys():
    p = settings(heatmap=100)
    out = paint.animate(np.zeros((2, 3), np.uint8), layout.model100(), 0.0, p, 0.0, np.array([1.0, 0.0]))
    assert out[0].tolist() == pytest.approx(paint.HOT.tolist()) and not out[1].any()


def test_brush_hits_puts_the_nearest_led_first():
    centers = np.array([[x * 10.0, 0.0] for x in range(5)])
    hits = paint.brush_hits(centers, 10.0, 21.0, 0.0, 1, 0.5, False)
    assert hits[0] == (2, 1.0)
    assert paint.brush_hits(centers, 10.0, 500.0, 0.0, 1, 0.5, False) == []
    assert paint.spacing(centers) == pytest.approx(10.0)


# ---------------------------------------------------------------- layouts

def test_layouts_round_trip_and_names_are_safe(state):
    painted(state, "dev", 4, (10, 20, 30))
    assert state.save("../../evil") == "evil"
    assert (state.folder / "evil.json").exists()
    state.reset_all()
    assert state.load("evil") and state.pixel("dev", 0) == [10, 20, 30]
    assert state.undo_last("dev") is False                      # undo history isn't saved


def test_rename_keeps_the_open_painting_and_its_undo(state):
    painted(state, "dev", 4, (255, 0, 0))
    state.save("Alpha")
    state.snapshot("dev", ["dev"])
    state.apply("dev", [(1, 1.0)], (0, 255, 0))
    assert state.rename("Alpha", "Desk") == "Desk"
    assert state.name == "Desk" and state.pixel("dev", 1) == [0, 255, 0]
    assert state.undo_last("dev") and state.pixel("dev", 1) == [0, 0, 0]   # undo survived the rename
    assert state.read("Desk") is not None and state.read("Alpha") is None


def test_rename_of_a_missing_layout_fails_instead_of_pretending(state):
    # Illumunda reported success and set the name, even though nothing had been renamed.
    painted(state, "dev", 4, (1, 2, 3))
    state.set_name("")
    assert state.rename("Nope", "Desk") == ""
    assert state.names() == []


def test_rename_refuses_taken_and_empty_names_any_case(state):
    state.save("Alpha")
    state.save("Bravo")
    assert state.rename("Bravo", "alpha") == ""
    assert state.rename("Bravo", "   ") == ""
    assert state.names() == ["Alpha", "Bravo"]


def test_rename_by_case_only_keeps_the_file(state):
    state.save("alpha")
    assert state.rename("alpha", "Alpha") == "Alpha"
    assert state.read("Alpha") is not None                       # on Windows the file is the same one


def test_a_crash_while_saving_keeps_the_old_layout(state, monkeypatch):
    painted(state, "dev", 4, (9, 9, 9))
    state.save("Keep")

    def crash(src, dst):
        raise OSError("power cut")
    painted(state, "dev", 4, (200, 0, 0))
    monkeypatch.setattr(paint.os, "replace", crash)
    with pytest.raises(OSError):
        state.save("Keep")
    monkeypatch.undo()
    assert state.read("Keep")["dev"][0].tolist() == [9, 9, 9]   # the old file is intact


def test_a_damaged_layout_is_refused_and_nothing_changes(state):
    painted(state, "dev", 4, (5, 5, 5))
    state.folder.mkdir(parents=True, exist_ok=True)
    (state.folder / "Broken.json").write_text("{not json", encoding="utf-8")
    assert state.load("Broken") is False
    assert state.pixel("dev", 0) == [5, 5, 5]


def test_differs_from_saved_tracks_unsaved_paint(state):
    painted(state, "dev", 4, (1, 0, 0))
    assert state.differs_from_saved()                            # painted, not in any layout yet
    state.save("Saved")
    assert not state.differs_from_saved()
    state.apply("dev", [(2, 1.0)], (0, 0, 9))
    assert state.differs_from_saved()
    state.load("Saved")
    assert not state.differs_from_saved()


def test_saving_while_the_renderer_adds_devices_does_not_crash(state):
    stop = threading.Event()
    errors = []

    def renderer():
        for i in range(300):                       # devices appearing while the window saves
            if stop.is_set():
                return
            try:
                state.frame_base(f"dev{i}", 8)     # the renderer inserts into pixels
            except Exception as e:                 # noqa: BLE001
                errors.append(e)
            time.sleep(0.001)

    thread = threading.Thread(target=renderer)
    thread.start()
    try:
        for _ in range(200):
            state.save("Busy")
    finally:
        stop.set()
        thread.join()
    assert errors == []


def test_clean_settings_replaces_junk_and_clamps_numbers():
    s = paint.clean_settings({"animation": "Disco", "react": "Audio", "size": 99, "weight": -5,
                              "color": [300, -1, "x"], "intensity": True, "cycle": 1, "bogus": 3})
    assert s["animation"] == "Static" and s["react"] == "Audio"
    assert s["size"] == 6 and s["weight"] == 0
    assert s["color"] == paint.DEFAULT_SETTINGS["color"]
    assert s["intensity"] == paint.DEFAULT_SETTINGS["intensity"] and s["cycle"] == 2
    assert "bogus" not in s and "fps" not in s
    assert paint.clean_settings(None) == paint.DEFAULT_SETTINGS


# ---------------------------------------------------------------- engine

def test_paint_frames_play_the_painting_without_gamma_or_brightness(state):
    import engine
    out = FakeOutput()
    painted(state, out.id, out.layout.n, (255, 128, 0), led=17)
    eng = engine.Engine([], 0, {"paint": settings(), "brightness": 10}, state)
    frame = eng._paint_colors(out, eng.analyzer, 0.0)
    assert np.allclose(frame[17], [1.0, 128 / 255, 0.0])       # exactly what was picked
    assert not frame[0].any()


def test_heatmap_reaches_the_frame_through_the_engine(state):
    import engine
    out = FakeOutput()
    mem = paint.KeyMemory()
    for _ in range(4):                       # four quick presses: the key is fully hot
        mem.press("a", time.monotonic())
    eng = engine.Engine([], 0, {"paint": settings(heatmap=100), "brightness": 100}, state, mem)
    frame = eng._paint_colors(out, eng.analyzer, 0.0)
    assert frame[33].tolist() == pytest.approx(paint.HOT.tolist(), abs=0.05)   # the "a" key, warm


# ---------------------------------------------------------------- key hook

class _Hook(keyhook.KeyHook):
    """A hook that's never installed: only its callback logic is run, by hand."""

    def __init__(self, blocked=lambda: False):
        self.calls = []
        super().__init__(self.calls.append, blocked=blocked)


def _event(hook, wparam, vk):
    kb = keyhook.KBDLLHOOKSTRUCT(vkCode=vk)
    hook._callback(0, wparam, ctypes.addressof(kb))


DOWN, UP = keyhook.WM_KEYDOWN, keyhook.WM_KEYUP


def test_holding_a_key_counts_once_and_a_new_press_counts_again():
    if not keyhook.IS_WINDOWS:
        pytest.skip("the key hook is Windows-only")
    hook = _Hook()
    _event(hook, DOWN, 0x41)            # A
    _event(hook, DOWN, 0x41)            # auto-repeat while held
    _event(hook, DOWN, 0x41)
    assert hook.calls == ["a"]
    _event(hook, UP, 0x41)
    _event(hook, DOWN, 0x41)
    assert hook.calls == ["a", "a"]


def test_shortcuts_and_password_fields_are_not_counted():
    if not keyhook.IS_WINDOWS:
        pytest.skip("the key hook is Windows-only")
    hook = _Hook()
    _event(hook, DOWN, 0x11)            # Ctrl
    _event(hook, DOWN, 0x43)            # Ctrl+C
    _event(hook, UP, 0x11)
    assert hook.calls == []
    blocked = _Hook(blocked=lambda: True)
    _event(blocked, DOWN, 0x42)         # typing into a password field
    assert blocked.calls == []


def test_password_check_runs_on_its_own_thread():
    if not keyhook.IS_WINDOWS:
        pytest.skip("the password check is Windows-only")
    watch = keyhook.PasswordWatch()
    watch.start()
    time.sleep(0.5)
    assert watch.focused is False and watch.uia_available
    watch.stop()


def test_key_monitor_refuses_off_windows(monkeypatch):
    monkeypatch.setattr(keyhook, "IS_WINDOWS", False)
    mon = keyhook.KeyMonitor(paint.KeyMemory())
    with pytest.raises(OSError):
        mon.start()
    assert not mon.running and "Windows" in mon.error
