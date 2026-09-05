"""The FIP display, over libusb.

The FIP exposes two USB interfaces. Interface 0 is a plain HID keypad (see
input.py). Interface 1 -- "Saitek FIP Data Pipes" -- is vendor-class with a
pair of bulk endpoints, and because macOS binds no driver to a vendor-class
interface, libusb can claim it without root and without fighting the kernel.

The wire protocol is a 44-byte command header, optionally followed by a
payload, and every command is answered with a 44-byte status header that has
to be read or the device eventually wedges. The header is eleven 32-bit
big-endian words, of which seven are named:

    word 1  bytes  4-7   running      1 once the handshake is acknowledged
    word 2  bytes  8-11  payload length
    word 3  bytes 12-15  ack          device -> host
    word 5  bytes 20-23  command      0x0a init, 0x06 image, 0x13 deinit, 0x18 LED
    word 6  bytes 24-27  gauge number host-side bookkeeping; echoed back
    word 7  bytes 28-31  LED number   1-8
    word 8  bytes 32-35  LED state    0 off, 1 on
    word 9  bytes 36-39  init status  device -> host

The field names, and the three fields this driver had never exercised -- the
LED pair and the gauge number -- come from EasyNetDev's independent capture of
the same device on Linux. Every non-zero word in the headers observed here
lands on one of them, which is the cross-check that the layout is real: what
was previously read as "a page number at byte 7" is the low byte of the
32-bit running flag, and the 24-bit length at bytes 9-11 is the bottom of a
32-bit one at 8-11. Same bytes on the wire either way.

The image payload is 320*240*3 = 230400 bytes in the pixel layout of a 24bpp
Windows BMP -- blue/green/red per pixel, bottom row first. That is not a
coincidence: the Windows driver hands the device the body of a .bmp with the
header lopped off, so we hand it exactly the same bytes.
"""

from __future__ import annotations

import sys
import time

import usb.core
import usb.util
from usb.backend import libusb1

VID, PID = 0x06A3, 0xA2AE
WIDTH, HEIGHT = 320, 240
FRAME_BYTES = WIDTH * HEIGHT * 3

CMD_HANDSHAKE = 0x0A
CMD_IMAGE = 0x06
CMD_DEINIT = 0x13
CMD_LED = 0x18
HEADER_LEN = 44
ACK_LEN = 512          # the device answers in one 44-byte packet; read a full buffer

# Word indices into the 44-byte header. Byte offset is index * 4.
W_RUNNING = 1
W_LENGTH = 2
W_ACK = 3
W_COMMAND = 5
W_GAUGE = 6
W_LED_NO = 7
W_LED_STATE = 8
W_INIT_STATUS = 9

# The eight lamps, in the order the device numbers them. The six soft keys run
# down the side of the screen; UP and DOWN are the pair by the right knob.
LEDS = {"s1": 1, "s2": 2, "s3": 3, "s4": 4, "s5": 5, "s6": 6, "up": 7, "down": 8}
LED_OFF, LED_ON = 0, 1

# Where libusb actually lives. On macOS, Homebrew and MacPorts put their dylibs
# somewhere ctypes does not look by default, so the paths have to be spelled
# out. On Linux the loader finds it by soname and this list is only a fallback
# for the distributions that do not ship the -dev symlink.
if sys.platform == "darwin":
    LIBUSB_PATHS = (
        "/opt/homebrew/lib/libusb-1.0.dylib",     # Homebrew, Apple Silicon
        "/usr/local/lib/libusb-1.0.dylib",        # Homebrew, Intel
        "/opt/local/lib/libusb-1.0.dylib",        # MacPorts
    )
    INSTALL_HINT = "brew install libusb"
else:
    LIBUSB_PATHS = (
        "libusb-1.0.so.0",
        "/usr/lib/x86_64-linux-gnu/libusb-1.0.so.0",
        "/usr/lib/aarch64-linux-gnu/libusb-1.0.so.0",
        "/usr/lib64/libusb-1.0.so.0",
        "/usr/lib/libusb-1.0.so.0",
    )
    INSTALL_HINT = ("apt install libusb-1.0-0   (or: dnf install libusb1, "
                    "pacman -S libusb)")

