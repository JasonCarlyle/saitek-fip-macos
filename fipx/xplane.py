"""X-Plane 12 over UDP.

X-Plane publishes any dataref on request over UDP ("RREF") and accepts writes
the same way ("DREF"), so nothing here needs an X-Plane plugin or the SDK --
just Settings > Network > "Accept incoming connections" on the sim side.

The dataref list and the gyro/aircraft logic are carried over from the author's
earlier xplane-sixpack browser panel, which had already worked out the two
awkward parts: an aircraft only models the gyro sources it actually has (the
rest read a flat zero forever), and a string dataref has to be read a byte at
a time because the feed carries nothing but floats.
"""

from __future__ import annotations

import math
import socket
import struct
import threading
import time

# (key, dataref, requested updates per second); list index == RREF index.
BASE_DATAREFS = [
    ("ias",         "sim/cockpit2/gauges/indicators/airspeed_kts_pilot",           25),
    ("alt",         "sim/cockpit2/gauges/indicators/altitude_ft_pilot",            25),
    ("vsi",         "sim/cockpit2/gauges/indicators/vvi_fpm_pilot",                25),
    ("baro",        "sim/cockpit2/gauges/actuators/barometer_setting_in_hg_pilot",  5),
    ("slip",        "sim/cockpit2/gauges/indicators/slip_deg",                     25),
    ("turn_defl",   "sim/cockpit2/gauges/indicators/turn_rate_roll_deg_pilot",     25),
    ("pitch_vac",   "sim/cockpit2/gauges/indicators/pitch_vacuum_deg_pilot",       25),
    ("roll_vac",    "sim/cockpit2/gauges/indicators/roll_vacuum_deg_pilot",        25),
    ("hdg_vac",     "sim/cockpit2/gauges/indicators/heading_vacuum_deg_mag_pilot", 25),
    ("pitch_elec",  "sim/cockpit2/gauges/indicators/pitch_electric_deg_pilot",     25),
    ("roll_elec",   "sim/cockpit2/gauges/indicators/roll_electric_deg_pilot",      25),
    ("hdg_elec",    "sim/cockpit2/gauges/indicators/heading_electric_deg_mag_pilot", 25),
    ("pitch_ahars", "sim/cockpit2/gauges/indicators/pitch_AHARS_deg_pilot",        25),
    ("roll_ahars",  "sim/cockpit2/gauges/indicators/roll_AHARS_deg_pilot",         25),
    ("hdg_ahars",   "sim/cockpit2/gauges/indicators/heading_AHARS_deg_mag_pilot",  25),
    ("pitch_true",  "sim/flightmodel/position/true_theta",                         25),
    ("roll_true",   "sim/flightmodel/position/true_phi",                           25),
    ("hdg_true",    "sim/flightmodel/position/mag_psi",                            25),
    ("hdg_bug",     "sim/cockpit2/autopilot/heading_dial_deg_mag_pilot",            5),
    ("gs_ms",       "sim/flightmodel/position/groundspeed",                         5),
    ("paused",      "sim/time/paused",                                             2),
    ("vso",   "sim/aircraft/view/acf_Vso",      1),
    ("vs1",   "sim/aircraft/view/acf_Vs",       1),
    ("vfe",   "sim/aircraft/view/acf_Vfe",      1),
    ("vno",   "sim/aircraft/view/acf_Vno",      1),
    ("vne",   "sim/aircraft/view/acf_Vne",      1),
]

STRING_REFS = [
    ("icao", "sim/aircraft/view/acf_ICAO", 8),
    ("name", "sim/aircraft/view/acf_descrip", 24),
]

DATAREFS = list(BASE_DATAREFS)
for _key, _dref, _n in STRING_REFS:
    for _i in range(_n):
        DATAREFS.append((f"{_key}#{_i}", f"{_dref}[{_i}]", 1))

# Writable datarefs the knobs drive.
WRITABLE = {
    "baro": "sim/cockpit2/gauges/actuators/barometer_setting_in_hg_pilot",
    "hdg_bug": "sim/cockpit2/autopilot/heading_dial_deg_mag_pilot",
}

