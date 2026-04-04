#!/usr/bin/env bash
set -euo pipefail

Label="com.eesa.fingersnap"
Uid="$(id -u)"
Root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

launchctl bootout "gui/${Uid}/${Label}" 2>/dev/null || true
if pkill -f "${Root}/main.py" 2>/dev/null; then
	echo "Stopped manual main.py process(es)."
fi

echo "Finger snap listener stopped (${Label})."
echo "Plist remains at ~/Library/LaunchAgents/${Label}.plist (login may reload it). Remove that file to disable auto-start."
