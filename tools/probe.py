#!/usr/bin/env python3
"""Probe the Saitek FIP over libusb on macOS: enumerate, init, push a test image."""
import os, sys, time, struct
import usb.core, usb.util
from usb.backend import libusb1

VID, PID = 0x06A3, 0xA2AE
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fipx.device import get_backend
from fipx import gauges as G
BACKEND = get_backend()

INIT      = bytes.fromhex("00000000000000000000000000000000000000000000000a0000000000000000000000000000000000000000")
INIT_ACK  = bytes.fromhex("00000000000000000000000001000000000000000000000a0000000000000000000000000000000200000000")
IMG_HDR   = bytes.fromhex("0000000000000001000384000000000000000000000000060000000000000000000000000000000000000000")
IMG_ACK   = bytes.fromhex("0000000000000001000000000000000000000000000000060000000000000000000000000000000000000000")

print(f"header lengths: init={len(INIT)} img_hdr={len(IMG_HDR)}")

dev = usb.core.find(idVendor=VID, idProduct=PID, backend=BACKEND)
if dev is None:
    sys.exit("!! FIP 06a3:a2ae not found")
print(f"found: bus={dev.bus} addr={dev.address} speed={dev.speed} bcdDevice={dev.bcdDevice:#06x}")
try:
    print(f"serial: {usb.util.get_string(dev, dev.iSerialNumber)}")
except Exception as e:
    print(f"serial: unreadable ({e})")

cfg = dev.get_active_configuration()
for intf in cfg:
    print(f"  interface {intf.bInterfaceNumber} alt {intf.bAlternateSetting} "
          f"class={intf.bInterfaceClass} eps={intf.bNumEndpoints}")
    for ep in intf:
        d = "IN " if usb.util.endpoint_direction(ep.bEndpointAddress) == usb.util.ENDPOINT_IN else "OUT"
        t = {0:"ctrl",1:"iso",2:"bulk",3:"intr"}[usb.util.endpoint_type(ep.bmAttributes)]
        print(f"    ep {ep.bEndpointAddress:#04x} {d} {t} max={ep.wMaxPacketSize}")

data_if = next(i for i in cfg if i.bInterfaceClass == 0xFF)
ep_out = next(e for e in data_if if usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
ep_in  = next(e for e in data_if if usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)
print(f"\nusing interface {data_if.bInterfaceNumber}: OUT {ep_out.bEndpointAddress:#04x} / IN {ep_in.bEndpointAddress:#04x}")

usb.util.claim_interface(dev, data_if.bInterfaceNumber)
print("claimed interface OK")

def xfer(payload, label, expect=None, timeout=2000):
    n = ep_out.write(payload, timeout)
    try:
        reply = bytes(ep_in.read(ep_in.wMaxPacketSize, timeout))
    except usb.core.USBError as e:
        print(f"{label}: wrote {n}, read failed: {e}")
        return None
    print(f"{label}: wrote {n}, read {len(reply)}: {reply.hex()}")
    if expect is not None:
        print(f"{label}: {'MATCHES expected' if reply == expect else 'differs from expected: ' + expect.hex()}")
    return reply

xfer(INIT, "init", INIT_ACK)

# --- test image: labelled so we can read orientation and channel order off the screen ---
from PIL import Image, ImageDraw, ImageFont
img = Image.new("RGB", (320, 240), (0, 128, 0))
d = ImageDraw.Draw(img)
try:
    font = ImageFont.truetype(G.find_font(), 30)
    small = ImageFont.truetype(G.find_font(), 20)
except Exception:
    font = small = ImageFont.load_default()
d.rectangle([0, 0, 319, 59], fill=(255, 0, 0))
d.text((8, 12), "TOP = RED", fill=(255, 255, 255), font=font)
d.rectangle([0, 180, 319, 239], fill=(0, 0, 255))
d.text((8, 192), "BOTTOM = BLUE", fill=(255, 255, 255), font=font)
d.text((8, 90), "MIDDLE = GREEN", fill=(0, 0, 0), font=small)
d.text((8, 120), "left", fill=(255, 255, 255), font=small)
d.text((240, 120), "right", fill=(255, 255, 255), font=small)
img.save("/tmp/fip_test_reference.png")

# BMP pixel-data convention: BGR, bottom-up rows. 320*3 = 960, already 4-byte aligned.
buf = img.transpose(Image.FLIP_TOP_BOTTOM).tobytes("raw", "BGR")
print(f"\nimage buffer: {len(buf)} bytes (expect {320*240*3})")

t0 = time.time()
ep_out.write(IMG_HDR, 2000)
CHUNK = 512
for i in range(0, len(buf), CHUNK):
    ep_out.write(buf[i:i+CHUNK], 3000)
try:
    reply = bytes(ep_in.read(ep_in.wMaxPacketSize, 2000))
    print(f"image ack: {reply.hex()}")
    print(f"image ack {'MATCHES expected' if reply == IMG_ACK else 'differs from expected: ' + IMG_ACK.hex()}")
except usb.core.USBError as e:
    print(f"image ack read failed: {e}")
print(f"frame time: {(time.time()-t0)*1000:.1f} ms")

usb.util.release_interface(dev, data_if.bInterfaceNumber)
usb.util.dispose_resources(dev)
print("\ndone -- look at the FIP screen")
