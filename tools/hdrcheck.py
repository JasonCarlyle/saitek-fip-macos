#!/usr/bin/env python3
"""Check the header builder against the packets actually seen on the wire.

fipx/device.py contains no captured bytes: it builds every header from the
field layout instead. That is only trustworthy if something proves the builder
still reproduces what the device was observed to accept, so the four 44-byte
constants live here, in the reverse-engineering tools, and this script asserts
the builder agrees with them. Run it after touching header().

No hardware needed.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fipx.device import (CMD_HANDSHAKE, CMD_IMAGE, FRAME_BYTES, HEADER_LEN,
                         W_ACK, W_COMMAND, W_INIT_STATUS, W_LENGTH, W_RUNNING,
                         header, read_word)

# Observed on a Saitek FIP 06a3:a2ae. Host -> device commands, and the status
# packets the device answered them with.
INIT     = bytes.fromhex("00000000000000000000000000000000000000000000000a0000000000000000000000000000000000000000")
INIT_ACK = bytes.fromhex("00000000000000000000000001000000000000000000000a0000000000000000000000000000000200000000")
IMG_HDR  = bytes.fromhex("0000000000000001000384000000000000000000000000060000000000000000000000000000000000000000")
IMG_ACK  = bytes.fromhex("0000000000000001000000000000000000000000000000060000000000000000000000000000000000000000")

FIELDS = {W_RUNNING: "running", W_LENGTH: "length", W_ACK: "ack",
          W_COMMAND: "command", W_INIT_STATUS: "init_status"}

failures = 0

def check(name, built, observed):
    global failures
    ok = built == observed
    print(f"{name:10s} {'OK' if ok else 'MISMATCH'}")
    if not ok:
        print(f"  built    {built.hex()}")
        print(f"  observed {observed.hex()}")
        failures += 1

check("handshake", header(CMD_HANDSHAKE), INIT)
check("image", header(CMD_IMAGE, FRAME_BYTES, running=1), IMG_HDR)

# Every non-zero word in all four packets must land on a field we have a name
# for. A stray value anywhere else would mean the layout is incomplete.
print("\nnon-zero words:")
for name, packet in (("INIT", INIT), ("INIT_ACK", INIT_ACK),
                     ("IMG_HDR", IMG_HDR), ("IMG_ACK", IMG_ACK)):
    assert len(packet) == HEADER_LEN, f"{name} is {len(packet)} bytes, not {HEADER_LEN}"
    for word in range(HEADER_LEN // 4):
        value = read_word(packet, word)
        if not value:
            continue
        field = FIELDS.get(word)
        print(f"  {name:9s} word {word:2d} (bytes {word*4:2d}-{word*4+3:2d}) "
              f"{field or '?????? UNNAMED':11s} = {value:#010x}")
        if field is None:
            failures += 1

print("\n" + ("all checks passed" if not failures else f"{failures} FAILED"))
sys.exit(1 if failures else 0)
