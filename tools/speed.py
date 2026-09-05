import os, sys
#!/usr/bin/env python3
"""Measure sustained FIP frame rate: chunked writes vs one big write."""
import time, math
import usb.core, usb.util
from usb.backend import libusb1
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fipx.device import get_backend
BACKEND = get_backend()
INIT    = bytes.fromhex("00000000000000000000000000000000000000000000000a0000000000000000000000000000000000000000")
IMG_HDR = bytes.fromhex("0000000000000001000384000000000000000000000000060000000000000000000000000000000000000000")

dev = usb.core.find(idVendor=0x06A3, idProduct=0xA2AE, backend=BACKEND)
cfg = dev.get_active_configuration()
intf = next(i for i in cfg if i.bInterfaceClass == 0xFF)
ep_out = next(e for e in intf if usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
ep_in  = next(e for e in intf if usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)
usb.util.claim_interface(dev, intf.bInterfaceNumber)

ep_out.write(INIT, 2000); ep_in.read(512, 2000)

def frame(buf, chunked):
    ep_out.write(IMG_HDR, 2000)
    if chunked:
        for i in range(0, len(buf), 512):
            ep_out.write(buf[i:i+512], 3000)
    else:
        ep_out.write(buf, 3000)
    ep_in.read(512, 2000)

def make(angle):
    img = Image.new("RGB", (320, 240), (10, 10, 30))
    d = ImageDraw.Draw(img)
    d.ellipse([40, 0, 280, 240], outline=(90, 90, 110), width=3)
    cx, cy, r = 160, 120, 105
    x, y = cx + r*math.sin(angle), cy - r*math.cos(angle)
    d.line([cx, cy, x, y], fill=(255, 220, 60), width=5)
    d.text((120, 210), "streaming...", fill=(200, 200, 200))
    return img.transpose(Image.FLIP_TOP_BOTTOM).tobytes("raw", "BGR")

frames = [make(i * math.tau / 36) for i in range(36)]

for label, chunked in (("chunked 512B", True), ("single write", False)):
    t0 = time.time(); n = 60
    for i in range(n):
        frame(frames[i % 36], chunked)
    dt = time.time() - t0
    print(f"{label}: {n/dt:5.1f} fps  ({dt/n*1000:.1f} ms/frame)")

# leave the labelled reference image on screen
img = Image.open("/tmp/fip_test_reference.png")
frame(img.transpose(Image.FLIP_TOP_BOTTOM).tobytes("raw", "BGR"), False)
usb.util.release_interface(dev, intf.bInterfaceNumber)
usb.util.dispose_resources(dev)
print("reference image left on screen")
