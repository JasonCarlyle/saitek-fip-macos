#!/usr/bin/env python3
"""Find exactly where sustained FIP streaming breaks, and what recovers it."""
import time, sys
import usb.core, usb.util
from usb.backend import libusb1
from PIL import Image

BACKEND = libusb1.get_backend(find_library=lambda x: "/opt/homebrew/lib/libusb-1.0.dylib")
INIT    = bytes.fromhex("00000000000000000000000000000000000000000000000a0000000000000000000000000000000000000000")
IMG_HDR = bytes.fromhex("0000000000000001000384000000000000000000000000060000000000000000000000000000000000000000")
print("IMG_HDR bytes:", " ".join(f"{i}:{b:02x}" for i, b in enumerate(IMG_HDR) if b))
print("INIT    bytes:", " ".join(f"{i}:{b:02x}" for i, b in enumerate(INIT) if b))

dev = usb.core.find(idVendor=0x06A3, idProduct=0xA2AE, backend=BACKEND)
try:
    dev.reset(); print("device reset OK"); time.sleep(1.0)
    dev = usb.core.find(idVendor=0x06A3, idProduct=0xA2AE, backend=BACKEND)
except Exception as e:
    print("reset failed:", e)

cfg = dev.get_active_configuration()
intf = next(i for i in cfg if i.bInterfaceClass == 0xFF)
ep_out = next(e for e in intf if usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
ep_in  = next(e for e in intf if usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)
usb.util.claim_interface(dev, intf.bInterfaceNumber)

def init():
    ep_out.write(INIT, 2000)
    return bytes(ep_in.read(512, 2000))

print("init ->", init().hex()[:40], "...")

img = Image.new("RGB", (320, 240), (0, 60, 0))
buf = img.tobytes("raw", "BGR")

def send(mode):
    ep_out.write(IMG_HDR, 2000)
    if mode == "chunk":
        for i in range(0, len(buf), 512):
            ep_out.write(buf[i:i+512], 3000)
    else:
        ep_out.write(buf, 3000)
    return bytes(ep_in.read(512, 2000))

for mode in ("single", "chunk"):
    print(f"\n--- mode={mode} ---")
    ok = fail = 0
    for n in range(1, 31):
        try:
            t0 = time.time(); send(mode); dt = (time.time()-t0)*1000
            ok += 1
            if n <= 3 or n % 10 == 0:
                print(f"  frame {n:3d}: OK {dt:5.1f} ms")
        except usb.core.USBError as e:
            fail += 1
            print(f"  frame {n:3d}: FAIL {e}")
            for ep in (ep_out, ep_in):
                try: dev.clear_halt(ep.bEndpointAddress)
                except Exception as ce: print(f"    clear_halt {ep.bEndpointAddress:#04x}: {ce}")
            try:
                print("    re-init ->", init().hex()[:24], "...")
            except usb.core.USBError as ie:
                print(f"    re-init failed: {ie}"); break
    print(f"  {mode}: {ok} ok / {fail} failed")

usb.util.release_interface(dev, intf.bInterfaceNumber); usb.util.dispose_resources(dev)
