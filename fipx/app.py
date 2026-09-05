"""Wiring: X-Plane's numbers in, a picture on the panel, buttons and knobs back out.

Rendering and USB run on separate threads. A frame takes ~5-20 ms to draw and
~22 ms to push, so overlapping them roughly doubles the achievable rate; the
writer always sends the newest frame and silently drops anything the renderer
managed to produce while it was busy, which is the right trade for a gauge.
"""

from __future__ import annotations

import threading
import time

from . import gauges as G
from .device import FipDevice, to_frame
from .input import FipInput
from .xplane import DemoFeed, XPlaneFeed

TOAST_SECONDS = 1.4
BARO_STEP = 0.01          # inHg per knob detent
BUG_STEP = 1.0            # degrees per knob detent
BUG_FAST_STEP = 10.0


class FrameWriter(threading.Thread):
    """Pushes the most recent frame to the panel, for ever."""

    def __init__(self, device: FipDevice):
        super().__init__(daemon=True, name="fip-usb")
        self.device = device
        self._frame = None
        self._new = threading.Event()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.sent = 0
        self.errors = 0
        self.last_error = None

    def submit(self, payload: bytes):
        with self._lock:
            self._frame = payload
        self._new.set()

    def run(self):
        while not self._stop.is_set():
            if not self._new.wait(0.25):
                continue
            self._new.clear()
            with self._lock:
                frame = self._frame
            if frame is None:
                continue
            try:
                self.device.send_raw(frame)
                self.sent += 1
            except Exception as exc:
                if self._stop.is_set():
                    break                     # shutting down; the device is going away
                self.errors += 1
                self.last_error = exc
                print(f"!! frame send failed: {exc}", flush=True)
                time.sleep(0.5)

    def stop(self):
        self._stop.set()
        self._new.set()


class App:
    def __init__(self, feed, device, buttons, rate=25.0, start_page=1,
                 verbose=False, seconds=0.0):
        self.feed = feed
        self.device = device
        self.buttons = buttons
        self.rate = rate
        self.verbose = verbose
        self.pages = [cls() for cls in G.ALL_GAUGES]
        self.page = max(0, min(len(self.pages) - 1, start_page))
        self.toast_until = time.time() + TOAST_SECONDS
        self.toast_text = self.pages[self.page].name
        self.seconds = seconds
        self.stop_flag = threading.Event()

    # -- controls ----------------------------------------------------------
    def select(self, index):
        index %= len(self.pages)
        if index != self.page:
            self.page = index
            self.toast_text = self.pages[index].name
            self.toast_until = time.time() + TOAST_SECONDS

    def adjust(self, direction):
        """The right knob tunes whatever the shown instrument is about."""
        gauge = self.pages[self.page]
        data = self.feed.snapshot()
        if gauge.key == "alt":
            value = round(data["baro_inhg"] + direction * BARO_STEP, 2)
            self.feed.write_dref("baro", max(27.5, min(31.5, value)))
            self.toast_text = f"BARO {max(27.5, min(31.5, value)):.2f}"
        elif gauge.key in ("hi", "ai"):
            value = (data["hdg_bug_deg"] + direction * BUG_STEP) % 360.0
            self.feed.write_dref("hdg_bug", value)
            self.toast_text = f"BUG {value:03.0f}"
        else:
            self.select(self.page + direction)
            return
        self.toast_until = time.time() + TOAST_SECONDS

    def handle(self, event):
        if event in ("s1", "s2", "s3", "s4", "s5", "s6"):
            self.select(int(event[1]) - 1)
        elif event == "left_cw":
            self.select(self.page + 1)
        elif event == "left_ccw":
            self.select(self.page - 1)
        elif event == "right_cw":
            self.adjust(+1)
        elif event == "right_ccw":
            self.adjust(-1)
        elif self.verbose:
            print(f"[app] unhandled control: {event}", flush=True)

    # -- main loop ---------------------------------------------------------
    def run(self):
        writer = FrameWriter(self.device)
        writer.start()
        period = 1.0 / self.rate
        frames = 0
        t_report = time.time()
        deadline = time.time() + self.seconds if self.seconds else None
        try:
            while not self.stop_flag.is_set():
                t0 = time.time()
                if deadline and t0 >= deadline:
                    break
                for event in self.buttons.poll():
                    self.handle(event)

                data = self.feed.snapshot()
                img = self.pages[self.page].render(data)
                if not data["connected"]:
                    G.overlay_message(img, "NO DATA FROM X-PLANE",
                                      "start the sim, or check Network settings")
                elif data["paused"]:
                    G.overlay_message(img, "PAUSED")
                if time.time() < self.toast_until:
                    G.toast(img, self.toast_text)
                writer.submit(to_frame(img))

                frames += 1
                if self.verbose and time.time() - t_report >= 5.0:
                    dt = time.time() - t_report
                    print(f"[app] sim {'live' if data['connected'] else 'NO DATA'}, "
                          f"render {frames/dt:4.1f} fps, sent {writer.sent}, "
                          f"usb errors {writer.errors}, recoveries {self.device.recoveries}, "
                          f"lost acks {self.device.ack_losses}", flush=True)
                    frames, t_report = 0, time.time()

                slack = period - (time.time() - t0)
                if slack > 0:
                    time.sleep(slack)
        except KeyboardInterrupt:
            pass
        finally:
            writer.stop()
            writer.join(timeout=2.0)          # let an in-flight frame finish first
