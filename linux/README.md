# Running the FIP driver on Linux

> **This has never been run against a real panel on Linux.** Nobody working on
> this repository has a FIP plugged into a Linux machine. Everything below is
> expected to work and everything testable without hardware has been tested, but
> the parts that only a real device can exercise — the USB transfers, the HID
> reads, whether the udev rule grants what is actually needed — are reasoned,
> not observed. If you are the first person to try it, please see
> [what to report back](#what-this-has-and-has-not-been-tested-against).

This is the same driver as the rest of the repository, running on Linux. Not a
port, not a rewrite — pyusb, hidapi and Pillow are cross-platform, and the only
things that were ever macOS-specific were where to find libusb and where to find
a font. Both are now resolved per platform.

So in principle everything should be there: the six instruments, X-Plane over
UDP, the keys and knobs, the knobs writing back into the sim, multiple panels in
one process, hotplug.

## Install

**1. System libraries.** pyusb and hid are ctypes wrappers around real shared
objects, which pip cannot install for you.

```
# Debian / Ubuntu
sudo apt install python3-venv libusb-1.0-0 libhidapi-hidraw0 fonts-liberation
# Fedora
sudo dnf install python3-virtualenv libusb1 hidapi liberation-sans-fonts
# Arch
sudo pacman -S python libusb hidapi ttf-liberation
```

`fonts-liberation` is worth having rather than DejaVu: Liberation Sans Narrow is
metric-compatible with Arial Narrow, which is what the dials were drawn against,
so the gauges lay out identically to the macOS ones. DejaVu works and is the
automatic fallback, but it is wider.

**2. The udev rule.** Without this the driver only runs as root, and the error
libusb gives for the missing permission says only "Access denied".

```
sudo cp linux/99-saitek-fip.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Then **unplug the panel and plug it back in** — udev applies rules when a device
appears, not retroactively.

The rule grants two separate things, because the FIP is two devices in one: the
display is a vendor-class USB interface reached through libusb, and the keys are
an ordinary HID interface that the kernel binds to usbhid and exposes as
`/dev/hidrawN`. Missing the second is the usual cause of "the screen works but no
button does anything". It uses `TAG+="uaccess"`, which hands the permission to
whoever is logged in at the seat; there is a commented-out
`MODE="0660" GROUP="plugdev"` fallback for systems without logind.

**3. Run it.**

```
./run.sh                    # builds the venv on first run, then flies
./run.sh --demo             # synthetic data, no simulator needed
./fipx.py list              # what is attached, and whether panels can be told apart
```

## If it doesn't work

| symptom | cause |
|---|---|
| `Access denied (insufficient permissions)` | the udev rule is missing, or the panel was not replugged after installing it |
| screen works, buttons do nothing | the `hidraw` half of the udev rule did not apply |
| `libusb not found` | `libusb-1.0-0` is not installed (the `-dev` package is not needed) |
| `no TrueType font found` | install `fonts-liberation` or `fonts-dejavu-core` |
| `Resource busy` on claim | something else already has the panel — another copy of the driver, most likely |

## What this has and has not been tested against

**Tested:** Debian 12, Python 3.11, x86-64 and arm64. Every module imports, all
six gauges render, the header builder still reproduces the captured packets, the
supervisor runs six panels concurrently at ~23 fps each, hotplug attach and
remove work, Ctrl-C shuts down cleanly in under 0.1 s.

**Not tested:** a real Saitek FIP on Linux — none of it. Nobody here has one
attached to a Linux machine. Everything *above* the USB layer is exercised; the
USB layer itself is the same code that drives a real panel on macOS, but the
Linux libusb path — permissions, kernel-driver detach, `dev.reset()`, the bulk
transfers themselves — has never met the hardware. Nor has the udev rule: it is
written to the documented shape of the problem, not verified against a device
appearing on a bus.

**If you are the first person to run this on Linux with a panel attached**,
`python3 tools/hwreport.py` asks every question that needs hardware and writes a
report file you can send back. It covers all four of these:

1. `./fipx.py list` output. It prints the USB serial numbers, which is the one
   fact multi-panel support rests on and which nobody has been able to confirm.
2. Whether `dev.reset()` on open works, or whether you need `--no-reset`. On
   macOS the reset works, *and* `--no-reset` works after a clean `0x13` deinit,
   so either should be fine; Linux permissions are the unknown.
3. Whether `./fipx.py leds` lights the lamps. All eight light on the macOS test
   panel, so this is really asking whether your panel behaves the same — a
   second confirmation, not a first.
4. The frame rate from `./run.sh --verbose`. macOS gets ~43 fps of headroom
   against a 25 fps target; Linux should be at least as good.

## Notes for anyone writing a native Linux driver

Things this codebase learned that are not in the protocol description, and that
apply whatever language you write in:

* **One bulk transfer per frame, not 512-byte chunks.** Hand libusb all 230400
  bytes and let the kernel packetise. Measured on macOS this is twice as fast as
  chunking the way DirectOutput does — 22 ms a frame against 45.
* **On Linux you can do better still.** `libusb_dev_mem_alloc()` gets you a
  zero-copy DMA buffer through usbfs, and the async API
  (`libusb_submit_transfer`) lets you keep more than one frame in flight. Neither
  is available on macOS, so neither is used here.
* **Do not resend a frame when the ack read fails.** About 1% of frames fail, and
  instrumenting each stage separately showed it is almost always the *ack*, not
  the pixel transfer — the image has already been delivered and drawn by then.
  Clear the halt, drain the endpoint, carry on.
* **Drain the IN endpoint before each handshake.** A command that fails halfway
  leaves an unread status packet queued, and the next handshake then reads the
  *previous* command's reply. The request/reply pairing slips by one and stays
  wrong.
* **`libusb_hotplug_register_callback()` works on Linux** and removes the need
  for a rescan loop and retry counters entirely.
* **Interface 0 is claimed by usbhid.** Read the buttons through hidraw rather
  than fighting the kernel for it. The report is twelve one-bit buttons in two
  bytes: soft keys are bits 0–5, the left knob is 6/7, the right knob is **10/11**,
  and bits 8/9 never fire. The knobs are not axes — each detent arrives as a
  momentary press, so count rising edges.
* **A USB reset clears a panel wedged by a crashed process**, which is the
  fallback if the `0x13` deinit has not been sent.

The wire protocol itself — the 44-byte header word by word, both commands, the
status replies and the image format — is in the main [README](../README.md#the-protocol).
