#!/usr/bin/env bash
# Create .venv and install Python deps for finger-snap (mic + optional webcam hand gate).
set -euo pipefail

Root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${Root}"

if ! command -v python3 >/dev/null 2>&1; then
	echo "install.sh: python3 not found. Install Python 3.10+ and try again." >&2
	exit 1
fi

if [[ -d "${Root}/.venv" ]]; then
	echo "Using existing venv at ${Root}/.venv …"
else
	echo "Creating venv at ${Root}/.venv …"
	python3 -m venv "${Root}/.venv"
fi

Pip="${Root}/.venv/bin/pip"
"${Pip}" install -U pip

if [[ ! -f "${Root}/requirements.txt" ]]; then
	echo "install.sh: missing requirements.txt" >&2
	exit 1
fi

echo "Installing requirements.txt (numpy, sounddevice) …"
"${Pip}" install -r "${Root}/requirements.txt"

if [[ "${FINGERSNAP_SKIP_HAND_DEPS:-0}" == "1" ]]; then
	echo "Skipping mediapipe / opencv (FINGERSNAP_SKIP_HAND_DEPS=1). Use FINGERSNAP_REQUIRE_HAND=0 ./start.sh for mic-only."
else
	echo "Installing mediapipe + opencv-python-headless (webcam / --require-hand / hand-test) …"
	"${Pip}" install mediapipe opencv-python-headless
fi

chmod +x "${Root}/start.sh" 2>/dev/null || true

echo ""
echo "Done. Examples:"
echo "  ${Root}/.venv/bin/python main.py"
echo "  ${Root}/.venv/bin/python main.py --require-hand"
echo "  ${Root}/.venv/bin/python main.py hand-test"
echo ""
echo "If sounddevice fails to load, try: brew install portaudio"
