#!/usr/bin/env bash
set -euo pipefail

Root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
Label="com.eesa.fingersnap"
PythonExe="${Root}/.venv/bin/python3"
Listener="${Root}/main.py"
PlistDest="${HOME}/Library/LaunchAgents/${Label}.plist"

# Double snap + webcam: require a visible hand at confirm (mediapipe + opencv in .venv).
# Mic-only: FINGERSNAP_REQUIRE_HAND=0 ./start.sh
RequireHand="${FINGERSNAP_REQUIRE_HAND:-1}"
ExtraProgramArgs=""
if [[ "${RequireHand}" == "1" ]]; then
	ExtraProgramArgs=$'\n\t\t<string>--require-hand</string>'
fi
CameraIndex="${FINGERSNAP_CAMERA_INDEX:-}"
if [[ -n "${CameraIndex}" ]]; then
	ExtraProgramArgs+=$'\n\t\t<string>--camera-index</string>\n\t\t<string>'"${CameraIndex}"'</string>'
fi

if [[ ! -x "${PythonExe}" ]]; then
	echo "Missing venv interpreter: ${PythonExe} (create .venv and pip install -r requirements.txt)" >&2
	exit 1
fi
if [[ ! -f "${Listener}" ]]; then
	echo "Missing ${Listener}" >&2
	exit 1
fi

# Unload LaunchAgent and kill any manual main.py (same as stop.sh) so start is never stacked.
if [[ -f "${Root}/stop.sh" ]]; then
	bash "${Root}/stop.sh"
fi

mkdir -p "${HOME}/Library/LaunchAgents"

Tmp="$(mktemp)"
trap 'rm -f "${Tmp}"' EXIT
cat > "${Tmp}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>${Label}</string>
	<key>ProgramArguments</key>
	<array>
		<string>${PythonExe}</string>
		<string>${Listener}</string>${ExtraProgramArgs}
	</array>
	<key>WorkingDirectory</key>
	<string>${Root}</string>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>StandardOutPath</key>
	<string>/tmp/fingersnap.out.log</string>
	<key>StandardErrorPath</key>
	<string>/tmp/fingersnap.err.log</string>
</dict>
</plist>
EOF

cp "${Tmp}" "${PlistDest}"
plutil -lint "${PlistDest}" >/dev/null

Uid="$(id -u)"
launchctl bootout "gui/${Uid}/${Label}" 2>/dev/null || true
launchctl bootstrap "gui/${Uid}" "${PlistDest}"

ModeNote=""
if [[ "${RequireHand}" == "1" ]]; then
	ModeNote=" (with --require-hand; set FINGERSNAP_REQUIRE_HAND=0 for mic-only)"
fi
echo "Finger snap listener started (${Label})${ModeNote}. Logs: /tmp/fingersnap.out.log /tmp/fingersnap.err.log"
