#!/usr/bin/env python3
"""fipx -- drive a Saitek/Logitech Flight Instrument Panel from X-Plane 12 on macOS.

    ./fipx.py run                 fly it
    ./fipx.py run --demo          synthetic data, no sim needed
    ./fipx.py test                self-test: find the panel, show a test card
    ./fipx.py calibrate           learn which button is which
    ./fipx.py contact-sheet       write PNGs of every gauge, no hardware needed
"""

from __future__ import annotations

import argparse
import sys
import time


def cmd_run(args):
    from fipx.app import App
    from fipx.device import FipDevice, FipError
    from fipx.input import FipInput
    from fipx.xplane import DemoFeed, XPlaneFeed

    try:
        device = FipDevice(serial=args.serial, verbose=args.verbose).open()
    except FipError as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 1

    feed = (DemoFeed() if args.demo else XPlaneFeed(args.xp_host, args.xp_port)).start()
    buttons = FipInput(serial=args.serial, verbose=args.verbose).start()

    print("fipx — Saitek FIP on X-Plane 12")
    print(f"  panel:   {args.serial or 'first found'}")
    print(f"  source:  {'demo data' if args.demo else f'X-Plane at {args.xp_host}:{args.xp_port} (UDP)'}")
    if buttons.available:
        print("  buttons: soft keys 1-6 pick the instrument; left knob cycles;")
        print("           right knob sets the altimeter or the heading bug")
    else:
        print(f"  buttons: unavailable ({buttons.error}) — display only")
    print(f"  target:  {args.rate:g} fps.  Ctrl-C to stop.")

    app = App(feed, device, buttons, rate=args.rate, start_page=args.page,
              verbose=args.verbose, seconds=args.seconds)
    try:
        app.run()
    finally:
        print("\nshutting down")
        buttons.close()
        feed.close()
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
        device = FipDevice(serial=args.serial, verbose=True).open()
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
    buttons = FipInput(serial=args.serial).start()
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
    ]
    try:
        device = FipDevice(serial=args.serial).open()
    except FipError as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 1
    buttons = FipInput(serial=args.serial, mapping={}).start()
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
    common.add_argument("--serial", help="pick one panel by serial number (see: fipx.py test)")
    common.add_argument("--verbose", action="store_true")

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
    run.add_argument("--page", type=int, default=1,
                     help="instrument to start on, 0-5 (default 1, the attitude indicator)")
    run.set_defaults(func=cmd_run)

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
