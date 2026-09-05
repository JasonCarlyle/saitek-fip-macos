#!/bin/bash
# Put the six-pack on every Flight Instrument Panel attached. Linux companion
# to run.command, which is the double-clickable macOS version.
cd "$(dirname "$0")" || exit 1

need() { command -v "$1" >/dev/null 2>&1; }

if ! python3 -c "import sys; sys.exit(sys.version_info < (3, 9))" 2>/dev/null; then
    echo "Python 3.9 or newer is needed."; exit 1
fi

# System libraries. pyusb and hid are ctypes wrappers -- they need the real
# shared objects, which pip cannot install for you.
missing=""
python3 - <<'PY' || missing="libusb"
import ctypes.util, sys
sys.exit(0 if ctypes.util.find_library("usb-1.0") else 1)
PY
python3 - <<'PY' || missing="$missing hidapi"
import ctypes.util, sys
sys.exit(0 if (ctypes.util.find_library("hidapi-hidraw")
               or ctypes.util.find_library("hidapi-libusb")
               or ctypes.util.find_library("hidapi")) else 1)
PY
if [ -n "$missing" ]; then
    echo "missing system libraries:$missing"
    if need apt;    then echo "  sudo apt install libusb-1.0-0 libhidapi-hidraw0 fonts-liberation"
    elif need dnf;  then echo "  sudo dnf install libusb1 hidapi liberation-sans-fonts"
    elif need pacman; then echo "  sudo pacman -S libusb hidapi ttf-liberation"
    else echo "  install libusb-1.0 and hidapi with your package manager"; fi
    exit 1
fi

if [ ! -x .venv/bin/python ]; then
    echo "creating the Python environment…"
    python3 -m venv .venv \
        && .venv/bin/pip install -q --upgrade pip \
        && .venv/bin/pip install -q pyusb pillow hid || exit 1
fi

# Permissions are the usual first failure on Linux, and the error libusb gives
# for it is unhelpful, so say something useful before it happens.
if [ ! -e /etc/udev/rules.d/99-saitek-fip.rules ] && [ "$(id -u)" -ne 0 ]; then
    echo "note: the udev rule is not installed, so the panel may not be openable."
    echo "      sudo cp linux/99-saitek-fip.rules /etc/udev/rules.d/"
    echo "      sudo udevadm control --reload-rules && sudo udevadm trigger"
    echo "      then unplug the panel and plug it back in."
    echo
fi

exec .venv/bin/python fipx.py run "$@"
