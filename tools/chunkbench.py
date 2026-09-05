#!/usr/bin/env python3
"""How big should a bulk write be? Measure speed and error rate per chunk size."""
import sys, time
sys.path.insert(0, ".")
import usb.core
from PIL import Image
from fipx.device import FipDevice, FRAME_BYTES, to_frame, ACK_LEN

frames = [to_frame(Image.new("RGB", (320, 240), (0, 40 + 20 * i, 0))) for i in range(4)]
dev = FipDevice(verbose=False).open()

def trial(chunk, n=150):
    errors, t0 = 0, time.time()
    for i in range(n):
        buf = frames[i % 4]
        try:
            dev.ep_out.write(dev._img_header, 2000)
            if chunk >= len(buf):
                dev.ep_out.write(buf, 5000)
            else:
                for off in range(0, len(buf), chunk):
                    dev.ep_out.write(buf[off:off + chunk], 5000)
            dev.ep_in.read(ACK_LEN, 2000)
        except usb.core.USBError:
            errors += 1
            try:
                dev.recover()
            except Exception:
                dev.close(); time.sleep(0.5); dev.open()
    dt = time.time() - t0
    label = "single write" if chunk >= FRAME_BYTES else f"{chunk // 1024} KB chunks"
    print(f"  {label:16s} {n/dt:5.1f} fps   {dt/n*1000:5.1f} ms/frame   {errors} error(s) in {n}")
    return errors

print("150 frames per chunk size:")
for chunk in (FRAME_BYTES, 65536, 32768, 16384, 4096):
    trial(chunk)
dev.close()
