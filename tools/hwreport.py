#!/usr/bin/env python3
"""Everything a real panel can answer, in one pass, written to a report file.

The three things added since the last hardware session were decoded from someone
else's capture and have never been sent to a device by this code: the LED command
(0x18), the deinit command (0x13), and the claim that a clean deinit makes the
USB reset on open unnecessary. On top of that sits the assumption the whole
multi-panel design rests on -- that a FIP reports a usable serial number.

This runs all of it, prompts you to look at the screen where only your eyes can
judge, and writes a transcript you can send on. It is safe: the worst case is a
wedged panel, which a replug clears.

    python3 tools/hwreport.py [--out report.txt] [--no-leds]
"""

from __future__ import annotations

import argparse
import os
import platform
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import usb.core
import usb.util

from fipx import gauges as G
from fipx.device import (ACK_LEN, CMD_DEINIT, FRAME_BYTES, HEADER_LEN, LEDS,
                         PID, VID, W_ACK, W_INIT_STATUS, FipDevice, FipError,
                         header, list_devices, read_word, to_frame)

LINES: list[str] = []


def say(text=""):
    print(text, flush=True)
    LINES.append(text)


UNANSWERED = []


def ask(question):
    """A yes/no only a human looking at the panel can answer.

    Returns "y", "n", "s" (deliberately skipped) or "?" (could not be asked --
    no terminal, or stdin at EOF). The last one is NOT a no, and the difference
    matters: an earlier version of this script collapsed them and reported a
    confirmed-working LED command as broken, which is exactly the kind of false
    negative a report like this exists to avoid.
    """
    if not sys.stdin.isatty():
        LINES.append(f"    {question} -> NOT ASKED (no terminal)")
        UNANSWERED.append(question)
        return "?"
    try:
        answer = input(f"    {question} [y/n/skip] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        LINES.append(f"    {question} -> NOT ASKED (input closed)")
        UNANSWERED.append(question)
        return "?"
    LINES.append(f"    {question} -> {answer or 'skip'}")
    return answer[:1] if answer and answer[0] in "yns" else "s"


def section(title):
    say()
    say(f"--- {title} " + "-" * max(0, 60 - len(title)))


def card(title, detail=""):
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (320, 240), (10, 10, 14))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 319, 239], outline=(90, 90, 100))
    d.text((160, 60), title, fill=(255, 176, 0), font=G.font(30), anchor="ma")
    if detail:
        d.text((160, 130), detail, fill=(230, 230, 230), font=G.font(15), anchor="ma")
    return to_frame(img)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="fip_hardware_report.txt")
    ap.add_argument("--no-leds", action="store_true", help="skip the LED tests")
    ap.add_argument("--frames", type=int, default=200, help="frames for the timing run")
    args = ap.parse_args()

    say("Saitek FIP hardware report")
    say(f"  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    say(f"  {platform.platform()}")
    say(f"  Python {platform.python_version()}  {platform.machine()}")

    # -- 1. identity ------------------------------------------------------
    section("1. what is attached, and can panels be told apart")
    found = list_devices()
    say(f"displays found: {len(found)}")
    for serial, bus, addr in found:
        say(f"  serial={serial!r}  bus={bus}  addr={addr}")
    if not found:
        say("!! no FIP attached — nothing else can run")
        return 1
    try:
        import hid
        pads = sorted(hid.enumerate(VID, PID), key=lambda i: i.get("path") or b"")
        say(f"keypads found: {len(pads)}")
        for info in pads:
            say(f"  serial={info.get('serial_number')!r}  path={info['path']!r}")
    except Exception as exc:
        say(f"keypad enumeration failed: {exc}")
        pads = []

    serials = [s for s, _b, _a in found]
    if all(serials) and len(set(serials)) == len(serials):
        say("VERDICT: serials are present and distinct — multi-panel identity is sound")
    elif len(found) == 1 and serials[0]:
        say(f"VERDICT: one panel, serial {serials[0]!r} is present. "
            "Whether serials are UNIQUE still needs two panels to prove.")
    else:
        say("VERDICT: serials are missing or duplicated — multi-panel would fall back "
            "to bus order. This is the answer that matters most; please report it.")

    # -- 2. descriptors ---------------------------------------------------
    section("2. descriptors")
    dev = usb.core.find(idVendor=VID, idProduct=PID)
    say(f"bcdDevice={dev.bcdDevice:#06x} speed={dev.speed}")
    for intf in dev.get_active_configuration():
        say(f"  interface {intf.bInterfaceNumber} class={intf.bInterfaceClass} "
            f"({'vendor' if intf.bInterfaceClass == 0xFF else 'HID' if intf.bInterfaceClass == 3 else '?'})")
        for ep in intf:
            direction = ("IN " if usb.util.endpoint_direction(ep.bEndpointAddress)
                         == usb.util.ENDPOINT_IN else "OUT")
            say(f"    ep {ep.bEndpointAddress:#04x} {direction} max={ep.wMaxPacketSize}")
    usb.util.dispose_resources(dev)

    # -- 3. handshake -----------------------------------------------------
    section("3. handshake, and the documented reply")
    device = FipDevice(verbose=False).open()
    say("opened and handshaken OK (with the USB reset)")
    device.drain()
    device.ep_out.write(header(0x0A), 2000)
    reply = bytes(device.ep_in.read(ACK_LEN, 2000))[:HEADER_LEN]
    say(f"reply: {reply.hex()}")
    ack, status = read_word(reply, W_ACK), read_word(reply, W_INIT_STATUS)
    say(f"  ack word (bytes 12-15)        = {ack:#010x}   expected 0x01000000")
    say(f"  init status word (bytes 36-39) = {status:#x}          expected 0x2")
    say(f"  VERDICT: {'matches the documented reply' if (ack, status) == (0x01000000, 2) else 'DIFFERS — report this'}")

    # -- 4. display + timing ----------------------------------------------
    section("4. display and throughput")
    device.send_raw(card("DISPLAY OK", "colours and text upright?"))
    answer = ask("Does the panel show 'DISPLAY OK', upright, in amber?")
    if answer == "n":
        say("  !! display path is wrong — report this")
    elif answer == "y":
        say("  display confirmed by eye")
    frame = card("TIMING", "measuring…")
    t0 = time.time()
    for _ in range(args.frames):
        device.send_raw(frame)
    elapsed = time.time() - t0
    say(f"{args.frames} frames in {elapsed:.2f}s = {args.frames/elapsed:.1f} fps, "
        f"{elapsed/args.frames*1000:.1f} ms/frame")
    say(f"  lost acks: {device.ack_losses}   recoveries: {device.recoveries}")
    say(f"  ({FRAME_BYTES*args.frames/elapsed/1e6:.1f} MB/s)")

    # -- 5. LEDs ----------------------------------------------------------
    section("5. LED command 0x18 — confirmed on one panel; check yours")
    if args.no_leds:
        say("skipped (--no-leds)")
    else:
        device.all_leds(False)
        device.send_raw(card("LEDS", "watch the buttons"))
        lit, dark, unasked = [], [], []
        accepted = 0
        for name, number in LEDS.items():
            ok = device.set_led(name, True)
            accepted += 1 if ok else 0
            time.sleep(0.7)
            answer = ask(f"Is LED {name} (number {number}) lit?")
            device.set_led(name, False)
            if answer == "y":
                lit.append(name)
            elif answer == "n":
                dark.append(name)
            else:
                unasked.append(name)
                # Keep going regardless: even unwatched, every lamp gets driven,
                # so the protocol half of this section is still worth having.
        say(f"the device accepted {accepted}/{len(LEDS)} lamp commands without error")
        say(f"  lit by eye:   {', '.join(lit) or 'none reported'}")
        say(f"  dark by eye:  {', '.join(dark) or 'none reported'}")
        if unasked:
            say(f"  NOT OBSERVED: {', '.join(unasked)}")
        if lit and not dark:
            say("VERDICT: command 0x18 works — the LED decode is CONFIRMED on hardware")
        elif dark and not lit:
            say("VERDICT: the device accepts 0x18 but no lamp lit. Either the decode "
                "is wrong, or these panels have no backlight. Report either way.")
        elif lit and dark:
            say("VERDICT: partial — see which lamps are listed above; report it")
        else:
            say("VERDICT: NOT ESTABLISHED — nobody watched the panel. The device "
                "accepted every command, which says the protocol is valid, but "
                "whether the lamps physically lit is unanswered. Re-run this in a "
                "terminal, or use `fipx.py leds` and watch.")
        device.all_leds(False)

    # -- 6. buttons -------------------------------------------------------
    section("6. keys and knobs")
    try:
        from fipx.input import FipInput
        buttons = FipInput().start()
        if not buttons.available:
            say(f"keypad unavailable: {buttons.error}")
        else:
            device.send_raw(card("BUTTONS", "press keys, turn knobs"))
            say("watching for 12 seconds — press every soft key and turn both knobs")
            seen, t0 = [], time.time()
            while time.time() - t0 < 12:
                for event in buttons.poll():
                    seen.append(event)
                    print(f"    {event}", flush=True)
                time.sleep(0.05)
            LINES.extend(f"    {e}" for e in seen)
            say(f"{len(seen)} events, distinct: {sorted(set(seen))}")
        buttons.close()
    except Exception as exc:
        say(f"keypad test failed: {exc}")

    # -- 7. deinit + no-reset ---------------------------------------------
    section("7. deinit 0x13, and whether it removes the need for a USB reset")
    device.send_raw(card("DEINIT", "closing cleanly"))
    device.ep_out.write(header(CMD_DEINIT, running=1), 1000)
    try:
        reply = bytes(device.ep_in.read(ACK_LEN, 1000))[:HEADER_LEN]
        say(f"deinit reply: {reply.hex()}")
    except Exception as exc:
        say(f"deinit produced no reply ({exc}) — not necessarily wrong")
    device.close(deinit=False)          # already sent one by hand
    say("closed. Reopening WITHOUT the USB reset…")
    time.sleep(1.0)
    try:
        quick = FipDevice(reset=False).open()
        quick.send_raw(card("NO RESET", "reopened cleanly"))
        say("VERDICT: reopened with reset=False after a deinit — the reset is NOT "
            "needed after a clean shutdown. `--no-reset` saves a second per start.")
        answer = ask("Does the panel show 'NO RESET'?")
        if answer == "n":
            say("  !! it opened but is not drawing — report this")
        elif answer == "y":
            say("  confirmed by eye")
        quick.close()
    except Exception as exc:
        say(f"VERDICT: reopening without the reset FAILED ({exc}). The reset on open "
            "stays necessary; leave the default alone.")
        try:
            FipDevice(reset=True).open().close()
            say("  (a reset cleared it again, as expected)")
        except Exception as exc2:
            say(f"  !! and a reset did not clear it either: {exc2} — replug the panel")

    # -- done -------------------------------------------------------------
    section("summary")
    if UNANSWERED:
        say(f"!! INCOMPLETE — {len(UNANSWERED)} question(s) could not be asked, because")
        say("   this ran without a terminal. Everything the device reports for itself")
        say("   above is sound; anything needing eyes is marked NOT ASKED or NOT")
        say("   OBSERVED and must not be read as a failure. For the full report, run")
        say("   this from a terminal:   python3 tools/hwreport.py")
        for question in UNANSWERED:
            say(f"     - {question}")
    else:
        say("Complete: every question was answered.")
    say()
    say("Send this file to anyone working on a FIP driver. The four things nobody")
    say("has been able to answer without hardware are in sections 1, 3, 5 and 7.")
    with open(args.out, "w") as fh:
        fh.write("\n".join(LINES) + "\n")
    print(f"\nwritten to {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
