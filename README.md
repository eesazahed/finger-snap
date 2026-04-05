# finger-snap

macOS microphone listener that detects **exactly two finger snaps** (a third snap cancels the gesture), then plays a startup sound, optionally opens your **default browser** (or a chosen app) to the dashboard URL, and prints **Double snap detected.** to stdout. Includes a small dashboard (`index.html` + `assets/`) and **`./start.sh`** to run the listener in the background (**`nohup`**).

## Repository layout

```
finger-snap/
├── main.py                  # snap listener + optional palm swipe --hand-gesture (Mission Control ⌃↑) + hand-test
├── index.html
├── requirements.txt
├── install.sh
├── start.sh                 # background listener + stop/status subcommands
├── assets/
│   ├── audio/startupsong.wav   # default chime for double snap
│   ├── css/styles.css
│   └── js/script.js
├── README.md
└── Updates.md
```

## Requirements

- macOS (uses `afplay`, `open`)
- Python 3.10+
- Microphone access for the Python interpreter you use
- [PortAudio](https://formulae.brew.sh/formula/portaudio) via Homebrew if `sounddevice` fails to load: `brew install portaudio`

## Setup

**Quick install (recommended):** from the repo root, run **`./install.sh`**. It creates **`.venv`**, installs **`requirements.txt`**, then **`mediapipe`** and **`opencv-python-headless`** (camera / **`--require-hand`** / **`--hand-gesture`**). Mic-only: **`FINGERSNAP_SKIP_HAND_DEPS=1 ./install.sh`**.

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

**Palm swipe → Mission Control:** same camera thread — **`--hand-gesture`** uses **swipe up** / **swipe down** on palm height (sensitive defaults: **`--gesture-min-delta-y`**, **`--gesture-min-speed`**, **`--gesture-history-frames`**). **⌃↑** opens when the app assumes MC is off and closes when it assumes on (state follows your swipes; keyboard toggles can desync until you swipe once to match). Mic-only: omit **`--require-hand`** and **`--hand-gesture`**. **`./start.sh`** does **not** pass **`--hand-gesture`** unless **`FINGERSNAP_HAND_GESTURE=1`** or **`./start.sh --hand-gesture`**.

**Hand-in-frame test (webcam → stdout echo):** after the venv setup above, run `./.venv/bin/pip install mediapipe opencv-python-headless` (never plain `pip install` on Homebrew Python—it errors with **externally-managed-environment** / PEP 668). Default: **headless** (no camera window)—`./.venv/bin/python main.py hand-test` prints a timestamped line when a hand appears, re-arms when it leaves. **`--preview`** draws the feed with guide lines; **`--mode raised`** uses the upper-band wrist rule instead of any hand in frame.

**`main.py`** opens the dashboard with **`open -a "Google Chrome"`** by default (see **`ListenerConfig.ChromeAppName`**). **`./start.sh`** forwards extra arguments to **`main.py`**, e.g. **`./start.sh --no-chrome`**. New tab vs new window depends on Chrome; for another browser you’d extend **`main.py`** or change **`ChromeAppName`**.

Useful flags: `--no-chrome` (skip opening a browser), `--no-startup-sound`, `--chrome-url`, `--startup-wav /path/to.wav`.

Tuning detection and timing: edit **`ListenerConfig`** at the top of **`main.py`**.

**Hand claps vs finger snaps:** By default **spectral anti-clap** compares finger snaps (bright, less low-frequency “boom”) to claps: a **low-frequency band** share (`ClapBoomBandLowHz`–`ClapBoomBandHighHz`, `MaxClapBoomHardRejectRatio`) and a **very-high** band (`AntiClapVeryHighCutoffHz`, `MinVeryHighFreqEnergyRatio`), with a **stricter HF floor** when boom is already elevated (`ClapBoomSoftThreshold`, `MinVeryHighFreqWhenBoomyPresent`). Tune in `ListenerConfig` if your room or mic behaves differently. Disable all of that with **`--disable-very-high-snap-gate`** if real snaps are missed.

## Background listener (`start.sh`)

One script replaces separate **`stop.sh`** / **`RunFingerSnapAgent.sh`** / **`launchd`** install:

| Command | Action |
|--------|--------|
| **`./start.sh`** | **`launchctl bootout`** any old **`com.eesa.fingersnap`** job, kill PID in **`.finger-snap.pid`**, kill stray **`main.py`** for this repo, rotate **`fingersnap.log`** if over **`FINGERSNAP_LOG_MAX_MB`** (default 8), then **`nohup`** **`main.py --supervise`**, **`--require-hand`** (unless **`FINGERSNAP_REQUIRE_HAND=0`**), **`--hand-gesture`** only if **`FINGERSNAP_HAND_GESTURE=1`** or passed on the command line. |
| **`./start.sh stop`** | Same shutdown (no new process). |
| **`./start.sh status`** | Print PID and log path if the saved PID is alive. |

Logs append to **`fingersnap.log`** in the repo (and **`fingersnap.log.1`** after rotation). **`FINGERSNAP_REQUIRE_HAND=0`**, **`FINGERSNAP_HAND_GESTURE=1`** (add palm-swipe / Mission Control), **`FINGERSNAP_CAMERA_INDEX`**, **`FINGERSNAP_RESTART_DELAY`** behave like before. Extra args go to **`main.py`**, e.g. **`./start.sh --no-chrome`** or **`./start.sh --hand-gesture`**.

Foreground helpers: **`./start.sh --help`**, **`./start.sh hand-test --preview`**.

Run **`./start.sh`** from Terminal (or a session with mic/camera access). If you previously used **`launchd`**, **`start.sh`** **bootouts** that label so you do not get two listeners; you may delete **`~/Library/LaunchAgents/com.eesa.fingersnap.plist`** if it is still on disk. To auto-start at login, add your own **LaunchAgent** whose **`ProgramArguments`** run **`/path/to/finger-snap/start.sh`** once (**`RunAtLoad`**, no **`KeepAlive`**, since **`nohup`** detaches the real Python process).

## Dashboard

Open **`index.html`** in a browser (local `file://` or a static host). It loads **`assets/css/styles.css`** and **`assets/js/script.js`** from the **`assets/`** folder—keep that structure when you clone or deploy.

## Changelog

See `Updates.md`.