# The first thing that goes wrong on Linux is permissions, not code: a plain
# user cannot open a USB device without a udev rule, and the error libusb gives
# for that says only "Access denied".
if sys.platform == "darwin":
    CLAIM_HINT = "Quit anything else driving the panel and try again."
else:
    CLAIM_HINT = ("Quit anything else driving the panel. If it says access "
                  "denied, you need the udev rule -- see linux/README.md -- or "
                  "you are running without the permission to open USB devices.")


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
        raise FipError(f"libusb not found -- install it with:  {INSTALL_HINT}")
    return backend


def header(command: int, length: int = 0, running: int = 0, gauge: int = 0,
           led_no: int = 0, led_state: int = 0) -> bytes:
    """Build a command header from its fields, never from a captured string.

    tools/hdrcheck.py asserts that this reproduces the two headers observed on
    the wire byte for byte; a decoded capture is a theory until something
    checks it.
    """
    words = [0] * 11
    words[W_RUNNING] = running
    words[W_LENGTH] = length
    words[W_COMMAND] = command
    words[W_GAUGE] = gauge
    words[W_LED_NO] = led_no
    words[W_LED_STATE] = led_state
    return b"".join(w.to_bytes(4, "big") for w in words)


def read_word(packet: bytes, index: int) -> int:
    """One 32-bit big-endian word out of a 44-byte status header."""
    return int.from_bytes(packet[index * 4:index * 4 + 4], "big")


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

    def __init__(self, serial: str | None = None, verbose: bool = False,
                 reset: bool = True, index: int | None = None):
        self.serial = serial
        self.index = index
        self.verbose = verbose
        self.reset_on_open = reset
        self.dev = None
        self.intf = None
        self.ep_out = None
        self.ep_in = None
        self._img_header = header(CMD_IMAGE, FRAME_BYTES, running=1)
        self.frames = 0
        self.recoveries = 0
        self.ack_losses = 0
        self.leds = {name: 0 for name in LEDS}

    # -- lifecycle ---------------------------------------------------------
    def _log(self, msg):
        if self.verbose:
            print(f"[fip] {msg}", flush=True)

    def _find(self):
        """The device this object is for, or None.

        Serial number is the selector that survives a USB reset, so it is the
        one to use when several panels are attached. `index` -- position in
        bus order -- is the fallback for panels that report no serial or a
        duplicate one; it does not survive a reset, which is why the app turns
        the reset off when it has to select that way.
        """
        backend = get_backend()
        devices = sorted(
            usb.core.find(find_all=True, idVendor=VID, idProduct=PID, backend=backend),
            key=lambda d: (d.bus, d.address))
        if self.index is not None:
            return devices[self.index] if self.index < len(devices) else None
        for dev in devices:
            if self.serial is None:
                return dev
            try:
                if usb.util.get_string(dev, dev.iSerialNumber) == self.serial:
                    return dev
            except Exception:
                continue
        return None

    @property
    def label(self):
        """How this panel is named in logs: its serial, or its position."""
        if self.serial:
            return self.serial
        return f"#{self.index}" if self.index is not None else "first found"

    def open(self):
        """Find, reset and initialise the panel.

        The reset matters. The device keeps state across processes, and a
        program that exits mid-transfer leaves it refusing further commands --
        a reset is the one thing that reliably clears that without a replug.

        Now that close() sends a proper deinit, a clean shutdown may leave the
        panel in a state that needs no reset at all, which would save the
        second this costs. Pass reset=False to find out on your own hardware.
        """
        dev = self._find()
        if dev is None:
            which = f" with serial {self.serial}" if self.serial else (
                f" at position {self.index}" if self.index is not None else "")
            raise FipError(f"no Saitek FIP{which} found (looked for {VID:#06x}:{PID:#06x})")
        if self.reset_on_open:
            try:
                dev.reset()
                time.sleep(1.0)              # it re-enumerates, so find it again
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
        # On Linux the kernel may have a driver bound to an interface, and
        # claiming it then fails with "Resource busy". Nothing should bind to a
        # vendor-class interface, but usbfs will hand it back if asked, and
        # asking is free. macOS cannot answer the question at all, hence the
        # NotImplementedError -- and there, nothing binds either.
        try:
            if dev.is_kernel_driver_active(intf.bInterfaceNumber):
                dev.detach_kernel_driver(intf.bInterfaceNumber)
                self._log(f"detached the kernel driver from interface "
                          f"{intf.bInterfaceNumber}")
        except NotImplementedError:
            pass
        except usb.core.USBError as exc:
            self._log(f"could not check for a kernel driver: {exc}")

        try:
            usb.util.claim_interface(dev, intf.bInterfaceNumber)
        except usb.core.USBError as exc:
            raise FipError(
                f"could not claim the FIP data interface ({exc}). {CLAIM_HINT}")

        self.dev, self.intf, self.ep_out, self.ep_in = dev, intf, ep_out, ep_in
        self._log(f"opened {self.label} on bus {dev.bus} addr {dev.address}: "
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
        """Send the init command and read the status packet it is answered with.

        The reply carries two documented values: 0x01000000 in the ack word and
        0x02 in the init-status word. Neither is acted on -- the panel works
        whatever they say -- but a reply that does not carry them is worth
        seeing in --verbose output when something is wrong.
        """
        self.ep_out.write(header(CMD_HANDSHAKE), 2000)
        reply = bytes(self.ep_in.read(ACK_LEN, 2000))
        if self.verbose:
            self._log(f"handshake ack {reply[:HEADER_LEN].hex()}")
            ack, status = read_word(reply, W_ACK), read_word(reply, W_INIT_STATUS)
            note = "" if (ack, status) == (0x01000000, 0x02) else "  <- not the documented reply"
            self._log(f"  ack={ack:#010x} init_status={status:#x}{note}")
        return reply

    def deinit(self):
        """Tell the panel we are done with it.

        The counterpart to the handshake, and the command that was missing when
        the only way to unstick a panel left behind by a crashed process was a
        USB reset on the next open. Best-effort: this runs on the way out, so a
        failure here must not stop the interface being released.
        """
        try:
            self.ep_out.write(header(CMD_DEINIT, running=1), 1000)
            reply = bytes(self.ep_in.read(ACK_LEN, 1000))
            self._log(f"deinit ack {reply[:HEADER_LEN].hex()}")
            return reply
        except Exception as exc:
            self._log(f"deinit failed, closing anyway: {exc}")
            return None

    def close(self, deinit: bool = True):
        if self.dev is None:
            return
        if deinit:
            self.deinit()
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

    # -- lamps -------------------------------------------------------------
    def set_led(self, led, on: bool):
        """Light or extinguish one of the panel's eight lamps.

        `led` is a name from LEDS ("s1".."s6", "up", "down") or the device's own
        number, 1-8. Confirmed on hardware: all eight numbers light their lamp
        and the device acks each one with the command echoed back. The command
        number and the two fields it uses came from EasyNetDev's Linux capture,
        so this is one decode checked from both ends.
        """
        number = LEDS.get(led, led) if isinstance(led, str) else led
        if number not in range(1, 9):
            raise ValueError(f"no such LED: {led!r} (use one of {', '.join(LEDS)}, or 1-8)")
        state = LED_ON if on else LED_OFF
        try:
            self.ep_out.write(
                header(CMD_LED, running=1, led_no=number, led_state=state), 1000)
            self.ep_in.read(ACK_LEN, 1000)
        except usb.core.USBError as exc:
            # A lamp is decoration. Never let one take the display down with it.
            self._log(f"LED {led} -> {'on' if on else 'off'} failed: {exc}")
            self.drain()
            return False
        for name, num in LEDS.items():
            if num == number:
                self.leds[name] = state
        return True

    def set_leds(self, states):
        """Apply a {name: bool} map, skipping lamps already in the right state."""
        changed = 0
        for name, want in states.items():
            if self.leds.get(name, 0) != (LED_ON if want else LED_OFF):
                if self.set_led(name, want):
                    changed += 1
        return changed

    def all_leds(self, on: bool):
        return self.set_leds({name: on for name in LEDS})

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
        # No deinit: the panel is not answering, so the two transfers would only
        # time out and add seconds to a recovery that is already slow.
        self.close(deinit=False)
        time.sleep(0.5)
        self.open()


def to_frame(image) -> bytes:
    """PIL RGB image -> the device's pixel layout (BGR, bottom row first)."""
    from PIL import Image as _Image
    return image.transpose(_Image.FLIP_TOP_BOTTOM).tobytes("raw", "BGR")
