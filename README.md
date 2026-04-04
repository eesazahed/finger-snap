# finger-snap

macOS microphone listener that detects **exactly two finger snaps** (a third snap cancels the gesture), then plays a startup sound, optionally opens **Google Chrome** to a URL, and shows a notification. Includes a small dashboard (`index.html` + `assets/`) and shell helpers to run under `launchd`.

## Repository layout

```
finger-snap/
├── main.py                  # snap listener (default) + hand-test subcommand
├── index.html
├── requirements.txt
├── install.sh
├── RunFingerSnapAgent.sh   # launchd: restart loop around main.py
├── start.sh / stop.sh
├── assets/
│   ├── audio/startupsong.wav   # default chime for double snap
│   ├── css/styles.css
│   └── js/script.js
├── README.md
└── Updates.md
```

## Requirements

- macOS (uses `afplay`, `open`, `osascript`)
- Python 3.10+
- Microphone access for the Python interpreter you use
- [PortAudio](https://formulae.brew.sh/formula/portaudio) via Homebrew if `sounddevice` fails to load: `brew install portaudio`

## Setup

**Quick install (recommended):** from the repo root, run **`./install.sh`**. It creates **`.venv`**, installs **`requirements.txt`**, then **`mediapipe`** and **`opencv-python-headless`** (needed for **`--require-hand`**, **`start.sh`** defaults, and **`main.py hand-test`**). Mic-only: **`FINGERSNAP_SKIP_HAND_DEPS=1 ./install.sh`**.

Manual equivalent:

```bash
cd finger-snap
python3 -m venv .venv
./.venv/bin/pip install -U pip
./.venv/bin/pip install -r requirements.txt
./.venv/bin/pip install mediapipe opencv-python-headless   # omit if mic-only
```

Default startup sound: **`assets/audio/startupsong.wav`**. Override with `--startup-wav` or disable with `--no-startup-sound`.

## Run

```bash
./.venv/bin/python main.py
```

**Snaps + visible hand:** install **`mediapipe`** and **`opencv-python-headless`** in the venv (same as the hand test below), allow **Camera**, then run with **`--require-hand`**. The listener still needs two valid snaps; when the post-second-snap window closes, MediaPipe must see at least one hand in frame (**presence** mode). If not, the gesture is discarded (stderr: *no hand visible*) and you can snap again. Use **`--camera-index N`** if the wrong device opens.

**Hand-in-frame test (webcam → stdout echo):** after the venv setup above, run `./.venv/bin/pip install mediapipe opencv-python-headless` (never plain `pip install` on Homebrew Python—it errors with **externally-managed-environment** / PEP 668). Default: **headless** (no camera window)—`./.venv/bin/python main.py hand-test` prints a timestamped line when a hand appears, re-arms when it leaves. **`--macos-notify`** adds **osascript** notifications; **`--preview`** draws the feed with guide lines; **`--mode raised`** uses the upper-band wrist rule instead of any hand in frame.

By default Chrome opens this repo’s **`index.html`** via a **`file://`** URL derived from **`main.py`**’s directory (works on any clone path). Useful flags: `--no-chrome`, `--no-notify`, `--no-startup-sound`, `--chrome-url 'https://...'`, `--startup-wav /path/to.wav`.

Tuning detection and timing: edit **`ListenerConfig`** at the top of **`main.py`**.

**Hand claps vs finger snaps:** By default **spectral anti-clap** compares finger snaps (bright, less low-frequency “boom”) to claps: a **low-frequency band** share (`ClapBoomBandLowHz`–`ClapBoomBandHighHz`, `MaxClapBoomHardRejectRatio`) and a **very-high** band (`AntiClapVeryHighCutoffHz`, `MinVeryHighFreqEnergyRatio`), with a **stricter HF floor** when boom is already elevated (`ClapBoomSoftThreshold`, `MinVeryHighFreqWhenBoomyPresent`). Tune in `ListenerConfig` if your room or mic behaves differently. Disable all of that with **`--disable-very-high-snap-gate`** if real snaps are missed.

## Launch Agent (background)

`start.sh` runs `stop.sh` first (if it exists) so you can restart without a separate stop, installs `~/Library/LaunchAgents/com.eesa.fingersnap.plist` (paths derived from the repo), and runs `launchctl bootstrap`. The plist runs **your venv `python3` → `main.py --supervise …` directly** (not a shell wrapper) so **macOS ties the webcam to that Python process** and the **green camera indicator** in the menu bar works like an interactive run. **`--supervise`** restarts each **listen session inside the same process** after errors or session end, with **`FINGERSNAP_RESTART_DELAY`** (default 5s) between sessions. Sustained PortAudio callback errors still end a session so mic + camera reopen on the next lap. **`ThrottleInterval`** (15s) limits how fast `launchd` respawns if the whole process exits.

By default the plist passes **`--require-hand`** (webcam + **mediapipe** / **opencv** in `.venv`); run **`FINGERSNAP_REQUIRE_HAND=0 ./start.sh`** for mic-only. Optional **`FINGERSNAP_CAMERA_INDEX=N`**. `stop.sh` unloads the agent and kills **`main.py`** / optional **`RunFingerSnapAgent.sh`** orphans from this repo path.

To change the bundle identifier or label, edit `start.sh` / `stop.sh` and the plist `Label` key together.

## Dashboard

Open **`index.html`** in a browser (local `file://` or a static host). It loads **`assets/css/styles.css`** and **`assets/js/script.js`** from the **`assets/`** folder—keep that structure when you clone or deploy.

## Changelog

See `Updates.md`.
