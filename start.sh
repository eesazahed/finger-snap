#!/usr/bin/env bash
# One script: stop any previous instance, run main.py in the background (nohup).
#
#   ./start.sh                    # background: --supervise [--require-hand] [--hand-gesture]
#   ./start.sh stop               # kill PID + old launchd job + stray main.py for this repo
#   ./start.sh status             # show PID / log if alive
#   ./start.sh --help             # → main.py --help
#   ./start.sh hand-test …        # foreground: exec main.py hand-test …
#
# Extra args are appended to main.py on background start, e.g. ./start.sh --no-chrome
#
# Env: FINGERSNAP_REQUIRE_HAND (default 1), FINGERSNAP_HAND_GESTURE (default 1; set 0 to omit --hand-gesture),
#      FINGERSNAP_CAMERA_INDEX, FINGERSNAP_RESTART_DELAY,
#      FINGERSNAP_LOG_MAX_MB (default 8, rotate fingersnap.log → fingersnap.log.1)
#
set -euo pipefail

Root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$Root"

Python="${Root}/.venv/bin/python3"
Main="${Root}/main.py"
PidFile="${Root}/.finger-snap.pid"
LogFile="${Root}/fingersnap.log"
LaunchLabel="com.eesa.fingersnap"

if [[ ! -x "$Python" ]]; then
	echo "Missing .venv: ${Python} — run ./install.sh or python3 -m venv .venv && pip install -r requirements.txt" >&2
	exit 1
fi
if [[ ! -f "$Main" ]]; then
	echo "Missing ${Main}" >&2
	exit 1
fi

StopLaunchd() {
	local Uid
	Uid="$(id -u)"
	launchctl bootout "gui/${Uid}/${LaunchLabel}" 2>/dev/null || true
}

StopPidFile() {
	if [[ ! -f "$PidFile" ]]; then
		return 0
	fi
	local Old
	Old="$(cat "$PidFile" 2>/dev/null || true)"
	if [[ -n "$Old" ]] && kill -0 "$Old" 2>/dev/null; then
		echo "Stopping previous PID ${Old}"
		kill "$Old" 2>/dev/null || true
		sleep 1
		if kill -0 "$Old" 2>/dev/null; then
			kill -9 "$Old" 2>/dev/null || true
		fi
	fi
	rm -f "$PidFile"
}

PkillRepoMain() {
	if pkill -f "${Root}/main\\.py" 2>/dev/null; then
		echo "Stopped stray ${Main} process(es)."
		sleep 1
	fi
}

case "${1:-}" in
	stop)
		StopLaunchd
		StopPidFile
		PkillRepoMain
		echo "Stopped."
		exit 0
		;;
	status)
		if [[ -f "$PidFile" ]] && kill -0 "$(cat "$PidFile" 2>/dev/null)" 2>/dev/null; then
			echo "Running PID $(cat "$PidFile") — log: ${LogFile}"
		else
			echo "Not running (no valid PID in ${PidFile})"
		fi
		exit 0
		;;
	-h | --help)
		exec "$Python" "$Main" --help
		;;
	hand-test)
		exec "$Python" "$Main" "$@"
		;;
esac

RotateLogIfLarge() {
	local MaxMb="${FINGERSNAP_LOG_MAX_MB:-8}"
	[[ "$MaxMb" =~ ^[0-9]+$ ]] && [[ "$MaxMb" -gt 0 ]] || return 0
	[[ -f "$LogFile" ]] || return 0
	local Bytes
	Bytes="$(stat -f%z "$LogFile" 2>/dev/null || stat -c%s "$LogFile" 2>/dev/null || echo 0)"
	local Limit=$((MaxMb * 1024 * 1024))
	if ((Bytes > Limit)); then
		mv -f "$LogFile" "${LogFile}.1"
		echo "Rotated log (${Bytes} bytes > ${MaxMb} MiB) -> ${LogFile}.1" >&2
	fi
}

StopLaunchd
StopPidFile
PkillRepoMain

RotateLogIfLarge
touch "$LogFile"

Args=(--supervise)
if [[ "${FINGERSNAP_REQUIRE_HAND:-1}" == "1" ]]; then
	Args+=(--require-hand)
fi
if [[ "${FINGERSNAP_HAND_GESTURE:-1}" == "1" ]]; then
	Args+=(--hand-gesture)
fi
if [[ -n "${FINGERSNAP_CAMERA_INDEX:-}" ]]; then
	Args+=(--camera-index "$FINGERSNAP_CAMERA_INDEX")
fi

nohup "$Python" "$Main" "${Args[@]}" "$@" >>"$LogFile" 2>&1 &
echo $! >"$PidFile"
echo "Started PID $(cat "$PidFile") in background."
echo "Log: ${LogFile}"
echo "Stop: ${Root}/start.sh stop   Status: ${Root}/start.sh status"
