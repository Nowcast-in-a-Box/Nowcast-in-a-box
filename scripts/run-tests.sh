#!/usr/bin/env bash
# Unified local test entry: Go handler suite plus the four pytest suites.
#
# Local runs are a precheck only. GitHub Actions (.github/workflows/ci.yml)
# is the authoritative gate; its jobs install the pinned lock files listed
# below and run the same suites.
#
# Python interpreter: .pi/environments/runtime if present, else python3 with
# the suite lock files already installed:
#   pip install -r tests/requirements-dev.lock.txt
#   pip install -r diagnostics/verification/requirements-dev.lock.txt
#   pip install -r diagnostics/visualization/requirements-viewer.lock.txt

set -uo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

# Every Python suite imports repo-root packages; tests/runtime reaches
# diagnostics/verification (nib_verify) via diagnostics.run_source, and
# tests/visualization needs diagnostics/visualization and its src.
export PYTHONPATH="$ROOT:$ROOT/diagnostics/verification:$ROOT/diagnostics/visualization:$ROOT/diagnostics/visualization/src"

if [ -x ".pi/environments/runtime/bin/python" ]; then
    PY=".pi/environments/runtime/bin/python"
else
    PY="python3"
    echo "note: .pi/environments/runtime not found, using python3 (see header for lock installs)" >&2
fi

failures=0
declare -a names=()
declare -a results=()

record() { # name, status
    names+=("$1")
    results+=("$2")
    [ "$2" = "FAIL" ] && failures=$((failures + 1))
    return 0
}

echo "== go vet =="
if go vet ./...; then record "go vet" "PASS"; else record "go vet" "FAIL"; fi

echo
echo "== go test =="
if go test ./...; then record "go test" "PASS"; else record "go test" "FAIL"; fi

py_suite() { # label, args...
    local label="$1"
    shift
    echo
    echo "== pytest $label =="
    if "$PY" -m pytest "$@"; then record "pytest $label" "PASS"; else record "pytest $label" "FAIL"; fi
}

py_suite "adapters+runtime" tests/adapters tests/runtime
py_suite "verification" tests/verification
py_suite "visualization" tests/visualization

echo
echo "==================== summary ===================="
for i in "${!names[@]}"; do
    printf '%-28s %s\n' "${names[$i]}" "${results[$i]}"
done
echo "================================================="

exit "$([ "$failures" -eq 0 ] && echo 0 || echo 1)"
