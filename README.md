# RGB Music Visualizer

Makes RGB keyboards, mice and ARGB lighting react to whatever is playing on
your Windows PC: spectrum bars, beat pulses, ripples, VU meters and
**Madness** (random colors everywhere, keys reacting to random frequencies, and
pixel-art shapes, figures, words and animals scrolling across), with a
per-band sensitivity equalizer.

Works with the Keyboardio Model 100, Logitech G, Razer Chroma,
Keychron/QMK (VIA), and anything OpenRGB supports - motherboard ARGB headers,
fan and strip controllers, RAM, GPUs and more.

## Install

1. Download `MusicVisualizer-<version>-windows-x64.zip` from the
   [latest release](../../releases/latest) and unzip it anywhere.
2. Run `MusicVisualizer.exe`. It lists the devices it finds; press **Start**.

Settings are saved in `%APPDATA%\MusicVisualizer`.

## Supported devices

Devices are found automatically (**Scan** re-checks). Switch each one on or
off in the device list, and click a device to preview it.

| Devices | How | Needs |
|---|---|---|
| Keyboardio Model 100 | Focus serial + MusicLEDs firmware | this project's firmware ([below](#keyboardio-model-100-firmware)); Chrysalis closed |
| Logitech G keyboards & mice | G HUB LED SDK | G HUB running, with "allow games & applications to control lighting" on |
| Razer Chroma keyboards, mice, mousepads, headsets, keypads, Chroma Link / ARGB controller | Chroma SDK REST API | Razer Synapse with the Chroma SDK / Chroma Connect module |
| Keychron and other QMK keyboards with VIA | VIA raw HID | stock VIA firmware; VIA web app closed. Whole-board color only (VIA can't stream per-key colors) |
| ARGB (motherboard headers, fan/strip controllers), RAM, GPUs, and most other RGB gear, including per-key Logitech/Razer/Keychron | OpenRGB SDK | [OpenRGB](https://openrgb.org) running with **SDK Server** started (port 6742) |

Notes:
- Don't drive the same device through two backends at once (e.g. a Razer
  keyboard via both Synapse and OpenRGB) - switch one of them off in the list.
- Vendor apps and OpenRGB fight over devices they both control. Use OpenRGB
  for Logitech/Razer gear only with G HUB/Synapse closed.
- Keychron per-key effects need QMK firmware with the OpenRGB protocol; then
  use the OpenRGB backend.
- Few-LED devices (mouse zones, whole-board keyboards) show a blend of the
  whole spectrum; strips and fan rings get the full spectrum along their length.
- With "Normal lighting when the music stops" on, devices get their own
  lighting back after 8 s of silence. Logitech devices can take a few seconds
  to switch back to the visualizer when music resumes (that's G HUB).

Tested on real hardware: Keyboardio Model 100, Logitech G610 (white per-key)
and G502 HERO via G HUB, all streaming together at ~40 fps. Razer, Keychron/VIA
and OpenRGB were tested against simulated devices only - reports welcome.

Logitech quirk: the SDK reports success even for calls a device ignores. The
G610 only accepts the key bitmap when the target is "all devices", so the app
uses that; white-only keyboards (G610, G710+) get brightness from each key's color.

## Keyboardio Model 100 firmware

The stock firmware can't take LED colors from a PC, so the Model 100 needs
`Model100-MusicLEDs-<version>.bin` from the release: the stock Model 100
firmware (Kaleidoscope 1.99.9) plus the small `MusicLEDs` plugin. The plugin
uses no EEPROM, so your Chrysalis keymap, colors and macros are kept.

To flash it, close Chrysalis and the visualizer, then either
- in Chrysalis, use **Firmware Update** with the custom firmware file option, or
- with [dfu-util](https://dfu-util.sourceforge.net/): hold **Prog** (top-left key)
  while plugging the keyboard in, then run
  `dfu-util -d 3496:0005 -D Model100-MusicLEDs-<version>.bin -R`

The plugin adds these Focus serial commands:

| Command | Effect |
|---|---|
| `music.frame <384 hex>` | Show one frame: 64 keys × `RRGGBB`, matrix order (4 rows × 16 cols) |
| `music.stop` | Give the LEDs back to the normal LED mode |
| `music.info` | `rows cols timeout_ms` |

If no frame arrives for 1.5 s, the keyboard returns to its normal lighting by
itself. If Chrysalis later flashes its stock firmware, the visualizer shows
"Firmware can't take LED frames" - flash this firmware again.

## Building from source

Requires Windows and Python 3.12.

```bat
python -m venv app\.venv
app\.venv\Scripts\pip install -r app\requirements.txt
Visualizer.bat
```

Release build (standalone app zip + firmware .bin into `dist\`):

```bat
app\.venv\Scripts\pip install -r packaging\requirements-dev.txt
app\.venv\Scripts\python packaging\build_release.py
```

Firmware: install [arduino-cli](https://arduino.github.io/arduino-cli/), add the
board index `https://raw.githubusercontent.com/keyboardio/boardsmanager/main/package_keyboardio_index.json`,
install the `keyboardio:gd32` core, then run `firmware\flash.bat [COMport]`
(builds, reboots the keyboard into its bootloader - hold **Prog** - and flashes).

### Layout

| Path | What |
|---|---|
| `app/app.py`, `widgets.py` | UI (CustomTkinter) |
| `app/engine.py` | render loop; one sender thread per device |
| `app/audio.py` | WASAPI loopback capture, FFT bands, beat detection |
| `app/effects.py`, `layout.py` | effects, rendered onto any device layout |
| `app/devices/` | one module per backend (`model100`, `logitech`, `razer`, `openrgb_devices`, `qmk_via`) |
| `firmware/Model100/` | Model 100 sketch + `MusicLEDs.h` plugin |

## License

GPL-3.0 - see [LICENSE](LICENSE). The firmware is based on
[Kaleidoscope](https://github.com/keyboardio/Kaleidoscope) (GPL-3.0).
