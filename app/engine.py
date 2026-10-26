"""Render loop: captures audio, renders the music effects or the painted lighting for every device,
and streams the frames, one sender thread per device."""

import threading
import time

import numpy as np

import audio
import effects
import paint
from devices import OutputError

CUSTOM = "Custom"
# Longer than the gap between songs: handing lighting back and taking it over
# again is slow on some devices (G HUB needs several seconds to hand it back).
SILENCE_RELEASE_S = 8.0
RETRY_S = 3.0


def palette_for(settings):
    """Returns (palette function, whether it colors by key position)."""
    if settings["palette"] == CUSTOM:
        return effects.solid_palette(settings["custom_color"]), False
    return effects.PALETTES[settings["palette"]], settings["palette"] == "Rainbow"


class OutputWorker(threading.Thread):
    """Streams frames to one device. Only the newest frame is kept, so a slow
    device drops frames instead of lagging behind or holding others up.

    `state` is one of: connecting, live, resting (lighting handed back during
    silence), error. `status` is a human-readable detail."""

    def __init__(self, output):
        super().__init__(daemon=True)
        self.output = output
        self.state = "connecting"
        self.status = "Connecting..."
        self.fps = 0
        self._cond = threading.Condition()
        self._frame = None
        self._release = False
        self._quit = False

    def submit(self, rgb):
        with self._cond:
            self._frame, self._release = rgb, False
            self._cond.notify()

    def request_release(self):
        with self._cond:
            self._frame, self._release = None, True
            self._cond.notify()

    def stop(self):
        with self._cond:
            self._quit = True
            self._cond.notify()

    def run(self):
        out = self.output
        opened, released, retry_at, sent = False, False, 0.0, []
        while True:
            with self._cond:
                while not (self._quit or self._frame is not None or self._release):
                    self._cond.wait()
                if self._quit:
                    break
                frame, release = self._frame, self._release
                self._frame, self._release = None, False

            now = time.monotonic()
            try:
                if not opened:
                    if now < retry_at:
                        continue
                    out.open()
                    opened = True
                if release:
                    if not released:
                        out.release()
                        released = True
                        self.state, self.status = "resting", "Showing its own lighting until music plays"
                else:
                    out.send(frame)
                    released = False
                    sent = [t for t in sent if now - t < 1] + [now]
                    self.fps = len(sent)
                    if self.state != "live":
                        self.state, self.status = "live", "Live"
            except OutputError as e:
                self.state, self.status, self.fps = "error", str(e), 0
                out.close()
                opened, released, retry_at = False, False, now + RETRY_S
        out.close()


class Engine(threading.Thread):
    """`outputs` are the enabled devices. `preview` (settable any time) is an
    Output whose frame is rendered for the on-screen preview even when it
    isn't enabled. `paint` is the window's PaintState; `keys` the KeyMemory that
    key presses are recorded in. `error` is set if audio capture fails.

    settings["mode"] is "music" or "paint": music renders the effect, paint plays
    the painted colors (see paint.py)."""

    def __init__(self, outputs, device_index, settings, paint_state, keys=None):
        super().__init__(daemon=True)
        self.workers = [OutputWorker(o) for o in outputs]
        self.device_index = device_index
        self.settings = settings
        self.paint = paint_state
        self.keys = keys if keys is not None else paint.KeyMemory()
        self.capture = audio.AudioCapture()
        self.analyzer = audio.Analyzer()
        self.preview = None
        self.frames = {}  # output id -> float RGB frame, for the preview
        self.error = None
        self.fps = 0
        self._effect_name = None
        self._effect = None
        self._quit = threading.Event()
        self._lock = threading.Lock()  # guards starting/stopping workers
        self._running = False

    def stop(self):
        self._quit.set()

    def add_output(self, output):
        """Starts streaming to one more device without disturbing the others."""
        with self._lock:
            if any(w.output.id == output.id for w in self.workers):
                return
            worker = OutputWorker(output)
            if self._running:
                worker.start()
            self.workers = self.workers + [worker]  # replaced, never mutated: run() iterates it

    def remove_output(self, output_id):
        """Stops streaming to one device (it gets its own lighting back)."""
        with self._lock:
            gone = [w for w in self.workers if w.output.id == output_id]
            self.workers = [w for w in self.workers if w.output.id != output_id]
            for w in gone:
                if w.is_alive():
                    w.stop()

    def _render(self, lay, a, palette, spatial):
        colors = np.clip(effects.render(self._effect, lay, a, palette, spatial), 0, 1)
        return colors * (self.settings["brightness"] / 100)

    def _paint_colors(self, out, a, t):
        """The painted lighting for one device at time t (seconds since the engine started)."""
        p = self.settings["paint"]
        n = out.layout.n
        base = self.paint.frame_base(out.id, n)
        heat = pressed = None
        if out.keymap and (p["heatmap"] > 0 or p["react"] == "Keystrokes"):
            heat, pressed = self.keys.maps(out.keymap, n, time.monotonic())
        level = 0.0 if a.silent else float(np.mean(a.bands[:5]))
        return paint.animate(base, out.layout, t, p, level, heat, pressed)

    def run(self):
        try:
            self.capture.start(self.device_index)
        except OSError as e:
            self.error = f"Couldn't open the audio device: {e}"
            return
        with self._lock:
            for w in self.workers:
                w.start()
            self._running = True
        silent_since = None
        frame_times = []
        t_start = time.monotonic()

        while not self._quit.is_set():
            t0 = time.monotonic()
            s = self.settings
            paint_mode = s["mode"] == "paint"
            if self._effect_name != s["effect"]:
                self._effect_name = s["effect"]
                self._effect = effects.EFFECTS[s["effect"]]()

            a = self.analyzer
            a.sensitivity = s["sensitivity"] / 100
            a.smoothing = s["smoothing"] / 100
            a.eq_db = np.array(s["eq"], dtype=float)
            a.process(self.capture.snapshot(), self.capture.rate)
            self._effect.update(a)
            palette, spatial = palette_for(s)
            self.keys.fade_s = max(0.1, s["paint"]["heat_fade"] / 10)

            silent_since = (silent_since or t0) if a.silent else None
            idle = (not paint_mode and s["release_on_silence"] and silent_since is not None
                    and t0 - silent_since > SILENCE_RELEASE_S)

            frames = {}
            for w in self.workers:
                if paint_mode:
                    colors = self._paint_colors(w.output, a, t0 - t_start)
                    rgb = colors * 255                    # linear: the color you picked is the color you see
                else:
                    colors = self._render(w.output.layout, a, palette, spatial)
                    rgb = (colors ** 2.0) * 255           # LEDs are linear; gamma makes fades look even
                frames[w.output.id] = colors
                if idle:
                    w.request_release()
                    continue
                budget = w.output.power_budget
                if budget and rgb.sum() > budget:
                    rgb *= budget / rgb.sum()
                w.submit(np.ascontiguousarray(rgb, dtype=np.uint8))
            preview = self.preview
            if preview is not None and preview.id not in frames:
                frames[preview.id] = (self._paint_colors(preview, a, t0 - t_start) if paint_mode
                                      else self._render(preview.layout, a, palette, spatial))
            self.frames = frames

            frame_times = [t for t in frame_times if t0 - t < 1] + [t0]
            self.fps = len(frame_times)
            delay = 1 / s["fps"] - (time.monotonic() - t0)
            if delay > 0:
                self._quit.wait(delay)

        with self._lock:
            self._running = False
            workers = self.workers
        for w in workers:
            w.stop()
        for w in workers:
            w.join(timeout=2)
        self.capture.stop()
