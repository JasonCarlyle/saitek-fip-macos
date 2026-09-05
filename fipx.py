#!/usr/bin/env python3
"""fipx -- drive a Saitek/Logitech Flight Instrument Panel from X-Plane 12 on macOS.

    ./fipx.py run                 fly it -- every panel attached, each independent
    ./fipx.py run --demo          synthetic data, no sim needed
    ./fipx.py list                what is attached, and how screens pair with keypads
    ./fipx.py identify            put each panel's number on its own screen
    ./fipx.py test                self-test: find the panel, show a test card
    ./fipx.py leds                walk the eight lamps, one at a time
    ./fipx.py calibrate           learn which button is which
    ./fipx.py contact-sheet       write PNGs of every gauge, no hardware needed
"""

from __future__ import annotations

import argparse
import sys
import time


def one_serial(args):
    """--serial takes a list for `run`; the single-panel commands take the first."""
    if not args.serial:
        return None
    first, _, rest = args.serial.partition(",")
    if rest:
        print(f"note: using panel {first.strip()} (--serial lists several)", flush=True)
    return first.strip()


def cmd_run(args):
    """Drive every panel attached, each showing its own instrument."""
    from fipx.app import App, default_pages, discover
    from fipx.xplane import DemoFeed, XPlaneFeed

    wanted = [x.strip() for x in args.serial.split(",")] if args.serial else None

    factory = None
    if args.fake:
        from fipx.fake import FakePanel
        FakePanel.dump_dir = args.fake_out
        FakePanel.fail_rate = args.fake_fail
        selectors, warning, factory = [{"index": i} for i in range(args.fake)], None, FakePanel
    else:
        selectors, warning = discover(wanted)
    if warning:
        print(f"!! {warning}\n", file=sys.stderr)
    if not selectors:
        print("!! no Saitek FIP found. Check it is plugged in and lit; "
              "`fipx.py list` shows what this machine can see.", file=sys.stderr)
        return 1

    # Position-based selection cannot survive the re-enumeration a USB reset
    # causes, so it is off whenever we had to fall back to it.
    by_index = any("index" in sel for sel in selectors)
    reset = not args.no_reset and not by_index

    if args.page:
        pages = [int(x) % 6 for x in args.page.split(",")]
    else:
        pages = default_pages(len(selectors))

    feed = (DemoFeed() if args.demo else XPlaneFeed(args.xp_host, args.xp_port)).start()
    app = App(feed, selectors, rate=args.rate, pages=pages, verbose=args.verbose,
              seconds=args.seconds, leds=args.leds, reset=reset,
              panel_factory=factory)

    print("fipx — Saitek FIP on X-Plane 12")
    print(f"  source:  {'demo data' if args.demo else f'X-Plane at {args.xp_host}:{args.xp_port} (UDP)'}")
    print(f"  panels:  {len(app.panels)}"
          f"{' (fake)' if args.fake else ''}")
    for panel in app.panels.values():
        print(f"    {panel.key:>16s}  starts on {panel.pages[panel.page].name.lower()}")
    print("  buttons: soft keys 1-6 pick the instrument; left knob cycles;")
    print("           right knob sets the altimeter or the heading bug"
          + (", per panel" if len(app.panels) > 1 else ""))
    print(f"  target:  {args.rate:g} fps per panel.  Ctrl-C to stop.")

    try:
        app.run()
    finally:
        print("\nshutting down")
        for line in app.summary():
            print(f"  {line}")
        feed.close()
    if not app.ever_ran:
        print("\n!! no panel ever started. See the errors above, or run: "
              "fipx.py list", file=sys.stderr)
        return 1
    return 0


