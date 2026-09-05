"""The FIP display, over libusb.

The FIP exposes two USB interfaces. Interface 0 is a plain HID keypad (see
input.py). Interface 1 -- "Saitek FIP Data Pipes" -- is vendor-class with a
pair of bulk endpoints, and because macOS binds no driver to a vendor-class
interface, libusb can claim it without root and without fighting the kernel.

The wire protocol is a 44-byte command header, optionally followed by a
payload, and every command is answered with a 44-byte status header that has
to be read or the device eventually wedges:

    offset  7   page number (1 for an image)
    offset  9   payload length, 24-bit big-endian
    offset 23   command: 0x0a = handshake, 0x06 = image

The image payload is 320*240*3 = 230400 bytes in the pixel layout of a 24bpp
Windows BMP -- blue/green/red per pixel, bottom row first. That is not a
coincidence: the Windows driver hands the device the body of a .bmp with the
header lopped off, so we hand it exactly the same bytes.
"""

from __future__ import annotations

import time

import usb.core
import usb.util
from usb.backend import libusb1

VID, PID = 0x06A3, 0xA2AE
WIDTH, HEIGHT = 320, 240
FRAME_BYTES = WIDTH * HEIGHT * 3

CMD_HANDSHAKE = 0x0A
CMD_IMAGE = 0x06
PAGE_IMAGE = 1
HEADER_LEN = 44
ACK_LEN = 512          # the device answers in one 44-byte packet; read a full buffer

# Homebrew does not put its dylibs anywhere ctypes looks by default.
LIBUSB_PATHS = (
    "/opt/homebrew/lib/libusb-1.0.dylib",
    "/usr/local/lib/libusb-1.0.dylib",
    "/opt/local/lib/libusb-1.0.dylib",
)


class FipError(RuntimeError):
    pass


def get_backend():
    for path in LIBUSB_PATHS:
        try:
            backend = libusb1.get_backend(find_library=lambda _x, p=path: p)
        except Exception:
            backend = None
        if backend is not None:
            return backend
    backend = libusb1.get_backend()          # whatever the system can find
    if backend is None:
        raise FipError("libusb not found -- run:  brew install libusb")
    return backend


def header(command: int, length: int = 0, page: int = 0) -> bytes:
    h = bytearray(HEADER_LEN)
    h[7] = page
    h[9] = (length >> 16) & 0xFF
    h[10] = (length >> 8) & 0xFF
    h[11] = length & 0xFF
    h[23] = command
    return bytes(h)


def list_devices():
    """Serial numbers of every FIP attached, in bus order."""
    out = []
    for dev in usb.core.find(find_all=True, idVendor=VID, idProduct=PID, backend=get_backend()):
        try:
            serial = usb.util.get_string(dev, dev.iSerialNumber)
        except Exception:
            serial = None
        out.append((serial, dev.bus, dev.address))
    return out


