import os, sys
#!/usr/bin/env python3
import usb.core, usb.util, time
from usb.backend import libusb1
from PIL import Image, ImageDraw, ImageFont
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from fipx.device import get_backend
from fipx import gauges as G
B = get_backend()
INIT    = bytes.fromhex("00000000000000000000000000000000000000000000000a0000000000000000000000000000000000000000")
IMG_HDR = bytes.fromhex("0000000000000001000384000000000000000000000000060000000000000000000000000000000000000000")
dev = usb.core.find(idVendor=0x06A3, idProduct=0xA2AE, backend=B)
try: dev.reset(); time.sleep(1.0); dev = usb.core.find(idVendor=0x06A3, idProduct=0xA2AE, backend=B)
except Exception: pass
cfg = dev.get_active_configuration()
intf = next(i for i in cfg if i.bInterfaceClass == 0xFF)
eo = next(e for e in intf if usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
ei = next(e for e in intf if usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)
usb.util.claim_interface(dev, intf.bInterfaceNumber)
eo.write(INIT, 2000); ei.read(512, 2000)

img = Image.new("RGB", (320, 240), (0, 120, 0))
d = ImageDraw.Draw(img)
F = lambda s: ImageFont.truetype(G.find_font(), s)
d.rectangle([0, 0, 319, 55], fill=(255, 0, 0))
d.text((10, 12), "1 TOP RED", fill=(255, 255, 255), font=F(32))
d.rectangle([0, 185, 319, 239], fill=(0, 0, 255))
d.text((10, 196), "3 LOW BLUE", fill=(255, 255, 255), font=F(32))
d.text((10, 80),  "2 MID GREEN", fill=(0, 0, 0), font=F(26))
d.text((10, 130), "<< LEFT", fill=(255, 255, 255), font=F(22))
d.text((205, 130), "RIGHT >>", fill=(255, 255, 255), font=F(22))
img.save("/tmp/fip_reference.png")
eo.write(IMG_HDR, 2000); eo.write(img.tobytes("raw", "BGR"), 3000); ei.read(512, 2000)
print("sent: BGR, top-down row order (no vertical flip)")
usb.util.release_interface(dev, intf.bInterfaceNumber); usb.util.dispose_resources(dev)