def cmd_list(args):
    """Show every panel, and whether screens can be paired with keypads.

    With one panel this is a nicety. With three or six it is the thing that
    tells you whether the driver can keep them apart at all -- pairing a screen
    with the right keypad is done by USB serial number, and everything about
    multi-panel depends on those being present and distinct.
    """
    import hid
    from fipx.device import PID, VID, list_devices
    from fipx.input import find_hid  # noqa: F401  (kept for symmetry of imports)

    displays = list_devices()
    keypads = sorted(hid.enumerate(VID, PID), key=lambda i: i.get("path") or b"")

    print(f"displays (vendor interface, libusb): {len(displays)}")
    for serial, bus, addr in displays:
        print(f"  serial={serial!r:24s} bus={bus} addr={addr}")
    print(f"keypads (HID, hidapi): {len(keypads)}")
    for info in keypads:
        print(f"  serial={info.get('serial_number')!r:24s} "
              f"path={info['path']!r}")

    if not displays:
        print("\nnothing attached.")
        return 1

    serials = [s for s, _b, _a in displays]
    if all(serials) and len(set(serials)) == len(serials):
        print("\nSerial numbers are present and distinct, so panels can be told "
              "apart\nreliably: each screen pairs with the keypad reporting the "
              "same serial,\nand a panel keeps its identity across a USB reset "
              "and a replug.")
        paired = {i.get("serial_number") for i in keypads}
        for serial in serials:
            mark = "ok" if serial in paired else "NO MATCHING KEYPAD"
            print(f"  {serial:>24s}  {mark}")
    elif len(displays) == 1:
        print("\nOne panel, so nothing has to be told apart. (Its serial is "
              f"{serials[0]!r}.)")
    else:
        print("\n!! Serial numbers are missing or duplicated, so panels can only "
              "be\n   selected by USB bus order. That works, but it is fragile: "
              "the USB\n   reset on open is disabled, and replugging can swap "
              "which panel shows\n   which instrument. Please open an issue with "
              "this output — it is the\n   one thing about multi-panel that has "
              "never been tested on real hardware.")
    return 0


def cmd_identify(args):
    """Show each panel its own number, so you can tell which is which.

    With six panels on a desk, the serial numbers in `fipx.py list` mean nothing
    until you can see which screen belongs to which. This puts the number and
    the serial on the glass.
    """
    from PIL import Image, ImageDraw
    from fipx.app import discover
    from fipx.device import FipDevice, FipError, to_frame
    from fipx import gauges as G

    selectors, warning = discover()
    if warning:
        print(f"!! {warning}\n", file=sys.stderr)
    if not selectors:
        print("!! no Saitek FIP found.", file=sys.stderr)
        return 1
    by_index = any("index" in sel for sel in selectors)

    def card(n, label):
        img = Image.new("RGB", (320, 240), (10, 10, 14))
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, 319, 239], outline=(90, 90, 100))
        d.text((160, 24), "PANEL", fill=(150, 150, 155), font=G.font(16), anchor="ma")
        d.text((160, 60), str(n), fill=(255, 176, 0), font=G.font(96), anchor="ma")
        d.text((160, 186), label, fill=(230, 230, 230), font=G.font(14), anchor="ma")
        return to_frame(img)

    opened = []
    try:
        for i, selector in enumerate(selectors, start=1):
            label = selector.get("serial") or f"bus position {selector.get('index')}"
            try:
                device = FipDevice(verbose=args.verbose, reset=not by_index,
                                   **selector).open()
            except FipError as exc:
                print(f"  panel {i} ({label}): !! {exc}", file=sys.stderr)
                continue
            opened.append(device)
            device.send_raw(card(i, label))
            print(f"  panel {i}: {label}")
        if not opened:
            return 1
        print(f"\nEach screen is showing its number for {args.seconds:g}s. "
              f"Note which is which —\nthat is the order `fipx.py run --page` "
              f"assigns instruments in.")
        time.sleep(args.seconds)
    finally:
        for device in opened:
            device.close()
    return 0


