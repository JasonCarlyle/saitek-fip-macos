"""The FIP's buttons and knobs, over hidapi.

Interface 0 is an ordinary HID keypad that macOS claims for itself, so we read
it the supported way rather than wrestling it away from the kernel. Its report
descriptor asks for twelve one-bit buttons in a two-byte report:

    Usage Page (Button)  Usage Min 1  Usage Max 12
    Report Size 1  Report Count 12  Input (Data,Var,Abs)
    Report Size 1  Report Count 4   Input (Const)      <- padding

Six of those bits are the soft keys down the side of the screen. The rest are
the two rotary knobs, which are not axes: each detent of rotation arrives as a
momentary press of a direction bit, so a knob is read by counting rising edges.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time

import hid

VID, PID = 0x06A3, 0xA2AE

# bit index in the 12-bit report -> what that control is called.
# Confirmed against the hardware: the six soft keys are bits 0-5 in order, the
# left knob is 6/7 and the right knob is 10/11. Bits 8 and 9 never fired on this
# panel. Which direction of each knob is which is a guess by symmetry with the
# left one -- "fipx.py calibrate" settles it.
DEFAULT_MAPPING = {
    0: "s1", 1: "s2", 2: "s3", 3: "s4", 4: "s5", 5: "s6",
    6: "left_cw", 7: "left_ccw",
    10: "right_cw", 11: "right_ccw",
    8: "aux1", 9: "aux2",
}

CONFIG_DIR = os.path.expanduser("~/.config/fipx")
MAPPING_FILE = os.path.join(CONFIG_DIR, "buttons.json")


def load_mapping():
    """The calibrated mapping if there is one, otherwise the sane default."""
    try:
        with open(MAPPING_FILE) as fh:
            saved = json.load(fh)
        return {int(k): v for k, v in saved.items()}
    except (OSError, ValueError):
        return dict(DEFAULT_MAPPING)


def save_mapping(mapping):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(MAPPING_FILE, "w") as fh:
        json.dump({str(k): v for k, v in sorted(mapping.items())}, fh, indent=2)
    return MAPPING_FILE


def find_hid(serial: str | None = None):
    for info in hid.enumerate(VID, PID):
        if serial is None or info.get("serial_number") == serial:
            return info
    return None


class FipInput:
    """Rising-edge events from the panel's twelve button bits.

    Runs a reader thread and hands events out through a queue, so a slow
    render loop never misses a knob detent.
    """

    def __init__(self, serial: str | None = None, mapping=None, verbose=False):
        self.serial = serial
        self.mapping = mapping if mapping is not None else load_mapping()
        self.verbose = verbose
        self.events: queue.Queue = queue.Queue()
        self.raw_events: queue.Queue = queue.Queue()
        self._dev = None
        self._stop = threading.Event()
        self._thread = None
        self.state = 0
        self.available = False
        self.error = None

    def open(self):
        info = find_hid(self.serial)
        if info is None:
            raise RuntimeError("no FIP HID interface found")
        self._dev = hid.Device(path=info["path"])
        self._dev.nonblocking = True
        self.available = True
        return self

    def start(self):
        try:
            self.open()
        except Exception as exc:
            # The panel is still perfectly usable as a display without buttons,
            # so this is a warning rather than a fatal error.
            self.error = str(exc)
            self.available = False
            return self
        self._thread = threading.Thread(target=self._run, daemon=True, name="fip-hid")
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            try:
                data = self._dev.read(8, 100)
            except Exception as exc:
                self.error = str(exc)
                self.available = False
                time.sleep(1.0)
                if not self._reopen():
                    continue
                continue
            if not data:
                continue
            value = int.from_bytes(bytes(data[:2]), "little") & 0x0FFF
            changed = value ^ self.state
            self.state = value
            if not changed:
                continue
            self.raw_events.put((time.time(), value, changed))
            for bit in range(12):
                mask = 1 << bit
                if changed & mask and value & mask:          # rising edge only
                    name = self.mapping.get(bit, f"bit{bit}")
                    self.events.put(name)
                    if self.verbose:
                        print(f"[hid] bit {bit} -> {name}", flush=True)

    def _reopen(self):
        try:
            self.open()
            return True
        except Exception:
            return False

    def poll(self):
        """Every event since the last call, oldest first."""
        out = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                return out

    def close(self):
        self._stop.set()
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None
