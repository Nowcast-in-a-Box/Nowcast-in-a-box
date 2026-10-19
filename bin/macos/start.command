#!/bin/bash
# macOS entry point. Double-click from Finder, or run from a terminal.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
REPO="Nowcast-in-a-Box/Nowcast-in-a-box"

uname_s="$(uname -s)"
if [[ "$uname_s" != Darwin ]]; then
	echo "ERROR: this entry is for macOS. Linux: bin/linux/run.sh  Windows: bin/windows/run.ps1" >&2
	exit 1
fi
uname_m="$(uname -m)"
case "$uname_m" in
	x86_64|amd64) goarch=amd64 ;;
	arm64|aarch64) goarch=arm64 ;;
	*)
		echo "ERROR: unsupported architecture $uname_m" >&2
		exit 1
		;;
esac

name="nib-handler-darwin-${goarch}"
bin="${NIB_HANDLER_BIN:-$HERE/$name}"
if [[ ! -x "$bin" ]]; then
	url="https://github.com/${REPO}/releases/latest/download/${name}"
	echo "fetching $url" >&2
	if ! command -v curl >/dev/null 2>&1; then
		echo "ERROR: curl is required to download the handler binary." >&2
		exit 1
	fi
	if ! curl -fL --retry 3 -o "$bin" "$url"; then
		rm -f "$bin"
		echo "ERROR: no handler binary at $bin" >&2
		echo "Download it from a GitHub Release, or build with scripts/cross-build.sh, and put it there." >&2
		exit 1
	fi
	chmod +x "$bin"
fi

bin="$(cd "$(dirname "$bin")" && pwd)/$(basename "$bin")"
config="${NIB_HANDLER_CONFIG:-$ROOT/config.yaml}"
action=start
case "${1:-}" in
	start|serve|status|stop|restart|connect|diagnostics) action="$1"; shift ;;
	nib://*) action=connect ;;
esac

if [[ "${NIB_SKIP_URI_REGISTRATION:-}" != 1 && ( "$action" == start || "$action" == connect || "$action" == restart ) ]]; then
	# Register the configuration actually selected by this invocation.
	arguments=("$@")
	bind_host=""
	bind_port=""
	for ((i=0; i<${#arguments[@]}; i++)); do
		case "${arguments[$i]}" in
			-config) config="${arguments[$((i+1))]:-}" ;;
			-config=*) config="${arguments[$i]#-config=}" ;;
			-host) bind_host="${arguments[$((i+1))]:-}" ;;
			-host=*) bind_host="${arguments[$i]#-host=}" ;;
			-port) bind_port="${arguments[$((i+1))]:-}" ;;
			-port=*) bind_port="${arguments[$i]#-port=}" ;;
		esac
	done
	if [[ ! -f "$config" ]]; then
		echo "ERROR: Handler configuration does not exist: $config" >&2
		exit 1
	fi
	config="$(cd "$(dirname "$config")" && pwd)/$(basename "$config")"
	app="$HOME/Applications/NiB Handler.app"
	stamp="$app/Contents/Resources/nib-connection-config"
	registration="$(printf '%s\n' 'nib-handler-app-v3' "$bin" "$config" "$bind_host" "$bind_port")"
	if [[ ! -f "$stamp" || "$(< "$stamp")" != "$registration" ]]; then
		mkdir -p "$(dirname "$app")"
		app_build="${app%.app}.new.app"
		applescript="$(mktemp -t nib-handler-script)"
		trap 'rm -f "$applescript"; rm -rf "$app_build"' EXIT
		rm -rf "$app_build"
		escape_applescript() {
			local value="$1"
			value="${value//\\/\\\\}"
			value="${value//\"/\\\"}"
			printf '%s' "$value"
		}
		program="$(escape_applescript "$bin")"
		configuration="$(escape_applescript "$config")"
		overrides=""
		if [[ -n "$bind_host" ]]; then
			overrides+=" & \" -host \" & (quoted form of \"$(escape_applescript "$bind_host")\")"
		fi
		if [[ -n "$bind_port" ]]; then
			overrides+=" & \" -port \" & (quoted form of \"$(escape_applescript "$bind_port")\")"
		fi
		# URL schemes arrive as AppleEvents, not as a shell application's $1.
		printf 'on run\n do shell script (quoted form of "%s") & " start -config " & (quoted form of "%s")%s\nend run\non open location theURL\n do shell script (quoted form of "%s") & " connect -config " & (quoted form of "%s")%s & " " & (quoted form of theURL)\nend open location\n' "$program" "$configuration" "$overrides" "$program" "$configuration" "$overrides" > "$applescript"
		/usr/bin/osacompile -o "$app_build" "$applescript"
		plist="$app_build/Contents/Info.plist"
		/usr/libexec/PlistBuddy -c 'Set :CFBundleIdentifier app.nowcast.nib-handler' "$plist" 2>/dev/null ||
			/usr/libexec/PlistBuddy -c 'Add :CFBundleIdentifier string app.nowcast.nib-handler' "$plist"
		/usr/libexec/PlistBuddy -c 'Delete :CFBundleURLTypes' "$plist" >/dev/null 2>&1 || true
		/usr/libexec/PlistBuddy -c 'Add :CFBundleURLTypes array' -c 'Add :CFBundleURLTypes:0 dict' -c 'Add :CFBundleURLTypes:0:CFBundleURLSchemes array' -c 'Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string nib' "$plist"
		/usr/bin/codesign --force --sign - "$app_build"
		rm -rf "$app"
		mv "$app_build" "$app"
		printf '%s\n' "$registration" > "$stamp"
	fi
	lsregister="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
	"$lsregister" -f "$app"
fi

if [[ "$action" == start ]]; then
	# launchctl bootstrap returns 5 on this host; keep that one failure
	# interactive. Other binary failures stay visible to the caller.
	if "$bin" start -config "$config" "$@"; then
		exit 0
	else
		status=$?
	fi
	if [[ "$status" -eq 5 ]]; then
		echo "background startup failed; keeping the Handler in this Terminal window" >&2
		exec "$bin" serve -config "$config" "$@"
	fi
	exit "$status"
fi
exec "$bin" "$action" -config "$config" "$@"
