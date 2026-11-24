#!/usr/bin/env python3
"""Upload a sketch to a Midimalism over USB, without a programmer.

How the CocoMake7 / teenyriot system works:
  * The teenyriot-usbtiny bootloader lives at 0x1500. After every reset it
    enumerates as a USBasp (16c0:05dc, "www.fischl.de") for ~2 s, then starts
    the sketch. Once a programmer request arrives it stays until the upload
    ends. The reset pin is disabled by the fuses (hfuse 0x5D), so the chip can
    only be reset by a power cycle (replug) or the watchdog.
  * The firmware listens for the SysEx F0 08 F7 ("kick") and restarts into the
    bootloader via the watchdog (restartToBootloader() in midimalism.h).
  * This script asks "Found Midimalism. Kick it into the bootloader? [Y/n]",
    sends the kick, then waits for the bootloader. With no Midimalism port (old
    firmware without the kick, a crashed sketch, or you said n) it asks you to
    replug and catches the bootloader's 2 s window instead.

Why it doesn't use avrdude: the bootloader commits each 64-byte flash page
with interrupts off, deaf to USB, and it can't take a transfer that crosses a
page boundary. avrdude sends 200-byte blocks back to back, so writes fail or
land in the wrong page (tested 24 Nov 2026: avrdude 6.3 and CocoMake7's own
avrdude both fail; this writer verifies 0 differences). So this script speaks
the USBasp protocol itself through libusb: chip erase (sketch area only, also
resets the bootloader's running checksum), each 64-byte page as 8-byte
transfers (one USB packet each: --chunk) with a pause after each page, read back and verify, then disconnect, which makes the bootloader
write its checksum and start the sketch.

Apple Silicon Macs: V-USB low-speed devices are unreliable on the built-in
USB-C ports (25-40 % of transfers failed). Plug the Midimalism into a USB 2.0
hub. Through a hub, 100 % of transfers worked.

Needs libusb-1.0 (Homebrew: brew install libusb) and, for the kick,
python-rtmidi (PlatformIO's Python has it). If libusb is missing it falls back
to avrdude, which is given with --avrdude/--conf.

Usage: midimalism_upload.py [--yes] [--no-kick] [--avrdude PATH --conf PATH] firmware.hex
"""

import argparse
import ctypes
import ctypes.util
import subprocess
import sys
import time

KICK = [0xF0, 0x08, 0xF7]
PORT_NAMES = ("midimalism", "teenageusb")
NOT_FOUND = "could not find USB device"

BOOT_VID, BOOT_PID = 0x16C0, 0x05DC
SKETCH_VID, SKETCH_PID = 0x1209, 0x3003     # include/usbconfig.h
PAGE = 64
SKETCH_MAX = 0x14FA                 # 5370: checksum byte + saved vectors above
PAGE_PAUSE = 0.02                   # s after each page (SPM with interrupts off)
ERASE_PAUSE = 0.8                   # s after chip erase (84 pages x 4.5 ms)
SETTLE = 0.5                        # s after the first request, before programming
ATTEMPTS = 5                        # whole-upload tries while the bootloader is up

# USBasp requests
CONNECT, DISCONNECT, TRANSMIT, READFLASH, ENABLEPROG, WRITEFLASH = 1, 2, 3, 4, 5, 6
BLOCK_FIRST, BLOCK_LAST = 1, 2
REQ_IN, REQ_OUT = 0xC0, 0x40        # vendor request, device recipient


# --------------------------------------------------------------- MIDI kick

def find_ports():
    """Return (rtmidi.MidiOut, [(index, name)]) for every Midimalism MIDI port."""
    try:
        import rtmidi
    except ImportError:
        print("python-rtmidi is not installed, so the kick can't be sent "
              "(pip install python-rtmidi).")
        return None, []
    out = rtmidi.MidiOut()
    return out, [(i, name) for i, name in enumerate(out.get_ports())
                 if any(n in name.lower() for n in PORT_NAMES)]


def ask(question):
    """Ask a Y/n question on the terminal. No terminal: answer yes."""
    try:
        with open("/dev/tty", "r+") as tty:
            tty.write(question + " [Y/n] ")
            tty.flush()
            return tty.readline().strip().lower() not in ("n", "no")
    except OSError:
        return True


