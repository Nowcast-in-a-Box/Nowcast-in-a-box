#!/bin/sh
# Format every Python file in the repository with Black.
cd "$(dirname "$0")/.." || exit 1
exec black --config black.toml .
