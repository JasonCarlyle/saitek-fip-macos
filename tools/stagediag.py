#!/usr/bin/env python3
"""Which part of the header/payload/ack transaction fails, and how often?"""
import sys, time, collections
sys.path.insert(0, ".")
import usb.core
from PIL import Image
from fipx.device import FipDevice, to_frame, ACK_LEN

frames = [to_frame(Image.new("RGB", (320, 240), (0, 30 + 15 * i, 60))) for i in range(4)]
dev = FipDevice(verbose=False).open()
fails = collections.Counter()
acks = collections.Counter()
N = 400
t0 = time.time()
for i in range(N):
    buf = frames[i % 4]
    stage = "header"
    try:
        dev.ep_out.write(dev._img_header, 2000)
        stage = "payload"
        dev.ep_out.write(buf, 5000)
        stage = "ack"
        reply = bytes(dev.ep_in.read(ACK_LEN, 2000))
        acks[reply[:24].hex()] += 1
    except usb.core.USBError as exc:
        fails[f"{stage}: errno {exc.errno}"] += 1
        try:
            dev.recover()
        except Exception:
            dev.close(); time.sleep(0.5); dev.open()
    time.sleep(0.04)                       # pace it like the real app, ~25 fps
dt = time.time() - t0
print(f"{N} frames in {dt:.1f}s ({N/dt:.1f} fps)")
print(f"failures: {sum(fails.values())} ({sum(fails.values())/N*100:.1f}%)")
for k, v in fails.most_common():
    print(f"  {k}: {v}")
print("ack packets seen:")
for k, v in acks.most_common():
    print(f"  {v:4d} x {k}")
dev.close()