class FipDevice:
    """One Flight Instrument Panel's screen.

    Use from a single thread. Every public call recovers from a stalled
    endpoint on its own; if that fails it reopens the device from scratch,
    which is what unplugging and replugging would have done for you.
    """

    def __init__(self, serial: str | None = None, verbose: bool = False):
        self.serial = serial
        self.verbose = verbose
        self.dev = None
        self.intf = None
        self.ep_out = None
        self.ep_in = None
        self._img_header = header(CMD_IMAGE, FRAME_BYTES, PAGE_IMAGE)
        self.frames = 0
        self.recoveries = 0
        self.ack_losses = 0

    # -- lifecycle ---------------------------------------------------------
    def _log(self, msg):
        if self.verbose:
            print(f"[fip] {msg}", flush=True)

    def _find(self):
        backend = get_backend()
        for dev in usb.core.find(find_all=True, idVendor=VID, idProduct=PID, backend=backend):
            if self.serial is None:
                return dev
            try:
                if usb.util.get_string(dev, dev.iSerialNumber) == self.serial:
                    return dev
            except Exception:
                continue
        return None

    def open(self):
        """Find, reset and initialise the panel.

        The reset matters. The device keeps state across processes, and a
        program that exits mid-transfer leaves it refusing further commands --
        a reset is the one thing that reliably clears that without a replug.
        """
        dev = self._find()
        if dev is None:
            which = f" with serial {self.serial}" if self.serial else ""
            raise FipError(f"no Saitek FIP{which} found (looked for {VID:#06x}:{PID:#06x})")
        try:
            dev.reset()
            time.sleep(1.0)                  # it re-enumerates, so find it again
            dev = self._find()
            if dev is None:
                raise FipError("the FIP disappeared after a USB reset -- replug it")
        except usb.core.USBError as exc:
            self._log(f"reset failed, continuing anyway: {exc}")

        cfg = dev.get_active_configuration()
        try:
            intf = next(i for i in cfg if i.bInterfaceClass == 0xFF)
        except StopIteration:
            raise FipError("this FIP exposes no vendor-class data interface")
        ep_out = next(e for e in intf
                      if usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
        ep_in = next(e for e in intf
                     if usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)
        try:
            usb.util.claim_interface(dev, intf.bInterfaceNumber)
        except usb.core.USBError as exc:
            raise FipError(
                f"could not claim the FIP data interface ({exc}). Quit anything else "
                "driving the panel and try again.")

        self.dev, self.intf, self.ep_out, self.ep_in = dev, intf, ep_out, ep_in
        self._log(f"opened on bus {dev.bus} addr {dev.address}: "
                  f"OUT {ep_out.bEndpointAddress:#04x} / IN {ep_in.bEndpointAddress:#04x}")
        self.drain()                 # anything a previous process left behind
        self.handshake()
        time.sleep(0.15)             # the panel is not ready for pixels immediately
        return self

    def drain(self):
        """Discard any reply still queued on the IN endpoint.

        Every command is answered, so a command that fails halfway can leave an
        unread status packet behind. Read it now and the next handshake gets its
        own answer instead of the previous frame's -- which is exactly how the
        request/reply pairing used to slip by one and fail twice on startup.
        """
        dropped = 0
        while True:
            try:
                if not self.ep_in.read(ACK_LEN, 50):
                    break
            except usb.core.USBError:
                break
            dropped += 1
            if dropped > 8:
                break
        if dropped:
            self._log(f"drained {dropped} stale reply packet(s)")
        return dropped

    def handshake(self):
        self.ep_out.write(header(CMD_HANDSHAKE), 2000)
        reply = bytes(self.ep_in.read(ACK_LEN, 2000))
        self._log(f"handshake ack {reply[:24].hex()}")
        return reply

    def close(self):
        if self.dev is None:
            return
        try:
            usb.util.release_interface(self.dev, self.intf.bInterfaceNumber)
        except Exception:
            pass
        try:
            usb.util.dispose_resources(self.dev)
        except Exception:
            pass
        self.dev = self.intf = self.ep_out = self.ep_in = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    # -- output ------------------------------------------------------------
    def send_raw(self, payload: bytes):
        """Push one 230400-byte frame, already in the device's own layout.

        Sent as a single bulk write: libusb splits it into 512-byte packets in
        the kernel, which is twice as fast as writing the chunks ourselves
        (22 ms a frame rather than 45).
        """
        if len(payload) != FRAME_BYTES:
            raise ValueError(f"frame must be {FRAME_BYTES} bytes, got {len(payload)}")
        try:
            self._write_frame(payload)
        except usb.core.USBError as exc:
            self._log(f"write failed ({exc}) -- recovering")
            self.recover()
            self._write_frame(payload)      # let a second failure propagate
        self.frames += 1

    def _write_frame(self, payload: bytes):
        self.ep_out.write(self._img_header, 2000)
        self.ep_out.write(payload, 5000)
        try:
            self.ep_in.read(ACK_LEN, 2000)
        except usb.core.USBError:
            # Losing the status packet is not losing the frame: the pixels went
            # out before it, and the device has already drawn them. Clear the
            # input endpoint so the next frame's reply lines up again, and carry
            # on rather than resending a picture the panel is already showing.
            self.ack_losses += 1
            try:
                self.dev.clear_halt(self.ep_in.bEndpointAddress)
            except Exception:
                pass
            self.drain()

    def send_image(self, image):
        """Push a 320x240 PIL RGB image."""
        if image.size != (WIDTH, HEIGHT):
            image = image.resize((WIDTH, HEIGHT))
        if image.mode != "RGB":
            image = image.convert("RGB")
        self.send_raw(to_frame(image))

    # -- error recovery ----------------------------------------------------
    def recover(self):
        """Unstick the endpoints; failing that, reopen the device."""
        self.recoveries += 1
        if self.dev is None:
            self.open()
            return
        try:
            for ep in (self.ep_out, self.ep_in):
                try:
                    self.dev.clear_halt(ep.bEndpointAddress)
                except Exception:
                    pass
            self.drain()
            self.handshake()
            return
        except Exception as exc:
            self._log(f"handshake after clear_halt failed ({exc}) -- reopening")
        self.close()
        time.sleep(0.5)
        self.open()


def to_frame(image) -> bytes:
    """PIL RGB image -> the device's pixel layout (BGR, bottom row first)."""
    from PIL import Image as _Image
    return image.transpose(_Image.FLIP_TOP_BOTTOM).tobytes("raw", "BGR")
