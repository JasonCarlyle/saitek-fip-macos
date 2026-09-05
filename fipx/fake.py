"""Stand-in panels, so six-panel behaviour can be exercised without six panels.

Nobody testing this owns six FIPs, and the parts of the driver that only matter
with several -- the supervisor, per-panel failure and retry, hotplug, the render
and USB budget -- are exactly the parts a single panel cannot exercise. So
`fipx.py run --fake 6` runs the whole app against fake displays that cost the
same time a real one does.

It is honest about what it is not: nothing here speaks USB, so it proves the
scheduling and the supervision, not the protocol. The real device is the only
thing that proves the protocol.
"""

from __future__ import annotations

import os
import threading
import time

from .device import FRAME_BYTES, HEIGHT, LEDS, WIDTH
from .panel import Panel

WRITE_SECONDS = 0.022     # what a 230400-byte bulk write actually costs
FAIL_NEVER = 0.0


class FakeDevice:
    """Quacks like FipDevice, costs like FipDevice, talks to nothing."""

    def __init__(self, serial=None, verbose=False, reset=True, index=None,
                 fail_rate=FAIL_NEVER, dump_dir=None):
        self.serial = serial
        self.index = index
        self.verbose = verbose
        self.fail_rate = fail_rate
        self.dump_dir = dump_dir
        self.frames = 0
        self.recoveries = 0
        self.ack_losses = 0
        self.leds = {name: 0 for name in LEDS}
        self.last_frame = None
        self._lock = threading.Lock()

    @property
    def label(self):
        return self.serial or f"#{self.index}"

    def open(self):
        time.sleep(0.05)
        return self

    def send_raw(self, payload: bytes):
        if len(payload) != FRAME_BYTES:
            raise ValueError(f"frame must be {FRAME_BYTES} bytes, got {len(payload)}")
        time.sleep(WRITE_SECONDS)          # the point of the exercise
        if self.fail_rate:
            import random
            if random.random() < self.fail_rate:
                raise RuntimeError("simulated USB failure")
        with self._lock:
            self.last_frame = payload
        self.frames += 1

    def set_led(self, led, on):
        number = LEDS.get(led, led) if isinstance(led, str) else led
        for name, num in LEDS.items():
            if num == number:
                self.leds[name] = 1 if on else 0
        return True

    def set_leds(self, states):
        for name, want in states.items():
            self.set_led(name, want)
        return len(states)

    def all_leds(self, on):
        return self.set_leds({name: on for name in LEDS})

    def deinit(self):
        return None

    def close(self, deinit=True):
        """Leave the last frame on disk, since there is no screen to look at."""
        if self.dump_dir and self.last_frame:
            try:
                from PIL import Image
                img = Image.frombytes("RGB", (WIDTH, HEIGHT), self.last_frame, "raw", "BGR")
                img = img.transpose(Image.FLIP_TOP_BOTTOM)
                path = os.path.join(self.dump_dir, f"fake_{self.label.strip('#')}.png")
                img.save(path)
                print(f"  wrote {path}", flush=True)
            except Exception as exc:
                print(f"  could not write dump: {exc}", flush=True)


class FakeInput:
    """A keypad nobody is pressing."""

    available = False
    error = "fake panel has no keypad"

    def __init__(self, *a, **kw):
        pass

    def start(self):
        return self

    def poll(self):
        return []

    def close(self):
        pass


class FakePanel(Panel):
    """A Panel whose hardware is imaginary. Everything above it is the real code."""

    dump_dir = None
    fail_rate = FAIL_NEVER

    def _open(self):
        self.state = "opening"
        self.device = FakeDevice(verbose=self.verbose, fail_rate=self.fail_rate,
                                 dump_dir=self.dump_dir, **self.selector).open()
        self.buttons = FakeInput().start()
        from .panel import FrameWriter
        self.writer = FrameWriter(self.device, name=f"fake-usb-{self.key}")
        self.writer.start()
        self.light_current_page()