def cmd_test(args):
    """Prove the whole display path without X-Plane or a rendering pipeline."""
    from PIL import Image, ImageDraw
    from fipx.device import FipDevice, FipError, list_devices, to_frame
    from fipx import gauges as G

    found = list_devices()
    if not found:
        print("!! no FIP found. Check it is plugged in and powered.", file=sys.stderr)
        return 1
    for serial, bus, addr in found:
        print(f"found FIP serial={serial} bus={bus} addr={addr}")

    try:
        device = FipDevice(serial=one_serial(args), verbose=True,
                           reset=not args.no_reset).open()
    except FipError as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 1

    img = Image.new("RGB", (320, 240), (10, 10, 14))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 319, 239], outline=(90, 90, 100))
    for i, (label, colour) in enumerate([("RED", (220, 40, 35)), ("GREEN", (40, 180, 70)),
                                         ("BLUE", (60, 110, 230))]):
        d.rectangle([12, 30 + i * 34, 60, 56 + i * 34], fill=colour)
        d.text((70, 36 + i * 34), label, fill=(230, 230, 230), font=G.font(16))
    d.text((160, 14), "fipx self-test", fill=(230, 230, 230), font=G.font(18), anchor="ma")
    d.text((160, 150), "colours correct, text upright", fill=(180, 180, 185),
           font=G.font(12), anchor="ma")
    d.text((160, 168), "= display path is good", fill=(180, 180, 185),
           font=G.font(12), anchor="ma")
    d.text((12, 200), "press any soft key…", fill=(150, 150, 155), font=G.font(13))
    device.send_raw(to_frame(img))
    print("test card sent — the screen should read upright with correct colours")

    from fipx.input import FipInput
    buttons = FipInput(serial=one_serial(args)).start()
    if not buttons.available:
        print(f"!! buttons unavailable: {buttons.error}")
        device.close()
        return 0
    print("watching buttons for 10 s — press the soft keys and turn the knobs")
    seen = []
    t0 = time.time()
    while time.time() - t0 < 10:
        for event in buttons.poll():
            seen.append(event)
            print(f"  {event}")
        time.sleep(0.05)
    print(f"{len(seen)} events, {len(set(seen))} distinct controls")
    if not seen:
        print("  (nothing seen — if the keys do work, run:  ./fipx.py calibrate)")
    buttons.close()
    device.close()
    return 0


def cmd_leds(args):
    """Light the panel's eight lamps, one at a time.

    The command number (0x18) and the two header fields it uses were decoded
    from a capture made by someone else on someone else's panel, then confirmed
    here: all eight light, and the device acks each command with the number
    echoed back. This stays a separate command because it is the quickest way
    to check the lamps on a panel nobody has tried yet.
    """
    from fipx.device import LEDS, FipDevice, FipError

    try:
        device = FipDevice(serial=one_serial(args), verbose=True,
                           reset=not args.no_reset).open()
    except FipError as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 1

    names = list(LEDS)
    try:
        if args.led:                          # one lamp, explicitly
            if args.led != "all" and args.led not in LEDS:
                print(f"!! no such LED: {args.led} (use one of {', '.join(names)}, or all)",
                      file=sys.stderr)
                return 1
            target = names if args.led == "all" else [args.led]
            want = args.state != "off"
            for name in target:
                ok = device.set_led(name, want)
                print(f"  {name:5s} -> {'on' if want else 'off':3s}  "
                      f"{'sent' if ok else 'FAILED'}")
            return 0

        print("Watch the panel. Each lamp should light alone for a moment.")
        print("If nothing lights, the LED command is decoded wrong -- say so and")
        print("nothing else in the driver is affected.\n")
        device.all_leds(False)
        for name in names:
            print(f"  {name:5s} (LED {LEDS[name]}) …", flush=True)
            device.set_led(name, True)
            time.sleep(args.dwell)
            device.set_led(name, False)
        print("\n  all on …", flush=True)
        device.all_leds(True)
        time.sleep(args.dwell * 2)
        print("  all off")
        device.all_leds(False)
        print("\nDid the lamps follow along? If they did, `fipx.py run --leds` "
              "lights\nthe soft key for the instrument on screen.")
    finally:
        device.close()
    return 0


