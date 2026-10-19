#!/bin/sh
# Format the repository by default, or only the explicitly named paths.
cd "$(dirname "$0")/.." || exit 1
if [ "$#" -eq 0 ]; then
    set -- .
fi
exec black --config black.toml "$@"
