#!/usr/bin/env python3
"""A stand-in for X-Plane, so the UDP layer can be tested without the sim.

Speaks enough of the protocol to be indistinguishable from the real thing:
accepts RREF subscriptions, streams RREF value packets back to whoever asked,
and applies DREF writes. Flies a lazy circling descent so every needle moves.
"""
import math, socket, struct, sys, time

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 49000
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
sock.bind(("127.0.0.1", PORT))
sock.settimeout(0.02)
print(f"fake X-Plane listening on 127.0.0.1:{PORT}")

subs = {}          # (client, index) -> (dataref, freq)
overrides = {}
t0 = time.time()

def value_for(dref):
    t = time.time() - t0
    if dref in overrides:
        return overrides[dref]
    table = {
        "sim/cockpit2/gauges/indicators/airspeed_kts_pilot": 95 + 25 * math.sin(t / 9),
        "sim/cockpit2/gauges/indicators/altitude_ft_pilot": 3200 + 900 * math.sin(t / 13),
        "sim/cockpit2/gauges/indicators/vvi_fpm_pilot": 850 * math.cos(t / 13),
        "sim/cockpit2/gauges/actuators/barometer_setting_in_hg_pilot": 29.92,
        "sim/cockpit2/gauges/indicators/slip_deg": 2.5 * math.sin(t / 5),
        "sim/cockpit2/gauges/indicators/pitch_vacuum_deg_pilot": 6 * math.sin(t / 13),
        "sim/cockpit2/gauges/indicators/roll_vacuum_deg_pilot": 20 * math.sin(t / 11),
        "sim/cockpit2/gauges/indicators/heading_vacuum_deg_mag_pilot": (t * 6) % 360,
        "sim/cockpit2/autopilot/heading_dial_deg_mag_pilot": 210.0,
        "sim/flightmodel/position/groundspeed": 55.0,
        "sim/aircraft/view/acf_Vso": 41.0, "sim/aircraft/view/acf_Vs": 48.0,
        "sim/aircraft/view/acf_Vfe": 85.0, "sim/aircraft/view/acf_Vno": 129.0,
        "sim/aircraft/view/acf_Vne": 163.0,
    }
    if dref in table:
        return float(table[dref])
    for name, text in (("acf_ICAO", "C172"), ("acf_descrip", "Cessna 172SP Skyhawk")):
        if name in dref and "[" in dref:
            i = int(dref.split("[")[1].rstrip("]"))
            return float(ord(text[i])) if i < len(text) else 0.0
    return 0.0

last_send = 0.0
while True:
    try:
        data, client = sock.recvfrom(2048)
    except socket.timeout:
        data = None
    if data:
        if data[:4] == b"RREF":
            freq, idx = struct.unpack("<ii", data[5:13])
            dref = data[13:].split(b"\x00")[0].decode()
            if freq == 0:
                subs.pop((client, idx), None)
            else:
                subs[(client, idx)] = dref
        elif data[:4] == b"DREF":
            value = struct.unpack("<f", data[5:9])[0]
            dref = data[9:].split(b"\x00")[0].decode()
            overrides[dref] = value
            print(f"  DREF {dref} = {value:.4f}")
    now = time.time()
    if now - last_send >= 0.04 and subs:
        last_send = now
        by_client = {}
        for (client, idx), dref in subs.items():
            by_client.setdefault(client, []).append((idx, value_for(dref)))
        for client, items in by_client.items():
            for i in range(0, len(items), 30):          # X-Plane splits big feeds
                body = b"".join(struct.pack("<if", idx, val) for idx, val in items[i:i+30])
                sock.sendto(b"RREF," + body, client)