def cmd_calibrate(args):
    """Ask for each control by name and record which bit it sets."""
    from PIL import Image, ImageDraw
    from fipx.device import FipDevice, FipError, to_frame
    from fipx.input import FipInput, save_mapping
    from fipx import gauges as G

    WANTED = [
        ("s1", "soft key 1", "the FIRST soft key"),
        ("s2", "soft key 2", "the SECOND soft key"),
        ("s3", "soft key 3", "the THIRD soft key"),
        ("s4", "soft key 4", "the FOURTH soft key"),
        ("s5", "soft key 5", "the FIFTH soft key"),
        ("s6", "soft key 6", "the SIXTH soft key"),
        ("left_cw", "left knob right", "turn the LEFT knob CLOCKWISE"),
        ("left_ccw", "left knob left", "turn the LEFT knob ANTICLOCKWISE"),
        ("right_cw", "right knob right", "turn the RIGHT knob CLOCKWISE"),
        ("right_ccw", "right knob left", "turn the RIGHT knob ANTICLOCKWISE"),
        ("up", "up button", "press UP, between the knobs"),
        ("down", "down button", "press DOWN, between the knobs"),
    ]
    try:
        device = FipDevice(serial=one_serial(args)).open()
    except FipError as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 1
    buttons = FipInput(serial=one_serial(args), mapping={}).start()
    if not buttons.available:
        print(f"!! cannot read the buttons: {buttons.error}", file=sys.stderr)
        device.close()
        return 1

    def show(title, detail, note=""):
        img = Image.new("RGB", (320, 240), (10, 10, 14))
        d = ImageDraw.Draw(img)
        d.text((160, 30), "CALIBRATE", fill=(150, 150, 155), font=G.font(14), anchor="ma")
        d.text((160, 78), title, fill=(255, 176, 0), font=G.font(26), anchor="ma")
        d.text((160, 124), detail, fill=(230, 230, 230), font=G.font(15), anchor="ma")
        if note:
            d.text((160, 190), note, fill=(150, 150, 155), font=G.font(12), anchor="ma")
        d.text((160, 212), "s to skip · Ctrl-C to stop", fill=(110, 110, 115),
               font=G.font(11), anchor="ma")
        device.send_raw(to_frame(img))

    mapping, used = {}, set()
    print("Watch the FIP screen and follow the prompts.")
    try:
        for name, short, prompt in WANTED:
            show(short.upper(), prompt, "press it now")
            print(f"  {prompt} … ", end="", flush=True)
            buttons.poll()
            bit = None
            t0 = time.time()
            while time.time() - t0 < 15:
                while not buttons.raw_events.empty():
                    _t, value, changed = buttons.raw_events.get()
                    for b in range(12):
                        if changed & (1 << b) and value & (1 << b) and b not in used:
                            bit = b
                            break
                    if bit is not None:
                        break
                if bit is not None:
                    break
                time.sleep(0.02)
            if bit is None:
                print("nothing seen, skipped")
                continue
            mapping[bit] = name
            used.add(bit)
            print(f"bit {bit}")
            show(short.upper(), f"got bit {bit}", "release it")
            time.sleep(0.6)
    except KeyboardInterrupt:
        print("\ninterrupted")

    if mapping:
        path = save_mapping(mapping)
        print(f"\nsaved {len(mapping)} controls to {path}")
        for bit, name in sorted(mapping.items()):
            print(f"  bit {bit:2d} -> {name}")
        show("DONE", f"{len(mapping)} controls learned")
    else:
        print("\nnothing learned; the built-in default mapping stays in place")
    time.sleep(1.0)
    buttons.close()
    device.close()
    return 0


