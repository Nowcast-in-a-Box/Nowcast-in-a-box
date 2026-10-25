#!/usr/bin/env bash
# Remove the locally installed Handler on Linux.
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

Usage: Uninstall NiB.sh [--dry-run] [--yes] [--purge]

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
  local arch candidate
  case "$(uname -m)" in
    arm64|aarch64) arch=arm64 ;;
    x86_64|amd64) arch=amd64 ;;
    *) return 0 ;;
  esac
  candidate="$ROOT/bin/linux/nib-handler-linux-$arch"
  if [[ -x "$candidate" ]]; then
    printf '%s' "$candidate"
    return
  fi
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
  elif ! "$BINARY" uninstall -config "$CONFIG"; then
    echo "ERROR: the Handler is still serving its port and could not be stopped. Nothing was removed." >&2
    exit 1
  fi
else
  echo "ERROR: no Handler binary found; cannot verify shutdown. Nothing was removed." >&2
  exit 1
fi

DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE="$DESKTOP_DIR/nib-handler.desktop"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/NiB/handler"

if ((DRY_RUN)); then
  remove_path "$DESKTOP_FILE"
  remove_path "$CACHE_DIR"
fi
remove_path "$ROOT/nib-settings.json"
remove_path "$ROOT/logs/handler.log"

if ! ((DRY_RUN)) && command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi

if ((PURGE)); then
  prune_generated_dir "$ROOT/runs"
  prune_generated_dir "$ROOT/artifacts"
  remove_path "$ROOT/resources"
  remove_path "$ROOT/logs"
  remove_path "$ROOT/tmp"
  local_candidate=""
  for local_candidate in "$ROOT/bin/linux/nib-handler-linux-"* "$ROOT/bin/nib-handler"; do
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
