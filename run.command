#!/bin/bash
# Double-click me to put the six-pack on the Flight Instrument Panel.
cd "$(dirname "$0")" || exit 1

if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew is needed for libusb and hidapi: https://brew.sh"; read -r; exit 1
fi
for lib in libusb hidapi; do
    brew list --formula 2>/dev/null | grep -qx "$lib" || { echo "installing $lib…"; brew install "$lib"; }
done
if [ ! -x .venv/bin/python ]; then
    echo "creating the Python environment…"
    python3 -m venv .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q pyusb pillow hid
fi

.venv/bin/python fipx.py run "$@"
status=$?
[ $status -ne 0 ] && { echo; echo "exited with status $status — press return to close"; read -r; }
exit $status
