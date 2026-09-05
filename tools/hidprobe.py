#!/usr/bin/env python3
"""Listen to the FIP's HID interface (buttons + encoders) for a few seconds."""
import sys, time, hid
found = [d for d in hid.enumerate(0x06A3, 0xA2AE)]
for d in found:
    print(f"path={d['path']} iface={d.get('interface_number')} usage_page={d.get('usage_page'):#06x} "
          f"usage={d.get('usage'):#06x} product={d.get('product_string')!r}")
if not found:
    sys.exit("!! no HID interface exposed for the FIP")
h = hid.Device(path=found[0]['path'])
print("\nlistening 8s -- press buttons / turn knobs now")
h.nonblocking = True
seen = {}
t0 = time.time()
while time.time() - t0 < 8:
    data = h.read(64, 50)
    if data:
        hx = bytes(data).hex()
        if hx not in seen:
            seen[hx] = time.time() - t0
            print(f"  t+{time.time()-t0:4.1f}s  len={len(data)}  {hx}")
print(f"\n{len(seen)} distinct report(s)")
h.close()
