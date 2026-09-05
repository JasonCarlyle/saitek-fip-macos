#!/usr/bin/env python3
"""Dump and parse the FIP's HID report descriptor via IOKit."""
import subprocess, re, sys
out = subprocess.run(["ioreg", "-r", "-c", "IOHIDDevice", "-l", "-w", "0"],
                     capture_output=True, text=True).stdout
blocks = out.split("+-o ")
hexdata = None
for b in blocks:
    if "0xa2ae" in b.lower() or '"ProductID" = 41646' in b:
        m = re.search(r'"ReportDescriptor" = <([0-9a-fA-F\s]+)>', b, re.S)
        print("--- device block keys ---")
        for k in ("Product", "VendorID", "ProductID", "PrimaryUsagePage", "PrimaryUsage", "MaxInputReportSize"):
            mm = re.search(rf'"{k}" = (.+)', b)
            if mm: print(f"  {k} = {mm.group(1).strip()}")
        if m: hexdata = re.sub(r"\s", "", m.group(1))
        break
if not hexdata:
    sys.exit("no report descriptor found")
data = bytes.fromhex(hexdata)
print(f"\nreport descriptor: {len(data)} bytes\n{data.hex()}\n")

ITEM = {0x04:"Usage Page",0x08:"Usage",0x14:"Logical Min",0x24:"Logical Max",
        0x34:"Physical Min",0x44:"Physical Max",0x54:"Unit Exp",0x64:"Unit",
        0x74:"Report Size",0x80:"Input",0x84:"Report ID",0x90:"Output",
        0x94:"Report Count",0xa0:"Collection",0xa4:"Push",0xb0:"Feature",
        0xc0:"End Collection",0x18:"Usage Min",0x28:"Usage Max"}
i = 0
while i < len(data):
    b = data[i]; size = b & 0x03; size = 4 if size == 3 else size
    tag = b & 0xFC
    val = int.from_bytes(data[i+1:i+1+size], "little") if size else None
    name = ITEM.get(tag, f"tag {tag:#04x}")
    print(f"  {name}" + (f" = {val} ({val:#x})" if val is not None else ""))
    i += 1 + size
