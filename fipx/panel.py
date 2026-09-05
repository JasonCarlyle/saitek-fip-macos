"""One panel: its screen, its keys, its own idea of which instrument is up.

A Panel is self-contained and self-supervising. It owns a display, a keypad and
two threads, and it is the unit that fails: a panel that is unplugged, wedges or
throws stops and retries on its own without touching the others. That is the
whole reason multi-panel support is not just a loop -- with six of these on one
process, any design where one sick panel can stall the rest is wrong.

Two threads per panel, because the work splits cleanly and both halves release
the GIL: the Panel thread renders (4-15 ms) and a FrameWriter pushes (~22 ms).
Measured, six panels rendering concurrently reach 82 fps each, so the render
side is not the ceiling; USB bandwidth is. See the note in app.py.
"""

from __future__ import annotations

import threading
import time

from . import gauges as G
from .device import FipDevice, FipError, to_frame
from .input import FipInput

TOAST_SECONDS = 1.4
BARO_STEP = 0.01          # inHg per knob detent
BUG_STEP = 1.0            # degrees per knob detent

# A panel that keeps failing backs off rather than spinning on a dead device.
RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0, 15.0)
WRITER_DEATHS = 6         # consecutive send failures before the panel restarts


class FrameWriter(threading.Thread):
    """Pushes the most recent frame to one panel, for ever."""

    def __init__(self, device: FipDevice, name="fip-usb"):
        super().__init__(daemon=True, name=name)
        self.device = device
        self._frame = None
        self._new = threading.Event()
        self._lock = threading.Lock()
        # NOT self._stop: threading.Thread has an internal _stop() method that
        # join() calls on Python 3.9-3.13, and an Event here shadows it, so
        # join() dies with "'Event' object is not callable". CPython 3.14
        # removed that method, which is the only reason this ever worked on the
        # machine it was written on. Found by running the driver on Debian 12.
        self._stopping = threading.Event()
        self._leds = None
        self.sent = 0
        self.errors = 0
        self.last_error = None
        self.failed = threading.Event()

    def submit(self, payload: bytes):
        with self._lock:
            self._frame = payload
        self._new.set()

    def set_leds(self, states):
        """Queue a {name: bool} lamp change for this thread to send.

        The device is single-threaded, so the render thread must not poke it
        directly while a 230 KB frame is going out. Leave the request here and
        the writer applies it between frames.
        """
        with self._lock:
            self._leds = dict(states)
        self._new.set()

    def run(self):
        consecutive = 0
        while not self._stopping.is_set():
            if not self._new.wait(0.25):
                continue
            self._new.clear()
            with self._lock:
                frame = self._frame
                leds, self._leds = self._leds, None
            if leds is not None:
                try:
                    self.device.set_leds(leds)
                except Exception as exc:
                    self.last_error = exc     # a lamp is never worth a stall
            if frame is None:
                continue
            try:
                self.device.send_raw(frame)
                self.sent += 1
                consecutive = 0
            except Exception as exc:
                if self._stopping.is_set():
                    break                     # shutting down; the device is going away
                self.errors += 1
                self.last_error = exc
                consecutive += 1
                if consecutive >= WRITER_DEATHS:
                    # Not a glitch: the panel has gone. Say so and let the Panel
                    # tear down and retry rather than printing for ever.
                    self.failed.set()
                    break
                time.sleep(0.5)

    def stop(self):
        self._stopping.set()
        self._new.set()


