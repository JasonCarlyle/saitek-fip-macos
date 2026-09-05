"""Wiring: one X-Plane feed in, N panels out, each one independent.

One process drives every FIP attached. The sim data is global -- there is one
UDP feed and every panel snapshots from it -- but everything else is per panel:
its own instrument, its own keys and knobs, its own threads, its own failures.
Unplug one of six mid-flight and the other five never notice.

Panels are identified by USB serial number, which is what survives the reset on
open and lets a screen be paired with the right keypad. A panel that reports no
serial, or one that duplicates another's, falls back to position in bus order --
see discover().

What limits the number of panels is USB bandwidth, not Python. A frame is
230400 bytes, so a panel at 25 fps is 5.8 MB/s and six of them are 34.6 MB/s,
which is most of what one USB 2.0 high-speed bus will carry in practice.
Rendering is not the constraint: measured on an M3 Pro, six panels rendering
concurrently sustain 82 fps each, because PIL releases the GIL. If six panels
on one hub come up short, spread them across ports on different controllers or
drop --rate; --verbose prints the achieved rate per panel.
"""

from __future__ import annotations

import threading
import time

from .device import list_devices
from .panel import Panel

RESCAN_SECONDS = 3.0


def discover(serials=None, verbose=False):
    """Every panel attached, as {"serial": ...} or {"index": ...} selectors.

    Returns (selectors, warning). Serial numbers are strongly preferred: they
    are stable across the USB reset that open() does, so a panel keeps its
    identity and its instrument across a restart. Position in bus order is the
    fallback, and it is a worse one -- it does not survive a reset, so the app
    turns the reset off when it has to use it.
    """
    found = list_devices()
    if not found:
        return [], None
    serials_seen = [s for s, _bus, _addr in found]
    usable = (all(serials_seen)
              and len(set(serials_seen)) == len(serials_seen))

    if usable:
        wanted = [s for s in serials_seen if serials is None or s in serials]
        missing = sorted(set(serials or []) - set(serials_seen))
        warning = (f"no panel with serial {', '.join(missing)} is attached"
                   if missing else None)
        return [{"serial": s} for s in sorted(wanted)], warning

    if len(found) == 1:
        return [{"index": 0}], None
    return ([{"index": i} for i in range(len(found))],
            f"{len(found)} panels attached but their serial numbers are "
            f"{'missing' if not all(serials_seen) else 'not unique'} "
            f"({', '.join(repr(s) for s in serials_seen)}). Falling back to USB "
            f"bus order, which means the USB reset on open is disabled and a "
            f"panel may swap instruments with another if you replug it.")


def default_pages(count, start=None):
    """One instrument per panel, consecutive from `start`.

    Six panels get the six-pack, one each, which is the obvious thing to want.
    A single panel keeps the old default of starting on the attitude indicator.
    """
    if start is None:
        start = 1 if count == 1 else 0
    return [(start + i) % 6 for i in range(count)]


class App:
    """Supervises every panel, and rescans so hotplug works."""

    def __init__(self, feed, selectors, rate=25.0, pages=None, verbose=False,
                 seconds=0.0, leds=False, reset=True, rescan=RESCAN_SECONDS,
                 panel_factory=None):
        self.feed = feed
        self.rate = rate
        self.verbose = verbose
        self.seconds = seconds
        self.use_leds = leds
        self.reset = reset
        # Rescanning asks the USB bus what is attached, which says nothing about
        # panels that are not real ones, so a custom factory turns hotplug off.
        self.rescan = 0.0 if panel_factory else rescan
        self.panel_factory = panel_factory or Panel
        self.panels: dict[str, Panel] = {}
        self.stop_flag = threading.Event()
        self.ever_ran = False       # did any panel ever come up? drives the exit code
        self._pages = list(pages) if pages else default_pages(len(selectors))
        for i, selector in enumerate(selectors):
            self._add(selector, self._pages[i % len(self._pages)] if self._pages else 0)

    # -- panel bookkeeping -------------------------------------------------
    def _key(self, selector):
        return selector.get("serial") or f"#{selector.get('index')}"

    def _add(self, selector, page):
        key = self._key(selector)
        if key in self.panels:
            return None
        panel = self.panel_factory(selector, self.feed, key=key, rate=self.rate,
                                   start_page=page, verbose=self.verbose,
                                   leds=self.use_leds, reset=self.reset)
        self.panels[key] = panel
        return panel

    def _next_page(self):
        """An instrument no running panel is already showing, if there is one."""
        taken = {p.page for p in self.panels.values()}
        for page in range(6):
            if page not in taken:
                return page
        return 0

    def _rescan(self):
        """Pick up panels plugged in after start-up.

        Only meaningful when panels have serial numbers: without them a new
        device has no identity to match, so hotplug is switched off rather than
        risking two Panels fighting over one screen.
        """
        selectors, _warning = discover()
        if any("index" in s for s in selectors):
            return
        attached = {self._key(s) for s in selectors}
        for selector in selectors:
            key = self._key(selector)
            if key not in self.panels:
                page = self._next_page()
                panel = self._add(selector, page)
                if panel:
                    print(f"+ panel {key} attached — {panel.pages[page].name.lower()}",
                          flush=True)
                    panel.start()
        for key, panel in list(self.panels.items()):
            if key not in attached and panel.state == "retrying":
                # Both halves of that condition matter. Absent from the bus is
                # not enough on its own: open() resets the device, and a panel
                # spends about a second re-enumerating and genuinely missing
                # from list_devices() every single time it starts. Waiting for
                # the panel to also have given up is what tells a real unplug
                # apart from that window. A panel that is attached but wedged
                # keeps its slot and keeps retrying, which is what you want.
                print(f"- panel {key} removed", flush=True)
                panel.stop()
                del self.panels[key]

    # -- main loop ---------------------------------------------------------
    def run(self):
        for panel in self.panels.values():
            panel.start()
        deadline = time.time() + self.seconds if self.seconds else None
        t_report = time.time()
        t_rescan = time.time()
        try:
            while not self.stop_flag.is_set():
                time.sleep(0.25)
                now = time.time()
                if deadline and now >= deadline:
                    break
                if self.rescan and now - t_rescan >= self.rescan:
                    t_rescan = now
                    try:
                        self._rescan()
                    except Exception as exc:
                        if self.verbose:
                            print(f"[app] rescan failed: {exc}", flush=True)
                if any(p.ever_ran for p in self.panels.values()):
                    self.ever_ran = True
                if self.verbose and now - t_report >= 5.0:
                    t_report = now
                    data = self.feed.snapshot()
                    print(f"[app] sim {'live' if data['connected'] else 'NO DATA'}, "
                          f"{len(self.panels)} panel(s)", flush=True)
                    for panel in self.panels.values():
                        print(f"      {panel.status_line()}", flush=True)
                if not self.panels:
                    continue
        except KeyboardInterrupt:
            pass
        finally:
            for panel in self.panels.values():
                panel.stop()
            for panel in self.panels.values():
                panel.join(timeout=5.0)

    def summary(self):
        return [panel.status_line() for panel in self.panels.values()]