GYRO_SOURCES = [
    ("vacuum",   "pitch_vac",   "roll_vac",   "hdg_vac"),
    ("electric", "pitch_elec",  "roll_elec",  "hdg_elec"),
    ("ahars",    "pitch_ahars", "roll_ahars", "hdg_ahars"),
]

RESUBSCRIBE_AFTER = 3.0
STALE_AFTER = 1.5


def wrap180(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0


class XPlaneFeed:
    def __init__(self, host="127.0.0.1", port=49000):
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(0.5)
        self.lock = threading.Lock()
        self.raw = {key: 0.0 for key, _, _ in DATAREFS}
        self.last_rx = 0.0
        self.packets = 0
        self.alive = set()
        self.stop_flag = threading.Event()
        self._turn_rate = 0.0
        self._last_hdg = None
        self._last_hdg_t = None
        self.derived = ("model", 0.0, 0.0, 0.0, 0.0)
        self.acf = {"sig": "", "icao": "", "name": "",
                    "vso": 0.0, "vs1": 0.0, "vfe": 0.0, "vno": 0.0, "vne": 0.0}
        self._acf_pending = ""
        self._acf_since = 0.0

    # -- subscription ------------------------------------------------------
    def subscribe(self, freq_override=None):
        for i, (_key, dref, freq) in enumerate(DATAREFS):
            f = freq if freq_override is None else freq_override
            pkt = struct.pack("<4sxii400s", b"RREF", f, i, dref.encode("utf-8"))
            try:
                self.sock.sendto(pkt, self.addr)
            except OSError:
                return
            time.sleep(0.001)      # X-Plane drops the tail of a burst

    def write_dref(self, key: str, value: float):
        """Set a dataref in the sim -- this is how the knobs do anything."""
        dref = WRITABLE.get(key, key)
        pkt = struct.pack("<4sxf500s", b"DREF", float(value), dref.encode("utf-8"))
        try:
            self.sock.sendto(pkt, self.addr)
        except OSError:
            pass
        with self.lock:            # echo locally so the gauge responds at once
            if key in self.raw:
                self.raw[key] = float(value)

    # -- receive loop ------------------------------------------------------
    def run(self):
        self.subscribe()
        last_sub = time.time()
        while not self.stop_flag.is_set():
            now = time.time()
            if now - self.last_rx > RESUBSCRIBE_AFTER and now - last_sub > RESUBSCRIBE_AFTER:
                self.subscribe()               # sim not up yet, or it restarted
                last_sub = now
            try:
                data, _src = self.sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                time.sleep(0.25)
                continue
            if len(data) < 13 or data[0:4] != b"RREF":
                continue
            self.last_rx = time.time()
            self.packets += 1
            with self.lock:
                for off in range(5, len(data) - 7, 8):
                    idx, value = struct.unpack("<if", data[off:off + 8])
                    if 0 <= idx < len(DATAREFS):
                        self.raw[DATAREFS[idx][0]] = value
                self._update_aircraft(self.raw)
                src, pitch, roll, hdg = self._pick_gyro_source(self.raw)
                self.derived = (src, pitch, roll, hdg,
                                self._update_turn_rate(hdg, self.last_rx))

    def start(self):
        threading.Thread(target=self.run, daemon=True, name="xplane-udp").start()
        return self

    def close(self):
        self.stop_flag.set()
        try:
            self.subscribe(freq_override=0)
        except OSError:
            pass
        self.sock.close()

    # -- derived state -----------------------------------------------------
    @staticmethod
    def _text(raw, key, n):
        out = []
        for i in range(n):
            c = int(round(raw.get(f"{key}#{i}", 0.0)))
            if c <= 0 or c > 126:
                break
            if c >= 32:
                out.append(chr(c))
        return "".join(out).strip()

    def _update_aircraft(self, raw):
        speeds = {k: round(float(raw[k]), 1) for k in ("vso", "vs1", "vfe", "vno", "vne")}
        icao = self._text(raw, "icao", 8)
        name = self._text(raw, "name", 24)
        sig = "|".join([icao, name] + [f"{speeds[k]}" for k in sorted(speeds)])
        if sig == self.acf["sig"]:
            return
        now = time.time()
        if sig != self._acf_pending:           # a name arrives a byte at a time;
            self._acf_pending, self._acf_since = sig, now   # wait for it to settle
            return
        if now - self._acf_since < 0.8:
            return
        acf = dict(speeds)
        acf.update({"sig": sig, "icao": icao, "name": name})
        self.acf = acf
        self.alive.clear()                     # different aircraft, different gyros
        if icao or name or speeds["vne"] > 0:
            print(f"aircraft: {name or icao or 'unknown'} — "
                  f"Vso {speeds['vso']:.0f}, Vfe {speeds['vfe']:.0f}, "
                  f"Vno {speeds['vno']:.0f}, Vne {speeds['vne']:.0f}", flush=True)

    def _pick_gyro_source(self, raw):
        for name, p, r, h in GYRO_SOURCES:
            if name not in self.alive and (raw[p] or raw[r] or raw[h]):
                self.alive.add(name)
        for name, p, r, h in GYRO_SOURCES:
            if name in self.alive:
                return name, raw[p], raw[r], raw[h] % 360.0
        return "model", raw["pitch_true"], raw["roll_true"], raw["hdg_true"] % 360.0

    def _update_turn_rate(self, hdg, now):
        if self._last_hdg is None:
            self._last_hdg, self._last_hdg_t = hdg, now
            return 0.0
        dt = now - self._last_hdg_t
        if dt < 0.02:
            return self._turn_rate
        rate = wrap180(hdg - self._last_hdg) / dt
        self._last_hdg, self._last_hdg_t = hdg, now
        if abs(rate) > 60.0:                   # a reposition, not a turn
            rate = self._turn_rate
        alpha = 1.0 - math.exp(-dt / 0.35)
        self._turn_rate += alpha * (rate - self._turn_rate)
        return self._turn_rate

    def snapshot(self):
        now = time.time()
        with self.lock:
            raw = dict(self.raw)
            src, pitch, roll, hdg, rate = self.derived
            acf = dict(self.acf)
        age = now - self.last_rx if self.last_rx else 999.0
        return {
            "connected": age < STALE_AFTER,
            "age": age,
            "src": src,
            "ias": raw["ias"],
            "alt_ft": raw["alt"],
            "vsi_fpm": raw["vsi"],
            "baro_inhg": raw["baro"] or 29.92,
            "slip_deg": raw["slip"],
            "turn_rate_dps": rate,
            "pitch_deg": pitch,
            "roll_deg": roll,
            "hdg_deg": hdg,
            "hdg_bug_deg": raw["hdg_bug"] % 360.0,
            "gs_kt": raw["gs_ms"] * 1.94384,
            "paused": bool(raw["paused"]),
            "acf": acf,
        }


class DemoFeed:
    """Synthetic flight, so the panel can be checked without starting the sim."""

    def __init__(self, *_a, **_k):
        self.t0 = time.time()
        self._baro = 29.92
        self._bug = 120.0

    def start(self):
        return self

    def close(self):
        pass

    def write_dref(self, key, value):
        if key == "baro":
            self._baro = value
        elif key == "hdg_bug":
            self._bug = value

    def snapshot(self):
        t = time.time() - self.t0
        turn = 3.0 * math.sin(t / 25.0)
        hdg = (95.0 + 75.0 * (1 - math.cos(t / 25.0))) % 360.0
        return {
            "connected": True, "age": 0.0, "src": "demo",
            "ias": 108.0 + 14.0 * math.sin(t / 11.0),
            "alt_ft": 4250.0 + 400.0 * math.sin(t / 17.0),
            "vsi_fpm": 700.0 * math.sin(t / 17.0),
            "baro_inhg": self._baro,
            "slip_deg": 1.6 * math.sin(t / 6.0),
            "turn_rate_dps": turn,
            "pitch_deg": 4.0 * math.sin(t / 17.0),
            "roll_deg": 18.0 * math.sin(t / 25.0),
            "hdg_deg": hdg,
            "hdg_bug_deg": self._bug % 360.0,
            "gs_kt": 112.0, "paused": False,
            "acf": {"sig": "demo", "icao": "C172", "name": "Cessna 172 (demo)",
                    "vso": 41.0, "vs1": 48.0, "vfe": 85.0, "vno": 129.0, "vne": 163.0},
        }
