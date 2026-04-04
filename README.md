# finger-snap

macOS microphone listener that detects **exactly two finger snaps** (a third snap cancels the gesture), then plays a startup sound, optionally opens **Google Chrome** to a URL, and shows a notification. Includes a small **Hello** dashboard (`index.html`) and shell helpers to run under `launchd`.

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

Place `startupsong.wav` next to `SnapListener.py` (or use `--startup-wav` / `--no-startup-sound`).

## Run

```bash
./.venv/bin/python SnapListener.py
```

Useful flags: `--no-chrome`, `--no-notify`, `--no-startup-sound`, `--chrome-url 'https://...'`, `--startup-wav /path/to.wav`.

Tuning detection and timing: edit `ListenerConfig` at the top of `SnapListener.py`.

## Launch Agent (background)

`start.sh` installs `~/Library/LaunchAgents/com.eesa.fingersnap.plist` (paths derived from the repo) and runs `launchctl bootstrap`. `stop.sh` unloads the agent and kills a manual `SnapListener.py` from this repo path.

To change the bundle identifier or label, edit `start.sh` / `stop.sh` and the plist `Label` key together.

## Dashboard

Open `index.html` in a browser (local file or static host) for the centred favourites grid and clock.

## Changelog

See `Updates.md`.
