#!/bin/bash
# Remove the locally installed Handler on macOS.
# Results, weights and the user's data folder are kept unless --purge is given.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
CONFIG="$ROOT/config.yaml"
DRY_RUN=0
ASSUME_YES=0
PURGE=0

usage() {
  cat <<'EOF'
Remove the local NiB Handler installation.

Usage: Uninstall NiB.command [--dry-run] [--yes] [--purge]

  --dry-run   Show what would be removed; change nothing.
  --yes       Do not ask for confirmation.
  --purge     Also remove generated runs, artifacts, resources, logs and
              downloaded Handler binaries. The user's data folder is never
              removed.
EOF
}

while (($#)); do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --yes|-y) ASSUME_YES=1 ;;
    --purge) PURGE=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

remove_path() {
  local path="$1"
  if [[ ! -e "$path" && ! -L "$path" ]]; then
    return
  fi
  if ((DRY_RUN)); then
    printf 'would remove %s\n' "$path"
  else
    printf 'remove %s\n' "$path"
    rm -rf -- "$path"
  fi
}

confirm() {
  local prompt="$1"
  if ((ASSUME_YES)); then
    return
  fi
  local answer=""
  read -r -p "$prompt [y/N] " answer || true
  [[ "$answer" == "y" || "$answer" == "Y" ]]
}

find_binary() {
  if [[ -n "${NIB_HANDLER_BIN:-}" && -x "${NIB_HANDLER_BIN}" ]]; then
    printf '%s' "${NIB_HANDLER_BIN}"
    return
  fi
  local candidate
  for candidate in "$ROOT/bin/macos/nib-handler-darwin-"*; do
    if [[ -x "$candidate" ]]; then
      printf '%s' "$candidate"
      return
    fi
  done
  if [[ -x "$ROOT/bin/nib-handler" ]]; then
    printf '%s' "$ROOT/bin/nib-handler"
  fi
  return 0
}

prune_generated_dir() {
  local directory="$1"
  [[ -d "$directory" ]] || return
  local item
  while IFS= read -r -d '' item; do
    [[ "$(basename "$item")" == ".gitkeep" ]] && continue
    remove_path "$item"
  done < <(find "$directory" -mindepth 1 -maxdepth 1 -print0)
}

BINARY="$(find_binary)"
APP="$HOME/Applications/NiB Handler.app"
CACHE="$HOME/Library/Caches/NiB/handler"
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"

if ! ((DRY_RUN)); then
  if ! confirm "Remove the local NiB Handler installation? Runs and artifacts will be kept."; then
    echo "Cancelled."
    exit 1
  fi
  if ((PURGE)); then
    if ! confirm "PURGE also removes generated runs, artifacts and downloaded binaries. Continue?"; then
      echo "Cancelled."
      exit 1
    fi
  fi
fi

if [[ -n "$BINARY" ]]; then
  if ((DRY_RUN)); then
    printf 'would stop the Handler using %s if it is running\n' "$BINARY"
  elif "$BINARY" status -config "$CONFIG" >/dev/null 2>&1; then
    if ! "$BINARY" stop -config "$CONFIG"; then
      echo "ERROR: the Handler is running and could not be stopped; an active job may still be running. Nothing was removed." >&2
      exit 1
    fi
  fi
else
  echo "No local Handler binary found; removing registration and local state only."
fi

if ((DRY_RUN)); then
  while IFS= read -r label; do
    [[ -n "$label" ]] || continue
    printf 'would remove launchd job %s\n' "$label"
  done < <(launchctl list 2>/dev/null | awk '$3 ~ /^app\.nowcast\.nib-handler\./ {print $3}')
  if [[ -d "$APP" ]]; then
    printf 'would unregister %s\n' "$APP"
  fi
else
  while IFS= read -r label; do
    [[ -n "$label" ]] || continue
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null ||
      launchctl remove "$label" 2>/dev/null || true
  done < <(launchctl list 2>/dev/null | awk '$3 ~ /^app\.nowcast\.nib-handler\./ {print $3}')
  if [[ -x "$LSREGISTER" && -d "$APP" ]]; then
    "$LSREGISTER" -u "$APP" >/dev/null 2>&1 || true
  fi
fi

remove_path "$APP"
remove_path "$CACHE"
remove_path "$ROOT/nib-settings.json"
remove_path "$ROOT/logs/handler.log"

if ((PURGE)); then
  prune_generated_dir "$ROOT/runs"
  prune_generated_dir "$ROOT/artifacts"
  remove_path "$ROOT/resources"
  remove_path "$ROOT/logs"
  remove_path "$ROOT/tmp"
  local_candidate=""
  for local_candidate in "$ROOT/bin/macos/nib-handler-darwin-"* "$ROOT/bin/nib-handler"; do
    [[ -e "$local_candidate" ]] || continue
    remove_path "$local_candidate"
  done
fi

if ((DRY_RUN)); then
  echo "Dry run complete; no changes were made."
  exit 0
fi

echo "Local NiB Handler installation removed."
if ((PURGE)); then
  echo "Generated runs, artifacts, resources and local binaries were also removed."
else
  echo "Runs, artifacts and your data folder were kept."
fi