def send_kick(confirm=True):
    """Send the kick SysEx to every matching MIDI port. Returns the port names."""
    out, ports = find_ports()
    if not ports:
        return []
    names = ", ".join(name for _, name in ports)
    if confirm and not ask("Found %s. Kick it into the bootloader?" % names):
        print("Not kicked.")
        return []
    kicked = []
    for i, name in ports:
        out.open_port(i)
        out.send_message(KICK)
        time.sleep(0.05)
        out.close_port()
        kicked.append(name)
    del out
    return kicked


# --------------------------------------------------------------- hex file

def load_hex(path):
    """Intel hex -> bytes from address 0, padded with 0xFF to whole pages."""
    mem, base = {}, 0
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line.startswith(":"):
                continue
            b = bytes.fromhex(line[1:])
            n, addr, typ = b[0], (b[1] << 8) | b[2], b[3]
            if typ == 0:
                for i in range(n):
                    mem[base + addr + i] = b[4 + i]
            elif typ == 2:
                base = ((b[4] << 8) | b[5]) << 4
            elif typ == 4:
                base = ((b[4] << 8) | b[5]) << 16
    if not mem:
        raise SystemExit("%s holds no data" % path)
    top = max(mem) + 1
    if top > SKETCH_MAX:
        raise SystemExit("Sketch is %d bytes, the bootloader leaves room for %d."
                         % (top, SKETCH_MAX))
    top = (top + PAGE - 1) // PAGE * PAGE
    return bytes(mem.get(i, 0xFF) for i in range(top))


# --------------------------------------------------------------- libusb

def load_libusb():
    names = [ctypes.util.find_library("usb-1.0"),
             "/opt/homebrew/lib/libusb-1.0.0.dylib",
             "/usr/local/lib/libusb-1.0.0.dylib",
             "libusb-1.0.so.0"]
    for name in names:
        if not name:
            continue
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            continue
        lib.libusb_init.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        lib.libusb_open_device_with_vid_pid.restype = ctypes.c_void_p
        lib.libusb_open_device_with_vid_pid.argtypes = [
            ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16]
        lib.libusb_control_transfer.argtypes = [
            ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8, ctypes.c_uint16,
            ctypes.c_uint16, ctypes.c_char_p, ctypes.c_uint16, ctypes.c_uint]
        lib.libusb_close.argtypes = [ctypes.c_void_p]
        lib.libusb_exit.argtypes = [ctypes.c_void_p]
        return lib
    return None


class UploadError(Exception):
    pass


class Bootloader:
    chunk = 8           # bytes per write transfer (set from --chunk)
    chunk_pause = 0.0   # s between write transfers (set from --chunk-pause)

    def __init__(self, lib, ctx, handle):
        self.lib, self.ctx, self.h = lib, ctx, handle

    def ctrl(self, rtype, req, value, index, data_or_len, tries=1):
        for attempt in range(tries):
            if isinstance(data_or_len, int):
                n = data_or_len
                buf = ctypes.create_string_buffer(n)
            else:
                n = len(data_or_len)
                buf = ctypes.create_string_buffer(bytes(data_or_len), n)
            r = self.lib.libusb_control_transfer(self.h, rtype, req, value,
                                                 index, buf, n, 1000)
            if r >= 0:
                return buf.raw[:r]
            time.sleep(0.05)
        raise UploadError("USB request %d (address 0x%04x) failed, libusb "
                          "error %d" % (req, value, r))

    def read(self, req, value=0, index=0, n=4):
        # reads change nothing on the chip, so they can be retried
        return self.ctrl(REQ_IN, req, value, index, n, tries=5)

    def close(self):
        self.lib.libusb_close(self.h)
        self.lib.libusb_exit(self.ctx)


def wait_for_bootloader(lib, timeout, kicked):
    """Poll for 16c0:05dc. Returns a Bootloader or None."""
    deadline = time.time() + timeout
    warned = False
    while time.time() < deadline:
        ctx = ctypes.c_void_p()
        if lib.libusb_init(ctypes.byref(ctx)) == 0:
            h = lib.libusb_open_device_with_vid_pid(ctx, BOOT_VID, BOOT_PID)
            if h:
                time.sleep(0.2)     # let it finish enumerating
                return Bootloader(lib, ctx, h)
            lib.libusb_exit(ctx)
        if kicked and not warned and time.time() > deadline - timeout + 3:
            print("Still no bootloader after the kick. If the firmware has no "
                  "kick handler, unplug and replug the Midimalism now.")
            sys.stdout.flush()
            warned = True
        time.sleep(0.1)
    return None


