#!/usr/bin/env bash
# Optional: manual restart loop around main.py. launchd uses main.py --supervise instead so Python
# (not bash) owns the camera and the green menu bar indicator appears.
set -uo pipefail

Root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
Py="${Root}/.venv/bin/python3"
Main="${Root}/main.py"
Delay="${FINGERSNAP_RESTART_DELAY:-5}"

if [[ ! -x "${Py}" ]] || [[ ! -f "${Main}" ]]; then
	echo "RunFingerSnapAgent.sh: missing ${Py} or ${Main}" >&2
	exit 1
fi

while true; do
	"${Py}" "${Main}" "$@"
	Code=$?
	echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') finger-snap: main.py exited ${Code}, restarting in ${Delay}s" >>/tmp/fingersnap.err.log
	sleep "${Delay}"
done
