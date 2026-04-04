# Updates

## 2026-04-04

- **GitHub / clone consistency:** **`assets/css/styles.css`**, **`assets/js/script.js`**, **`assets/audio/startupsong.wav`** are the canonical dashboard + chime paths; root **`startupsong.wav`** removed (duplicate). **`SnapListener.py`**: default WAV is **`assets/audio/startupsong.wav`**; default Chrome URL is **`Path.as_uri()`** for **`index.html`** next to the script (no hardcoded `/Users/...` path). **`README.md`**: repo layout tree and deploy note for **`assets/`**.
- Reverted in-page startup sound: removed `<audio>` from **`index.html`** and **`load`** playback from **`assets/js/script.js`**.
- Restored `SnapListener.py`: microphone double-snap detection, Chrome tab + notification on match; CLI `--no-notify`, `--chrome-url`, `--no-chrome`.
- `SnapListener.py`: snap vs keyboard discrimination — HF share, high vs low–mid band ratio, crest; **relaxed defaults** + **spectral pre-emphasis** (FFT only) for laptop mics; **HiLo stays mandatory**, with one partner (HF or crest) allowed slightly below threshold when the other is strong.
- `SnapListener.py`: **exactly-two-snaps** gesture — after a valid 2nd snap, wait `ThirdSnapRejectSeconds`; a **3rd snap cancels**; on success, **1 s** `ListenCooldownAfterTriggerSeconds` before listening again.
- `SnapListener.py`: on confirmed double snap, play **`assets/audio/startupsong.wav`** (by default) via **`afplay`** (non-blocking `Popen`); `--no-startup-sound`, `--startup-wav PATH`.
- Added `requirements.txt` (`numpy`, `sounddevice`).
- Added `launchd/com.eesa.fingersnap.plist.example` (template) and optional machine-specific plist under `launchd/`.
- Added `start.sh`: writes `~/Library/LaunchAgents/com.eesa.fingersnap.plist` from this repo’s paths, then `launchctl bootstrap` so `SnapListener.py` runs under `launchd`.
- Added `stop.sh`: `launchctl bootout` for `com.eesa.fingersnap` and `pkill` for any in-terminal `SnapListener.py` from this repo path.
- `index.html`: live greeting + clock; task list + **month calendar**; **centred 4×4** favourites grid (16 tiles).
- Repo hygiene for GitHub: `.gitignore` (venv, caches, OS/editor noise), `README.md`, initial Git commit instructions left to user after `git remote add`.
- `git remote add` / `origin` → `https://github.com/eesazahed/finger-snap.git`; push must be run locally with GitHub auth (`gh auth login`, credential manager, or PAT).
