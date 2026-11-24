#!/usr/bin/env python3
"""Merge the sketch with the teenyriot-usbtiny bootloader into one flash image,
laid out exactly as the bootloader itself lays it out after a USB upload.

Use it for the very first flash of a blank chip with a real USBasp programmer.
Simply concatenating the two hex files does NOT work: the reset vector would
point at the sketch, so the bootloader never runs (and can never run again
once hfuse 0x5D has disabled the reset pin).

Layout (ATtiny85, 8 KiB flash, reverse-engineered from the bootloader and
checked against CocoMake7's teenyriot-usbtiny-touchkeyboard.bin):

  0x0000  rjmp 0x1500   reset          -> bootloader
  0x0004  rjmp 0x1504   PCINT0 (USB)   -> bootloader, which forwards it to the
                                          sketch's handler unless it is active
  0x14FA  checksum      bytes 0x0000..0x14FA must add up to 0 (mod 256), or the
                        bootloader stays in bootloader mode instead of starting
                        the sketch
  0x14FB  0x00
  0x14FC  rjmp <sketch reset handler>
  0x14FE  rjmp <sketch PCINT0 handler>
  0x1500  bootloader

The sketch must therefore end below 0x14FA (5370 bytes).

Usage: make_bootstrap_image.py firmware.hex bootloader.hex out.hex
       make_bootstrap_image.py --selftest official-full-image.bin
"""

import sys

FLASH = 0x2000
BOOT = 0x1500
CHK = 0x14FA
SAVED_RESET = 0x14FC
SAVED_PCINT0 = 0x14FE
PCINT0_VEC = 0x0004
APP_MAX = CHK


def read_hex(path):
    mem = {}
    base = 0
    for line in open(path):
        line = line.strip()
        if not line.startswith(":"):
            continue
        b = bytes.fromhex(line[1:])
        n, addr, typ, data = b[0], (b[1] << 8) | b[2], b[3], b[4:4 + b[0]]
        if sum(b) & 0xFF:
            raise ValueError("bad checksum in %s: %s" % (path, line))
        if typ == 0:
            for i, v in enumerate(data):
                mem[base + addr + i] = v
        elif typ == 1:
            break
        elif typ == 2:
            base = ((data[0] << 8) | data[1]) << 4
        elif typ == 4:
            base = ((data[0] << 8) | data[1]) << 16
        # 3/5 (start address) are irrelevant for flashing
    return mem


def write_hex(img, path):
    with open(path, "w") as f:
        for a in range(0, len(img), 16):
            chunk = img[a:a + 16]
            if all(v == 0xFF for v in chunk):
                continue
            rec = bytes([len(chunk), a >> 8, a & 0xFF, 0]) + bytes(chunk)
            f.write(":%s%02X\n" % (rec.hex().upper(), (-sum(rec)) & 0xFF))
        f.write(":00000001FF\n")


def rjmp_target(img, at):
    w = img[at] | (img[at + 1] << 8)
    if w & 0xF000 != 0xC000:
        raise ValueError("word at 0x%04X is 0x%04X, not an rjmp" % (at, w))
    k = w & 0x0FFF
    if k & 0x800:
        k -= 0x1000
    return (at + 2 + 2 * k) % FLASH


def put_rjmp(img, at, target):
    k = ((target - at - 2) // 2) & 0x0FFF   # rjmp wraps around on 8 KiB parts
    w = 0xC000 | k
    img[at], img[at + 1] = w & 0xFF, w >> 8
    assert rjmp_target(img, at) == target


def build(app, boot):
    """app, boot: dict addr->byte. Returns a FLASH-sized bytearray."""
    if not app:
        raise ValueError("sketch is empty")
    top = max(app)
    if top >= APP_MAX:
        raise ValueError("sketch ends at 0x%04X, must stay below 0x%04X (%d bytes)"
                         % (top, APP_MAX, APP_MAX))
    if min(boot) < BOOT:
        raise ValueError("bootloader hex has data below 0x%04X" % BOOT)
    img = bytearray([0xFF] * FLASH)
    for a, v in app.items():
        img[a] = v
    for a, v in boot.items():
        img[a] = v
    return patch(img)


def patch(img):
    put_rjmp(img, SAVED_RESET, rjmp_target(img, 0x0000))
    put_rjmp(img, SAVED_PCINT0, rjmp_target(img, PCINT0_VEC))
    put_rjmp(img, 0x0000, BOOT)
    put_rjmp(img, PCINT0_VEC, BOOT + PCINT0_VEC)
    img[CHK + 1] = 0x00
    img[CHK] = (-sum(img[:CHK])) & 0xFF
    assert sum(img[:CHK + 1]) & 0xFF == 0
    return img


def selftest(official_bin):
    """Undo the bootloader's patching of an official image, redo it, compare."""
    ref = bytearray(open(official_bin, "rb").read())
    ref += b"\xFF" * (FLASH - len(ref))
    raw = bytearray(ref)
    put_rjmp(raw, 0x0000, rjmp_target(ref, SAVED_RESET))
    put_rjmp(raw, PCINT0_VEC, rjmp_target(ref, SAVED_PCINT0))
    for a in range(CHK, BOOT):
        raw[a] = 0xFF
    ok = patch(raw) == ref
    print("selftest:", "OK, rebuilt image is byte-identical" if ok else "MISMATCH")
    return 0 if ok else 1


def main(argv):
    if len(argv) == 3 and argv[1] == "--selftest":
        return selftest(argv[2])
    if len(argv) != 4:
        print(__doc__)
        return 2
    app, boot = read_hex(argv[1]), read_hex(argv[2])
    img = build(app, boot)
    write_hex(img, argv[3])
    print("bootstrap image: sketch %d bytes (max %d), reset -> 0x%04X via bootloader, "
          "checksum 0x%02X -> %s" % (max(app) + 1, APP_MAX,
                                     rjmp_target(img, SAVED_RESET), img[CHK], argv[3]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
