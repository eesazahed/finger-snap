# finger-snap

macOS microphone listener that detects **exactly two finger snaps** (a third snap cancels the gesture), then plays a startup sound, optionally opens **Google Chrome** to a URL, and shows a notification. Includes a small dashboard (`index.html` + `assets/`) and shell helpers to run under `launchd`.

## Repository layout

```
finger-snap/
├── SnapListener.py
├── index.html
├── requirements.txt
├── start.sh / stop.sh
├── assets/
│   ├── audio/startupsong.wav   # default chime for SnapListener
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

```bash
cd finger-snap
python3 -m venv .venv
source .venv/bin/activate   # or: .venv/bin/pip install -r requirements.txt
pip install -r requirements.txt
```

Default startup sound: **`assets/audio/startupsong.wav`**. Override with `--startup-wav` or disable with `--no-startup-sound`.

## Run

```bash
./.venv/bin/python SnapListener.py
```

By default Chrome opens this repo’s **`index.html`** via a **`file://`** URL derived from `SnapListener.py`’s location (works on any clone path). Useful flags: `--no-chrome`, `--no-notify`, `--no-startup-sound`, `--chrome-url 'https://...'`, `--startup-wav /path/to.wav`.

Tuning detection and timing: edit `ListenerConfig` at the top of `SnapListener.py`.

## Launch Agent (background)

`start.sh` installs `~/Library/LaunchAgents/com.eesa.fingersnap.plist` (paths derived from the repo) and runs `launchctl bootstrap`. `stop.sh` unloads the agent and kills a manual `SnapListener.py` from this repo path.

To change the bundle identifier or label, edit `start.sh` / `stop.sh` and the plist `Label` key together.

## Dashboard

Open **`index.html`** in a browser (local `file://` or a static host). It loads **`assets/css/styles.css`** and **`assets/js/script.js`** from the **`assets/`** folder—keep that structure when you clone or deploy.

## Changelog

See `Updates.md`.