def flash(bl, img):
    # The first request makes the bootloader stay (no more 2 s timeout), so
    # send it right away, then give the USB link a moment to settle. Right
    # after a kick the host has just swapped the MIDI device for this one.
    bl.read(CONNECT)
    time.sleep(SETTLE)
    if bl.read(ENABLEPROG)[:1] != b"\x00":
        raise UploadError("The bootloader refused to enter programming mode.")
    sig = bytes(bl.read(TRANSMIT, 0x0030, i)[3] for i in range(3))
    print("Bootloader found, signature %s%s" % (
        sig.hex(), " (ATtiny85)" if sig == b"\x1e\x93\x0b" else ""))
    if sig != b"\x1e\x93\x0b":
        raise UploadError("That's not an ATtiny85, stopping.")

    # Chip erase: clears 0x0000-0x14FF only (never the bootloader) and resets
    # the bootloader's running checksum, so a retry in the same session is fine.
    bl.read(TRANSMIT, 0x80AC, 0x0000)
    time.sleep(ERASE_PAUSE)

    pages = len(img) // PAGE
    t0 = time.time()
    for p in range(pages):
        # A page goes out as PAGE/chunk small transfers. With chunk 8, each is
        # a single low-speed data packet: on direct (no hub) USB-C ports,
        # multi-packet OUT transfers to this bootloader always failed, single
        # packets got through. The bootloader commits the page once its last
        # word has arrived, then is deaf to USB for a few ms: hence the pause.
        for a in range(p * PAGE, (p + 1) * PAGE, bl.chunk):
            last = a + bl.chunk >= len(img)
            flags = (BLOCK_FIRST if a == 0 else 0) | (BLOCK_LAST if last else 0)
            # Retried on error. If the chip did get the data, the resend writes
            # the same bytes again (harmless) but counts them twice in the
            # bootloader's checksum. Then the bootloader won't start the
            # sketch; booted_ok() notices that and the whole try is redone.
            bl.ctrl(REQ_OUT, WRITEFLASH, a, (flags << 8) | PAGE,
                    img[a:a + bl.chunk], tries=3)
            if bl.chunk_pause:
                time.sleep(bl.chunk_pause)
        time.sleep(PAGE_PAUSE)
        sys.stdout.write("\rWriting  %3d%%" % ((p + 1) * 100 // pages))
        sys.stdout.flush()
    print("  %d bytes, %.1f s" % (len(img), time.time() - t0))

    back = b"".join(bl.read(READFLASH, a, 0, PAGE) for a in range(0, len(img), PAGE))
    bad = [i for i in range(len(img)) if back[i] != img[i]]
    if bad:
        raise UploadError("Verify failed: %d bytes differ, first at 0x%04x."
                          % (len(bad), bad[0]))
    print("Verified %d bytes." % len(img))

    # Disconnect: the bootloader writes its checksum and starts the sketch.
    bl.read(DISCONNECT)


def booted_ok(lib, wait=6.0):
    """After DISCONNECT: True once the sketch (1209:3003) enumerates, False if
    the bootloader comes back instead (checksum rejected), None if neither."""
    time.sleep(0.5)
    deadline = time.time() + wait
    seen_boot = 0
    while time.time() < deadline:
        ctx = ctypes.c_void_p()
        if lib.libusb_init(ctypes.byref(ctx)) == 0:
            found = {}
            for vid, pid in ((SKETCH_VID, SKETCH_PID), (BOOT_VID, BOOT_PID)):
                h = lib.libusb_open_device_with_vid_pid(ctx, vid, pid)
                found[vid] = bool(h)
                if h:
                    lib.libusb_close(h)
            lib.libusb_exit(ctx)
            if found[SKETCH_VID]:
                return True
            # a good sketch starts at once, so a bootloader still there after
            # ~2 s is one that rejected the checksum
            seen_boot = seen_boot + 1 if found[BOOT_VID] else 0
            if seen_boot >= 10:
                return False
        time.sleep(0.2)
    return None


def upload_libusb(lib, args, img, kicked):
    bl = wait_for_bootloader(lib, args.timeout, kicked)
    if not bl:
        print("The bootloader never showed up. Check the USB cable (on Apple "
              "Silicon Macs use a USB 2.0 hub), run the upload again and replug "
              "the Midimalism when asked. Chips flashed by the old uploader "
              "(pasted-together hex) can't be updated over USB at all.")
        return 1
    # The bootloader stays active while the sketch is incomplete or its
    # checksum is wrong, and every try starts with a chip erase (which also
    # resets the bootloader's checksum), so a failed try can simply be redone.
    for attempt in range(1, ATTEMPTS + 1):
        try:
            flash(bl, img)
            bl.close()
            ok = booted_ok(lib)
            if ok is None:
                print("Upload written and verified. Couldn't see which device "
                      "came back (a sketch with another USB ID?), check it.")
                return 0
            if ok:
                print("Upload OK%s. The Midimalism is running the new sketch."
                      % (" (try %d)" % attempt if attempt > 1 else ""))
                return 0
            raise UploadError("written and verified, but the bootloader's "
                              "checksum came out wrong (a write was resent)")
        except UploadError as e:
            print("\nTry %d/%d failed: %s" % (attempt, ATTEMPTS, e))
            sys.stdout.flush()
            bl.close()
        if attempt == ATTEMPTS:
            break
        time.sleep(0.5)
        bl = wait_for_bootloader(lib, 10, False)
        if not bl:
            print("The bootloader dropped off the bus and didn't come back.")
            break
    print("FAILED. Unplug and replug the Midimalism, then run the upload again: "
          "the bootloader keeps waiting until a sketch is written completely. "
          "If it keeps failing, plug it into a USB 2.0 hub (V-USB timing is "
          "marginal on some USB-C ports).")
    return 1


# --------------------------------------------------------------- avrdude fallback

def upload_avrdude(args, kicked):
    if not args.conf:
        print("libusb-1.0 not found (brew install libusb), and no --avrdude/--conf "
              "given to fall back on.")
        return 1
    print("libusb-1.0 not found (brew install libusb), falling back to avrdude. "
          "Expect write errors: see the comment at the top of this script.")
    cmd = [args.avrdude, "-C", args.conf, "-p", "attiny85", "-c", "usbasp",
           "-D", "-U", "flash:w:%s:i" % args.hexfile]
    deadline = time.time() + args.timeout
    while True:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True)
        if NOT_FOUND not in p.stdout:
            print(" ".join(cmd))
            print(p.stdout)
            return p.returncode
        if time.time() > deadline:
            print(p.stdout)
            print("The bootloader never showed up.")
            return 1
        time.sleep(0.25)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hexfile")
    ap.add_argument("--avrdude", default="avrdude",
                    help="only used if libusb-1.0 can't be loaded")
    ap.add_argument("--conf", help="avrdude.conf, for the avrdude fallback")
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="seconds to wait for the bootloader (default 30)")
    ap.add_argument("--no-kick", action="store_true",
                    help="don't send the SysEx, just wait for a replug")
    ap.add_argument("--yes", action="store_true",
                    help="kick without asking first")
    ap.add_argument("--chunk", type=int, default=8, choices=(8, 16, 32, 64),
                    help="bytes per USB write transfer (default 8 = one packet)")
    ap.add_argument("--chunk-pause", type=float, default=0.0,
                    help="extra seconds between write transfers (default 0)")
    args = ap.parse_args()
    Bootloader.chunk, Bootloader.chunk_pause = args.chunk, args.chunk_pause

    img = load_hex(args.hexfile)
    lib = load_libusb()

    kicked = [] if args.no_kick else send_kick(confirm=not args.yes)
    if kicked:
        print("Sent bootloader kick (F0 08 F7) to: " + ", ".join(kicked))
    else:
        print("No kick sent (no Midimalism MIDI port, or you said no).")
        print(">>> Unplug the Midimalism and plug it back in now. "
              "Waiting %d s for the bootloader..." % args.timeout)
    sys.stdout.flush()

    if lib:
        return upload_libusb(lib, args, img, kicked)
    return upload_avrdude(args, kicked)


if __name__ == "__main__":
    sys.exit(main())