def cmd_contact_sheet(args):
    from PIL import Image
    from fipx import gauges as G
    from fipx.xplane import DemoFeed

    data = DemoFeed().snapshot()
    data.update(turn_rate_dps=3.0, slip_deg=2.0, roll_deg=15.0, pitch_deg=6.0, vsi_fpm=700.0)
    sheet = Image.new("RGB", (320 * 3 + 40, 240 * 2 + 30), (30, 30, 30))
    for i, cls in enumerate(G.ALL_GAUGES):
        gauge = cls()
        img = gauge.render(data)
        img.save(f"{args.out}/fip_{gauge.key}.png")
        sheet.paste(img, (10 + (i % 3) * 330, 10 + (i // 3) * 250))
    sheet.save(f"{args.out}/fip_sheet.png")
    print(f"wrote {len(G.ALL_GAUGES)} gauges + fip_sheet.png to {args.out}")
    return 0


def main():
    # Shared flags, accepted on either side of the subcommand.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--serial",
                        help="restrict to these panels by serial number, comma separated "
                             "(default: every panel attached). See: fipx.py list")
    common.add_argument("--verbose", action="store_true")
    common.add_argument("--no-reset", action="store_true",
                        help="skip the USB reset on open (it should be unnecessary "
                             "after a clean shutdown, which now sends a deinit)")

    ap = argparse.ArgumentParser(description=__doc__, parents=[common],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", parents=[common], help="drive the panel from X-Plane")
    run.add_argument("--demo", action="store_true", help="synthetic data instead of the sim")
    run.add_argument("--xp-host", default="127.0.0.1", help="machine running X-Plane")
    run.add_argument("--xp-port", type=int, default=49000)
    run.add_argument("--rate", type=float, default=25.0, help="frames per second")
    run.add_argument("--seconds", type=float, default=0.0,
                     help="stop after this many seconds (0 = run until Ctrl-C)")
    run.add_argument("--page", default=None,
                     help="instrument each panel starts on, 0-5, comma separated per "
                          "panel (default: one panel starts on the attitude indicator; "
                          "several get consecutive instruments, so six panels show the "
                          "whole six-pack)")
    run.add_argument("--fake", type=int, default=0, metavar="N",
                     help="drive N imaginary panels instead of real ones — the only way "
                          "to exercise six-panel behaviour without six panels")
    run.add_argument("--fake-out", default=None, metavar="DIR",
                     help="with --fake, write each panel's last frame here as a PNG")
    run.add_argument("--fake-fail", type=float, default=0.0, metavar="P",
                     help="with --fake, fail this fraction of frames, to exercise "
                          "per-panel recovery")
    run.add_argument("--leds", action="store_true",
                     help="backlight the soft key for the instrument on screen "
                          "(try fipx.py leds to see the lamps on their own)")
    run.set_defaults(func=cmd_run)

    leds = sub.add_parser("leds", parents=[common], help="light the panel's lamps one by one")
    leds.add_argument("led", nargs="?",
                      help="one of s1-s6, up, down, or all; omit to run the whole sequence")
    leds.add_argument("state", nargs="?", default="on", choices=["on", "off"])
    leds.add_argument("--dwell", type=float, default=0.6,
                      help="seconds to hold each lamp (default 0.6)")
    leds.set_defaults(func=cmd_leds)

    lst = sub.add_parser("list", parents=[common],
                         help="what is attached, and whether panels can be told apart")
    lst.set_defaults(func=cmd_list)

    ident = sub.add_parser("identify", parents=[common],
                           help="show each panel its own number")
    ident.add_argument("--seconds", type=float, default=10.0,
                       help="how long to hold the number on screen (default 10)")
    ident.set_defaults(func=cmd_identify)

    test = sub.add_parser("test", parents=[common], help="find the panel and show a test card")
    test.set_defaults(func=cmd_test)

    cal = sub.add_parser("calibrate", parents=[common], help="learn which button is which")
    cal.set_defaults(func=cmd_calibrate)

    sheet = sub.add_parser("contact-sheet", parents=[common], help="render every gauge to PNG (no hardware)")
    sheet.add_argument("--out", default="/tmp", help="output directory")
    sheet.set_defaults(func=cmd_contact_sheet)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