class Panel:
    """One FIP, supervised.

    `selector` is what identifies this panel to the USB and HID layers:
    {"serial": "..."} normally, or {"index": n} for panels that report no
    usable serial number.
    """

    def __init__(self, selector, feed, key=None, rate=25.0, start_page=1,
                 verbose=False, leds=False, reset=True):
        self.selector = dict(selector)
        self.feed = feed
        self.key = key or selector.get("serial") or f"#{selector.get('index')}"
        self.rate = rate
        self.verbose = verbose
        self.use_leds = leds
        self.reset = reset

        self.pages = [cls() for cls in G.ALL_GAUGES]
        self.page = max(0, min(len(self.pages) - 1, start_page))
        self.toast_until = time.time() + TOAST_SECONDS
        self.toast_text = self.pages[self.page].name

        self.device = None
        self.buttons = None
        self.writer = None
        self.thread = None
        self.state = "new"         # new | opening | running | retrying | stopped
        self.error = None
        self.ever_ran = False
        self._reported = None          # last message printed, to suppress repeats
        self._announced_failure = False  # did we tell the user this panel is down?
        self.frames = 0
        self.restarts = 0
        self.fps = 0.0
        self._t_start = None
        # Totals survive a restart: the writer and device that hold the live
        # counters are thrown away and rebuilt each time the panel recovers,
        # so a panel that has died twice would otherwise report a clean sheet.
        self.sent = 0
        self.usb_errors = 0
        self.recoveries = 0
        self.ack_losses = 0
        self._stopping = threading.Event()

    def __repr__(self):
        return f"<Panel {self.key} {self.state}>"

    # -- lifecycle ---------------------------------------------------------
    def start(self):
        self.thread = threading.Thread(target=self._supervise, daemon=True,
                                       name=f"fip-panel-{self.key}")
        self.thread.start()
        return self

    def stop(self):
        self._stopping.set()

    def join(self, timeout=None):
        if self.thread:
            self.thread.join(timeout)

    def _log(self, msg):
        if self.verbose:
            print(f"[{self.key}] {msg}", flush=True)

    def _report(self, msg, bad=False):
        """Tell the user once, and not again until something changes.

        A panel retries for ever, which is right -- plug it back in and it comes
        back -- but it must not turn a stuck panel into a scrolling wall of the
        same line. So the first failure is always printed, a *different* failure
        is printed, and recovery is printed; a repeat of the last message is not.
        """
        if msg == self._reported:
            return
        self._reported = msg
        who = f"panel {self.key}" if self.key else "panel"
        print(f"{'!! ' if bad else ''}{who}: {msg}", flush=True)

    def _supervise(self):
        """Open, run, and on any failure tear down and try again."""
        attempt = 0
        self._t_start = time.time()
        while not self._stopping.is_set():
            try:
                self._open()
                attempt = 0
                self.state = "running"
                if self._announced_failure:
                    # We told the user this panel was down, so tell them it is
                    # not any more -- including when the very first open failed
                    # and a later one worked, the ordinary "you plugged it in"
                    # case. A keypad warning is not a failure and must not arm
                    # this, or a display-only panel announces a recovery from
                    # nothing every time it starts.
                    self._report("back")
                    self._announced_failure = False
                self.ever_ran = True
                self._loop()
            except FipError as exc:
                self.error = exc
                self._report(str(exc), bad=True)
                self._announced_failure = True
            except Exception as exc:
                self.error = exc
                self._report(f"{exc}", bad=True)
                self._announced_failure = True
            finally:
                self._teardown()
            if self._stopping.is_set():
                break
            self.state = "retrying"
            self.restarts += 1
            delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
            attempt += 1
            self._log(f"retrying in {delay:g}s")
            if self._stopping.wait(delay):
                break
        self.state = "stopped"

    def _open(self):
        self.state = "opening"
        self.device = FipDevice(verbose=self.verbose, reset=self.reset,
                                **self.selector).open()
        # Buttons are optional: a panel with an unreadable keypad is still a
        # perfectly good screen, and with six panels one bad keypad must not
        # cost you the other five displays.
        self.buttons = FipInput(verbose=self.verbose, **self.selector).start()
        if not self.buttons.available:
            # Worth saying out loud, not just under --verbose: the panel will
            # look like it is working while none of its buttons do anything.
            self._report(f"keypad unavailable ({self.buttons.error}) — display only",
                         bad=True)
        self.writer = FrameWriter(self.device, name=f"fip-usb-{self.key}")
        self.writer.start()
        self.light_current_page()

    def _teardown(self):
        if self.writer:
            self.writer.stop()
            self.writer.join(timeout=2.0)     # let an in-flight frame finish first
            self.sent += self.writer.sent
            self.usb_errors += self.writer.errors
        if self.device:
            self.recoveries += self.device.recoveries
            self.ack_losses += self.device.ack_losses
        if self.buttons:
            self.buttons.close()
        if self.device:
            if self.use_leds:
                try:
                    self.device.all_leds(False)
                except Exception:
                    pass                      # the panel is going away regardless
            try:
                self.device.close()
            except Exception:
                pass
        self.device = self.buttons = self.writer = None

    # -- controls ----------------------------------------------------------
    def select(self, index):
        index %= len(self.pages)
        if index != self.page:
            self.page = index
            self.toast_text = self.pages[index].name
            self.toast_until = time.time() + TOAST_SECONDS
            self.light_current_page()

    def light_current_page(self):
        """Backlight the soft key for the instrument on screen, if asked to.

        Opt-in rather than automatic, but only because it is a matter of taste
        whether a lit key is wanted -- the command itself is confirmed on
        hardware. `fipx.py leds` exercises the lamps on their own.
        """
        if not (self.use_leds and self.writer):
            return
        self.writer.set_leds({f"s{i + 1}": (i == self.page) for i in range(6)})

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
        elif event in ("left_cw", "down"):
            # The UP/DOWN pair between the knobs steps through the instruments,
            # the same as the left knob. They went unused until a second
            # hardware session noticed they report at all.
            self.select(self.page + 1)
        elif event in ("left_ccw", "up"):
            self.select(self.page - 1)
        elif event == "right_cw":
            self.adjust(+1)
        elif event == "right_ccw":
            self.adjust(-1)
        elif self.verbose:
            print(f"[{self.key}] unhandled control: {event}", flush=True)

    # -- render loop -------------------------------------------------------
    def _loop(self):
        period = 1.0 / self.rate
        frames, t_report = 0, time.time()
        while not self._stopping.is_set() and not self.writer.failed.is_set():
            t0 = time.time()
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
            self.writer.submit(to_frame(img))

            frames += 1
            self.frames += 1
            if time.time() - t_report >= 5.0:
                self.fps = frames / (time.time() - t_report)
                frames, t_report = 0, time.time()

            slack = period - (time.time() - t0)
            if slack > 0:
                time.sleep(slack)
        if self.writer.failed.is_set():
            raise RuntimeError(f"display stopped responding: {self.writer.last_error}")

    # -- reporting ---------------------------------------------------------
    def status_line(self):
        """One line per panel, totals carried across restarts."""
        w, dev = self.writer, self.device
        sent = self.sent + (w.sent if w else 0)
        errors = self.usb_errors + (w.errors if w else 0)
        recoveries = self.recoveries + (dev.recoveries if dev else 0)
        losses = self.ack_losses + (dev.ack_losses if dev else 0)
        # The supervisor thread sets "stopped" as its very last act, so on a
        # slow machine a shutdown summary can be printed while the thread is
        # still unwinding and report every panel as "running". A thread that is
        # no longer alive has stopped, whatever it last wrote down.
        state = self.state
        if self.thread is not None and not self.thread.is_alive() and state != "new":
            state = "stopped"

        # While running, the rolling five-second figure; once stopped, the
        # lifetime average -- otherwise a run shorter than one reporting
        # interval signs off with a misleading "0.0 fps".
        fps = self.fps
        if (state != "running" or not fps) and self._t_start:
            # Also covers the first report of a run: the rolling figure has no
            # window yet, and printing "0.0 fps" at a panel that is plainly
            # working reads as a fault.
            elapsed = time.time() - self._t_start
            fps = self.frames / elapsed if elapsed > 0 else 0.0
        bits = [f"{self.key:>16s}", f"{state:9s}",
                f"page {self.pages[self.page].key:4s}",
                f"{fps:4.1f} fps",
                f"sent {sent:6d}", f"usb err {errors:4d}",
                f"recov {recoveries:2d}", f"lost acks {losses:3d}"]
        if self.restarts:
            bits.append(f"restarts {self.restarts}")
        if state != "running" and self.error:
            bits.append(f"-- {self.error}")
        return "  ".join(bits)
