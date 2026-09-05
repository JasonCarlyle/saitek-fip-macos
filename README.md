# saitek-fip-macos

**A Saitek / Logitech Flight Instrument Panel driver for macOS — the classic
six-pack, live from X-Plane 12.**

Logitech never shipped a Mac driver for the Pro Flight Instrument Panel. The
Windows one — DirectOutput, last updated in 2018 — is the only software that
ever spoke to it, and Logitech's own support page is explicit that the panels
are the exception to Mac compatibility: *"Other than Pro Flight Panels, all
other products in the Pro Flight and Farm Sim ranges are compatible with Mac OS
X 10.10.x or later as basic Plug and Play HID devices"*
([source](https://support.logi.com/hc/en-ch/articles/360023347393-Mac-support-for-Saitek-devices)).
So the panel sits in a drawer.

This drives it directly. No kernel extension, no DirectOutput service, no
X-Plane plugin.

```
X-Plane 12  --UDP 49000-->  fipx  --USB bulk-->  FIP screen
                             ^                   FIP keys and knobs
                             +---USB HID---------+
```

Why this is possible at all: the FIP's display lives on a **vendor-class** USB
interface, and macOS binds no driver to a vendor-class interface — so libusb
can claim it with no root and no kext. The buttons are a separate, ordinary HID
interface, which we read the supported way through hidapi rather than trying to
wrestle it away from the kernel. And X-Plane has published a UDP dataref
protocol for years, so the simulator side needs no plugin either.

> An independent, unaffiliated project. Saitek and Logitech are trademarks of
> their respective owners; this is not endorsed or supported by Logitech, and
> contains no Logitech code, firmware, or redistributed files. See
> [Licence](#licence). It draws instrument faces for a flight simulator — don't
> use it to fly a real aeroplane.

## Requirements

* A Mac (Apple Silicon or Intel). Developed on macOS 15; older versions
  should work but are untested. Linux *should* work too, but has never been run
  against a panel — see [linux/README.md](linux/README.md)
* [Homebrew](https://brew.sh), for `libusb` and `hidapi`
* Python 3.9 or newer (developed on 3.14)
* X-Plane 12, with **Settings ▸ Network ▸ Accept incoming connections** on
  (it is on by default)
* A Saitek / Logitech Pro Flight Instrument Panel, USB `06a3:a2ae`

## Install and run

```
git clone https://github.com/JasonCarlyle/saitek-fip-macos.git
cd saitek-fip-macos
./run.command
```

`run.command` installs the two Homebrew libraries, builds a virtualenv, and
starts the driver. It is double-clickable from Finder. By hand:

```
brew install libusb hidapi
python3 -m venv .venv && .venv/bin/pip install pyusb pillow hid
.venv/bin/python fipx.py run
```

Start order doesn't matter. The panel reads `NO DATA FROM X-PLANE` until the
sim answers, re-subscribes until it does, and recovers if X-Plane restarts. If
you have just launched X-Plane, expect a blank half-minute while it loads the
flight — it accepts UDP before it starts replying.

| command | what it does |
|---|---|
| `fipx.py run` | every panel attached, live from the sim |
| `fipx.py list` | what is plugged in, and whether panels can be told apart |
| `fipx.py identify` | put each panel's number on its own screen |
| `fipx.py run --demo` | synthetic flight — checks the panel without X-Plane |
| `fipx.py run --xp-host 192.168.1.50` | X-Plane on another machine |
| `fipx.py test` | find the panel, show a test card, echo key presses |
| `fipx.py leds` | walk the eight lamps one at a time (see [The lamps](#the-lamps)) |
| `fipx.py calibrate` | learn which key is which, prompting on the panel itself |
| `fipx.py contact-sheet` | render every gauge to PNG, no hardware needed |

Also: `--rate` frames per second per panel (default 25), `--page` which
instrument each panel starts on, `--serial` to restrict to particular panels,
`--verbose` for per-panel frame-rate and USB statistics, `--leds` to backlight
the soft key for the instrument on screen, and `--no-reset` to skip the USB
reset on open.

## More than one panel

One process drives every FIP attached, and each one is independent: its own
instrument, its own keys and knobs, its own threads. Just run it.

```
./fipx.py list          # what is attached
./fipx.py identify      # which physical panel is which
./fipx.py run           # all of them
```

With one panel this behaves exactly as it always did. With several, each gets a
different instrument to start with, so six panels come up as the whole six-pack,
one gauge each. `--page 4,1,0` assigns them explicitly, in the order `identify`
numbers them, and `--serial A,B` restricts the run to particular panels.

Panels are independent in the way that matters: **one failing does not touch the
others.** Unplug one of six mid-flight and the rest never drop a frame; the one
you removed disappears from the display within a few seconds, and plugging it
back in brings it up again on a free instrument. A panel that wedges rather than
disappears retries on its own with a backoff.

Panels are told apart by **USB serial number**, which is what survives the USB
reset on open. The panel here reports one, and — the part that matters — **its
keypad reports the same serial as its display**, so a screen really can be paired
with the right set of buttons. That is the assumption the whole design
rests on, and it holds on the one panel available to test it. Whether serials are
distinct *between* panels still needs somebody with two.

`fipx.py list` says plainly whether the serials on your panels are present and
distinct; if they are not, the driver falls back to USB bus order, prints a
warning, and disables both the reset and hotplug, because neither is safe
without a stable identity.

**What limits the count is USB bandwidth, not the Mac.** A frame is 230400
bytes, so one panel at 25 fps is 5.8 MB/s and six are 34.6 MB/s — most of what a
single USB 2.0 high-speed bus carries in practice. Rendering is nowhere near the
limit: PIL releases the GIL, and six panels rendering concurrently measured 82
fps each on an M3 Pro against a 25 fps target. If six on one hub come up short,
spread them across ports on different controllers, or drop `--rate`. `--verbose`
prints what each panel actually achieved.

**Tested how:** with one real panel, and with `--fake N`, which runs the entire
app — supervisor, per-panel threads, failure, retry, hotplug — against imaginary
displays that cost the same 22 ms a real bulk write does. That is how six-panel
behaviour was exercised without six panels, and it is what you should reach for
before buying more hardware:

```
./fipx.py run --demo --fake 6 --verbose         # six panels, no hardware
./fipx.py run --demo --fake 6 --fake-fail 0.5   # ...one of them misbehaving
```

Nothing in `--fake` speaks USB, so it proves the scheduling and the supervision,
not the protocol. Only the real device proves the protocol.

## The controls

Each panel's controls drive that panel only.

| control | does |
|---|---|
| soft keys 1–6 | airspeed, attitude, altimeter, turn coordinator, heading, vertical speed |
| up / down | step through the instruments (the pair between the knobs) |
| left knob | step through the instruments |
| right knob | on the altimeter, sets the Kollsman window; on the attitude and heading gauges, moves the heading bug |

The knobs write back into the sim, so turning the right knob on the altimeter
really does re-set the altimeter in X-Plane.

**The default key map is now confirmed on hardware** — all twelve controls, by
running `fipx.py calibrate`, which prompts for each one on the panel's own
screen. Run it if your panel behaves differently; you should not need to. Calibrate asks for each control in turn, prompting on the panel's own
screen, and saves the result to `~/.config/fipx/buttons.json`.

## The lamps

The six soft keys and the two buttons by the right knob are backlit, and the
panel takes a command to light them.

```
./fipx.py leds              # each lamp alone, then all on, then all off
./fipx.py run --leds        # light the soft key for the instrument on screen
./fipx.py leds s3 on        # one lamp: s1-s6, up, down, or all
```

All eight work. The command was decoded from a second USB capture, made
independently on Linux by [EasyNetDev](https://github.com/EasyNetDev/Saitek-FIP),
and confirmed here on a real panel: every lamp lights, and the device
acknowledges each command with the number echoed back. Lighting the key for the
instrument on screen is opt-in only because it is a matter of taste, not because
there is any doubt about the command.

## The instruments

* **Airspeed** — the arcs are built from the loaded aircraft's own V-speeds, so
  the dial re-scales when you change aircraft. A 172 gets a 40–180 kt face with
  a caution arc; a jet gets a wider one and no yellow.
* **Attitude** — pitch ladder and bank scale.
* **Altimeter** — three pointers and a Kollsman window.
* **Turn coordinator** — rate of turn differentiated from heading and smoothed,
  so it reads a true 3°/sec at the standard-rate marks in any aircraft; the ball
  comes from `slip_deg`.
* **Heading** — rotating card with a heading bug.
* **Vertical speed** — ±2000 fpm, or ±6000 for something fast.

Attitude and heading come from whichever gyro group the aircraft actually
models — vacuum, electric or AHARS — falling back to the flight model. An
aircraft that models none of them reads a flat zero forever otherwise. A
consequence worth knowing: with vacuum gyros the attitude and heading
indicators stay dead until the engine is running, exactly as they should.

## If it doesn't work

* **"no Saitek FIP found"** — check it is plugged in and lit. `fipx.py test`
  lists what it can see.
* **"could not claim the FIP data interface"** — something else already has it.
  Quit any other copy of the driver.
* **Buttons don't register but the display works** — they are two separate USB
  interfaces, so one can work without the other. Run `fipx.py calibrate`.
* **The panel wedges after a crash** — it isn't broken. The device keeps state
  across processes, and a program that dies mid-transfer leaves it refusing
  commands. The driver issues a USB reset when it opens the panel, which clears
  that without unplugging anything.
* **"NO DATA FROM X-PLANE"** — the sim isn't answering. Check Settings ▸
  Network ▸ Accept incoming connections, make sure a flight is actually loaded,
  and pass `--xp-host` if X-Plane is on another machine.

---

# How this was built

Nobody publishes this protocol. Here is where the information came from, what
had to be worked out, and how each piece was checked — both to give credit and
so the next person doesn't start from nothing.

## What already existed

**[EasyNetDev/Saitek-FIP](https://github.com/EasyNetDev/Saitek-FIP)** is the
reason this project was a few hours rather than a month. That repository contains
no source code but its author did the one step
that is genuinely hard to repeat: they ran Logitech's Windows DirectOutput
service under a USB capture, pulled the packets out with Wireshark and
`tshark`, and wrote down what the driver actually sends.

Four facts came from that README, and only those four:

1. The display is driven over **bulk USB**, not HID, on device `06a3:a2ae`.
2. Two literal 44-byte command strings, in hex, with the replies they expect —
   a handshake and an "image incoming" command.
3. The image payload is the **body of a 24bpp BMP with the header removed**,
   320 × 240 × 3 = 230400 bytes.
4. **No firmware upload is required.** DirectOutput is not a driver at all, just
   a service that pokes USB endpoints.

That last one was make-or-break. Plenty of devices in this class need a
firmware blob extracted from a Windows installer and pushed on every plug-in,
and if the FIP had been one of them this project would have looked very
different.

Nothing else existed to build on. There is no Mac driver for this device, open
or otherwise, and no code in that repository to fork.

## What had to be worked out here

Everything below was derived from the device itself, on macOS.

**That macOS could work at all.** Dumping the descriptors with `ioreg` showed
the FIP exposes two interfaces: interface 0 is HID, which macOS claims, and
interface 1 is **vendor-class, named "Saitek FIP Data Pipes"**. macOS binds no
driver to a vendor-class interface, which is precisely why libusb can take it
without root or a kernel extension. That is the fact the whole port rests on
and it appears in no documentation. The same dump gave the real endpoint
addresses — `0x02` OUT and `0x81` IN — settling a contradiction in the source
README, which said `0x82` in one paragraph and `0x81` in another, and confirmed
the device is USB high-speed, which set the frame-rate budget.

**The command structure.** The inherited material was two opaque hex blobs. In
them, `0x038400` = 230400 sits at bytes 9–11 as a big-endian length, the two
commands differ only at byte 23 (`0x0a` handshake, `0x06` image), and byte 7
looked like a page number. The driver therefore *builds* its headers from that
structure rather than replaying captured bytes, and `tools/hdrcheck.py` asserts
that the builder reproduces both captured strings exactly — a decoding of a
capture is a theory until something checks it.

That guess was later corrected from the other side. EasyNetDev, whose capture
this started from, subsequently shared the field layout they had arrived at
independently: the header is **eleven 32-bit big-endian words**, and byte 7 is
not a page number at all but the low byte of a 32-bit *running* flag at bytes
4–7, set to 1 once the handshake has been acknowledged. Identical bytes on the
wire — the driver was already correct — but the wrong idea about why.

The two decodes cross-check each other exactly. Every non-zero word in all four
packets observed here lands on one of their named fields and nowhere else, and
the two values in the handshake reply are the two constants in their code.
`tools/hdrcheck.py` asserts that too: an unnamed non-zero word anywhere in a
captured packet fails the run, so the layout cannot quietly become incomplete.
Three previously blank fields came out of it — a gauge number, and the LED
number and state that make [the lamps](#the-lamps) addressable.

**The pixel layout.** The source README said "RGB" in prose but "BMP body" in
practice, and those disagree: BMP pixel data is BGR, bottom row first. Settled
empirically by pushing a deliberately asymmetric test card labelled `1 TOP RED`
/ `3 LOW BLUE` / `<< LEFT` / `RIGHT >>` and looking at the panel. It came up
inverted with the colours correct and `<< LEFT` still on the left, which
confirms BGR, proves the rows are flipped, and rules out a 180° rotation — all
three at once, which a top-and-bottom-only test card could not have done.

**The buttons and knobs, from scratch** — the inherited material says nothing
about input. The HID report descriptor, pulled from `ioreg` and parsed by hand,
asks for twelve one-bit buttons in a two-byte report. That immediately explains
the knobs: they are not axes, so each detent arrives as a momentary press of a
direction bit and a knob is read by counting rising edges. The actual bit
assignment came from pressing every control and watching: soft keys are bits
0–5 in order, the left knob is 6/7, and the right knob is **10/11**, which is
not what the obvious guess would be.

That session got two things wrong, and both took a second one to catch.

It concluded bits 8 and 9 never fire. They are the UP and DOWN buttons between
the knobs — six soft keys against eight addressable lamps was the clue that two
controls had gone unpressed. "Never observed" was a statement about the
observing, not about the device.

And it recorded both knob *directions* backwards. Those were always flagged as a
guess, but the guess shipped as the default, so until this was measured the left
knob stepped backwards through the instruments and the right knob turned the
altimeter the wrong way. `fipx.py calibrate` had existed the whole time to settle
exactly this and nobody had run it against the defaults it was meant to check.

**Throughput.** Handing libusb the whole 230 KB frame in one bulk write runs at
44.8 fps — 22.3 ms and 10.3 MB/s a frame; splitting it into 512-byte packets
yourself, as the capture showed DirectOutput doing, halves that. Measured, not
assumed.

**The failure mode.** About 1% of frames returned `errno 5` when this was
written. They no longer do: a later session put 738 frames through the panel —
200 timed, 538 through the driver — with **zero** lost acks and zero recoveries.
What changed in between was draining the endpoint before each handshake and
sending a proper deinit on close, so the likeliest reading is that the 1% was a
symptom of the desynchronised request/reply pairing described below rather than
a property of the hardware. The recovery path stays, because it costs nothing
and one clean run is not proof; anyone porting this should know it may be
guarding against a bug that no longer exists.

The original observation, which is what the recovery code was built from:
instrumenting each stage separately over 400 frames showed the failure was
almost always the *ack read*, not the pixel transfer — by which point the image has already been
delivered and drawn. So the driver clears the input endpoint and carries on
instead of resending a picture the panel is already showing. Diagnosing that
also turned up the startup problem behind it: a failed transfer can leave an
unread status packet queued, so the next handshake reads the *previous* frame's
reply and the request/reply pairing slips by one. The fix is to drain the
endpoint before every handshake.

**Recovering a wedged panel.** The source README notes that the device stops
answering if it isn't shut down properly, and that the deinitialisation
commands were not yet understood. A USB reset on open turns out to clear it
completely, which is why you never have to unplug anything.

The deinit command is `0x13`, which came from the same later exchange as the
field names, and it is now confirmed at both ends: the panel answers it with
`running` set and the command echoed, and after sending one the device reopens
cleanly **with no USB reset at all**. So a clean shutdown really does leave
nothing to recover from. The reset stays on by default because it is what
rescues a panel left by a process that crashed; `--no-reset` skips it and saves
the second.

## How it was checked

Reverse-engineered protocol claims are worth nothing unless something tests
them, so each one has a check behind it:

* `tools/hdrcheck.py` **asserts** the header builder reproduces both captured
  command strings byte-for-byte, and that no non-zero word in any observed
  packet falls outside a named field; the panel returns the documented ack
  byte-for-byte
* the attitude indicator was verified **numerically**, not by eye — sampling
  the rendered horizon confirms 3.0 px per degree, linear across ±20°, with
  nose-up putting the horizon down and a right bank lifting the right side
* the turn coordinator's index marks were checked by computing where each
  wingtip actually lands at standard rate. This caught a real error: the marks
  were originally placed above the horizontal, where a right turn would have
  rested the *raised left* wingtip on the mark labelled `L`
* `tools/fake_xplane.py` answers RREF subscriptions and applies DREF writes, so
  the whole UDP layer — including the knobs writing back into the sim — was
  tested before the simulator was ever started
* chunk-size and per-stage benchmarks (`tools/chunkbench.py`,
  `tools/stagediag.py`) produced the throughput and error-rate numbers quoted
  above
* `tools/hwreport.py` puts every question that needs a real panel into one run —
  serial numbers, the handshake reply byte for byte, all eight lamps, the
  deinit, whether the reset is still required, and throughput — and writes a
  transcript. On this panel: **200 frames at 44.8 fps, 22.3 ms each, 10.3 MB/s,
  zero lost acks**, and the driver itself then ran 538 frames with no USB error,
  no recovery and no lost ack at all
* finally, flown against X-Plane 12.4.3 with a Cessna 172, holding 23 fps

---

# The protocol

For anyone doing this again, on any platform.

**Two interfaces.** Interface 0 is a HID keypad. Interface 1, "Saitek FIP Data
Pipes", is vendor-class with bulk endpoints `0x02` OUT and `0x81` IN, 512-byte
packets, USB high-speed.

**Commands** are a 44-byte header: eleven 32-bit **big-endian** words, of which
seven are named and four have never been seen to carry anything.

| word | bytes | field | meaning |
|---|---|---|---|
| 1 | 4–7 | running | `1` on every command after the handshake is acknowledged |
| 2 | 8–11 | length | payload bytes to follow (`0x038400` = 230400 for an image) |
| 3 | 12–15 | ack | device → host; `0x01000000` in the handshake reply |
| 5 | 20–23 | command | `0x0a` init, `0x06` image, `0x13` deinit, `0x18` LED |
| 6 | 24–27 | gauge number | host-side page bookkeeping; echoed back, never set by the device |
| 7 | 28–31 | LED number | `1`–`6` the soft keys, `7` up, `8` down |
| 8 | 32–35 | LED state | `0` off, `1` on |
| 9 | 36–39 | init status | device → host; `0x02` in the handshake reply |

Words 0, 4 and 10 have only ever been zero.

Every command is answered with a 44-byte status packet on the IN endpoint, and
that reply **must be read** or the device eventually stops responding.

**Lighting a lamp** is command `0x18` with `running` = 1, the LED number in
word 7 and `0`/`1` in word 8. **Shutting down** is command `0x13` with
`running` = 1 and nothing else set.

One oddity, on which both captures agree: `running` at bytes 4–7 reads as
big-endian `1` (`00 00 00 01`), but the ack at bytes 12–15 reads `01 00 00 00`.
If the whole header were uniformly 32-bit big-endian those would be 1 and
16777216. Either byte 12 is really an 8-bit flag at the top of that word, or
Logitech's own "the data is big-endian" advice is not the whole story. It makes
no practical difference — nothing needs to act on the value — but it is the one
place the model creaks.

The driver builds these headers from the table above rather than replaying
anything captured; `fipx/device.py` contains no literal command bytes at all.
`tools/hdrcheck.py` holds the captured packets and asserts that the builder
reproduces the observed handshake and image headers exactly, *and* that no
non-zero word in any of them falls outside a named field. A decoded capture is
only a theory until something checks it, and this is that check.

```
handshake  00000000000000000000000000000000000000000000000a0000...
   reply   00000000000000000000000001000000000000000000000a0000...0200000000
image      0000000000000001000384000000000000000000000000060000...
   reply   0000000000000001000000000000000000000000000000060000...
```

**The image** is 320 × 240 × 3 = 230400 bytes, blue-green-red per pixel,
**bottom row first** — the pixel layout of a 24bpp Windows BMP. 320 × 3 = 960
is already 4-byte aligned, so there is no row padding. Send it as one bulk
write; libusb splits it into packets in the kernel far more efficiently than
you can.

**Keys and knobs** are a two-byte HID report of twelve one-bit buttons, and all
twelve are now accounted for. Knobs report a momentary press per detent, so
count rising edges.

| bit | control | bit | control |
|---|---|---|---|
| 0–5 | soft keys 1–6 | 8 | **UP** button |
| 6 | left knob anticlockwise | 9 | **DOWN** button |
| 7 | left knob clockwise | 10 | right knob anticlockwise |
| | | 11 | right knob clockwise |

Bits 8 and 9 were long thought dead — an earlier version of this file said they
"were never seen to fire". They are the **UP and DOWN buttons between the two
knobs**, which the driver had never used and the first session never pressed.
Eight addressable lamps (`0x18` takes 1–8) alongside six soft keys was the clue.

**Recovery.** Send `0x13` on the way out and the panel needs no reset next time:
measured, a deinit followed by a reopen with no USB reset works first time. The
reset on open stays as the default because it is what rescues a panel left by a
process that *didn't* shut down cleanly — `--no-reset` skips it and saves about
a second. Drain the IN endpoint before each handshake so a lost status packet
cannot desynchronise the request/reply pairing.

**Confirmed on hardware:** every command in the table, including the two that
came from the second capture rather than from this panel. `0x18` lights all
eight lamps; `0x13` is answered with `running` set and the command echoed. The
only field still unexercised is the gauge number, which the device merely
carries for the host and never acts on.

# What is tested, and what isn't

Developed and flown on:

| | |
|---|---|
| Mac | Apple M3 Pro, macOS 15.7.4 |
| Python | 3.14, with pyusb, Pillow and hid |
| Libraries | libusb 1.0.30, hidapi 0.15.0 (Homebrew) |
| Simulator | X-Plane 12.4.3 |
| Panel | one FIP, `06a3:a2ae`, `bcdDevice` 0x0218 |
| Linux | Debian 12, Python 3.11, x86-64 and arm64 — no panel attached |

A note on Python versions, since it bit this project: `threading.Thread` has an
internal `_stop()` method that `join()` calls on Python 3.9 through 3.13, and a
`Thread` subclass with its own `self._stop` attribute shadows it, so `join()`
dies with `'Event' object is not callable`. CPython 3.14 removed that method,
which is the only reason the bug was invisible on the machine this was written
on. It surfaced the first time the driver ran on Debian. If you are reading this
code for ideas, that is a trap worth knowing about.

Not tested on real hardware: Intel Macs (the libusb path checks `/usr/local/lib`
as well as `/opt/homebrew/lib`, but nobody has run it), **more than one FIP at
once**, and older macOS versions. Everything a single panel can answer has been
answered — see [How it was checked](#how-it-was-checked). Multi-panel is written and exercised in full
against `--fake` panels — supervision, per-panel failure, hotplug attach and
remove — but this author owns one FIP, so the two things only real hardware can
answer are open: whether FIP serial numbers are in fact unique per panel, and
whether the bandwidth arithmetic above survives contact with a real hub. If you
own two or more, `fipx.py list` output is the single most useful thing you could
send. Bug reports welcome — `fipx.py test`
`--verbose` output is the useful thing to attach.

**Linux: we think it works, but it has never been tested on a panel.** The
platform-specific parts have been dealt with — libusb and font discovery now
resolve per platform, there is a udev rule and a `run.sh`, and nothing else in
the driver was ever macOS-only — and everything that can be checked without
hardware has been, on Debian 12 with Python 3.11: modules import, all six gauges
render, the protocol header check passes, six concurrent panels run, hotplug
attaches and removes, Ctrl-C shuts down cleanly.

But nobody here has a FIP plugged into a Linux machine, so **every claim about
Linux stops at the USB layer**. Whether the udev rule actually grants what is
needed, whether `dev.reset()` behaves, whether the bulk transfers and the hidraw
reads work against the real device — all of that is reasoned, not observed.
Treat it as untested, because it is. [linux/README.md](linux/README.md) has the
setup and lists what is worth reporting back if you try it.

# Files

```
fipx.py            command line: run, list, identify, test, leds, calibrate
run.command        double-clickable launcher for macOS; installs deps first time
run.sh             the same for Linux
linux/             udev rule and Linux setup notes
fipx/device.py     the display: libusb transport, framing, error recovery
fipx/input.py      the keys and knobs, over hidapi
fipx/xplane.py     X-Plane's UDP dataref feed, and DREF writes back
fipx/gauges.py     the six instruments
fipx/panel.py      one panel: render thread, USB writer thread, controls, recovery
fipx/app.py        the supervisor: discovery, hotplug, one feed for every panel
fipx/fake.py       imaginary panels, so N-panel behaviour can be tested with one
tools/             the reverse-engineering scripts, a stand-in X-Plane, and the
                   header check that holds device.py to the captured packets
```

# Credits

* **[EasyNetDev/Saitek-FIP](https://github.com/EasyNetDev/Saitek-FIP)** — the
  USB capture of Logitech's DirectOutput service that this protocol work starts
  from, and the observation that the FIP needs no firmware upload. Without that
  README this would have needed a Windows machine and a lot more guessing.
  Later, and just as generously, the field layout of the 44-byte header from
  their own Linux driver work: the names in the table above, the correction
  that byte 7 is a *running* flag rather than a page number, and the deinit and
  LED commands, which no capture made here had ever contained.
* **[sparker256/xsaitekpanels](https://github.com/sparker256/xsaitekpanels)** —
  the long-running X-Plane plugin that handles the Saitek radio, switch and
  multi panels on macOS and Linux. None of its code is used here, but it is
  what you want for the *other* panels, and it is proof these devices were
  always usable outside Windows.
* **Laminar Research** — for documenting X-Plane's UDP interface, which is why
  the simulator half of this needed no plugin and no reverse engineering.

# Other Saitek panels

This project only touches the Flight Instrument Panel. The radio, switch and
multi panels are plain HID and are already handled by
[xsaitekpanels](https://github.com/sparker256/xsaitekpanels).

# Licence

MIT — see [LICENSE](LICENSE).

Saitek and Logitech are trademarks of their respective owners, used here only
to identify the hardware this driver works with. This project is not affiliated
with, endorsed by, or supported by Logitech.

It contains no Logitech code, no firmware, and no files extracted from any
Logitech distribution. The device needs no firmware upload, so none is shipped
or referenced. The driver itself embeds no captured data of any kind: it
constructs its USB headers from the documented structure above, and the only
literal command strings anywhere in the repository are four 44-byte constants —
two commands and the two status packets they were answered with, largely zeros,
describing a wire format — in the `tools/` scripts used to establish and check
that structure.

The protocol description here was arrived at by observing a device the author
owns, for the purpose of making it work with hardware and software the author
also owns.

It draws instrument faces for a flight simulator. Do not use it to fly a real
aeroplane.
