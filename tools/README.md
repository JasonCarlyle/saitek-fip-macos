# tools

The scripts the driver was reverse-engineered with, kept because they are the
evidence behind the protocol notes in the main README — and because anyone
extending this will want them.

None of them are needed to *use* the driver.

| script | what it does |
|---|---|
| `hwreport.py` | **run this with the panel plugged in.** Every question only real hardware can answer — serial numbers, the handshake reply, the LED command, deinit, whether the USB reset is still needed, throughput — in one pass, written to a report file to send on |
| `hdrcheck.py` | holds the four captured 44-byte packets and asserts `device.py`'s header builder still reproduces them, with no non-zero word outside a named field. No hardware needed; run it after touching `header()` |
| `probe.py` | the first contact: enumerate the FIP, print its descriptors, handshake, and push one labelled test image. Run this first on unfamiliar hardware. |
| `hiddesc.py` | dump and decode the HID report descriptor from IOKit — how the twelve-button, two-byte report layout was found |
| `hidprobe.py` | listen to the HID interface and print each distinct report, for reading off the button bit map |
| `speed.py` | frame-rate comparison: one big bulk write versus 512-byte chunks |
| `chunkbench.py` | speed *and* error rate across a range of bulk transfer sizes |
| `stagediag.py` | which stage of the header/payload/ack transaction actually fails, over 400 frames. This is how the ~1% failures were traced to the ack read rather than the pixel transfer. |
| `diag.py` | sustained-streaming stress test with clear-halt/re-handshake recovery |
| `refimg.py` | push a labelled orientation test card — how the BGR, bottom-row-first pixel layout was confirmed |
| `fake_xplane.py` | a stand-in X-Plane: answers RREF subscriptions and applies DREF writes, so the whole UDP layer can be tested without the simulator |

Typical use:

```
.venv/bin/python tools/probe.py            # is the panel talking to us at all?
.venv/bin/python tools/fake_xplane.py &    # then, in another shell:
.venv/bin/python fipx.py run --verbose
```

All of these now resolve libusb and fonts through `fipx.device.get_backend()`
and `fipx.gauges.find_font()`, so they run on Linux as well as macOS.
