#!/usr/bin/env bash
set -euo pipefail

Root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
Label="com.eesa.fingersnap"
PythonExe="${Root}/.venv/bin/python3"
Listener="${Root}/SnapListener.py"
PlistDest="${HOME}/Library/LaunchAgents/${Label}.plist"

if [[ ! -x "${PythonExe}" ]]; then
	echo "Missing venv interpreter: ${PythonExe} (create .venv and pip install -r requirements.txt)" >&2
	exit 1
fi
if [[ ! -f "${Listener}" ]]; then
	echo "Missing ${Listener}" >&2
	exit 1
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
		<string>${Listener}</string>
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

echo "Finger snap listener started (${Label}). Logs: /tmp/fingersnap.out.log /tmp/fingersnap.err.log"
