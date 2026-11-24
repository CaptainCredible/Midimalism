#!/usr/bin/env python3
"""Upload a sketch to a Midimalism over USB, without a programmer.

How the CocoMake7 / teenyriot system works:
  * The teenyriot-usbtiny bootloader lives at 0x1500. After every reset it
    enumerates as a USBasp (16c0:05dc, "www.fischl.de") for ~2 s, then starts
    the sketch. The reset pin is disabled by the fuses (hfuse 0x5D), so the
    chip can only be reset by a power cycle (replug) or the watchdog.
  * The firmware listens for the SysEx F0 08 F7 ("kick") and restarts into the
    bootloader via the watchdog (restartToBootloader() in midimalism.h).
  * This script sends the kick to the Midimalism's MIDI port, then keeps
    trying avrdude -c usbasp until the bootloader answers. If no Midimalism
    port is found (old firmware without the kick, or a crashed sketch), it asks
    you to replug the device and catches the bootloader's 2 s window instead.

CocoMake7's own "hacked" avrdude did the same, but only looked for a MIDI port
named "TeenageUSB".

Usage: midimalism_upload.py --avrdude PATH --conf PATH firmware.hex
"""

import argparse
import subprocess
import sys
import time

KICK = [0xF0, 0x08, 0xF7]
PORT_NAMES = ("midimalism", "teenageusb")
NOT_FOUND = "could not find USB device"


def send_kick():
    """Send the kick SysEx to every matching MIDI port. Returns the port names."""
    try:
        import rtmidi
    except ImportError:
        print("python-rtmidi is not installed, so the kick can't be sent "
              "(pip install python-rtmidi).")
        return []
    out = rtmidi.MidiOut()
    kicked = []
    for i, name in enumerate(out.get_ports()):
        if any(n in name.lower() for n in PORT_NAMES):
            out.open_port(i)
            out.send_message(KICK)
            time.sleep(0.05)
            out.close_port()
            kicked.append(name)
    del out
    return kicked


def run_avrdude(args, hexfile):
    cmd = [args.avrdude, "-C", args.conf, "-p", "attiny85", "-c", "usbasp",
           "-D",  # no chip erase: the bootloader must survive
           "-U", "flash:w:%s:i" % hexfile]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True)
    return p.returncode, p.stdout, cmd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hexfile")
    ap.add_argument("--avrdude", default="avrdude")
    ap.add_argument("--conf", required=True, help="avrdude.conf")
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="seconds to wait for the bootloader (default 30)")
    ap.add_argument("--no-kick", action="store_true",
                    help="don't send the SysEx, just wait for a replug")
    args = ap.parse_args()

    kicked = [] if args.no_kick else send_kick()
    if kicked:
        print("Sent bootloader kick (F0 08 F7) to: " + ", ".join(kicked))
    else:
        print("No Midimalism MIDI port found to kick.")
        print(">>> Unplug the Midimalism and plug it back in now. "
              "Waiting %d s for the bootloader..." % args.timeout)
    sys.stdout.flush()

    deadline = time.time() + args.timeout
    attempts = 0
    while True:
        attempts += 1
        rc, output, cmd = run_avrdude(args, args.hexfile)
        if NOT_FOUND not in output:
            print(" ".join(cmd))
            print(output)
            if rc == 0:
                print("Upload OK (bootloader found after %d tries). The sketch "
                      "starts when the bootloader times out (~2 s)." % attempts)
            return rc
        if time.time() > deadline:
            print(output)
            print("The bootloader never showed up. Check the USB cable, run the "
                  "upload again and replug the Midimalism when asked. Chips "
                  "flashed by the old uploader (pasted-together hex) can't be "
                  "updated over USB at all.")
            return 1
        if attempts == 12 and kicked:
            print("Still no bootloader after the kick. If the firmware has no "
                  "kick handler, unplug and replug the Midimalism now.")
            sys.stdout.flush()
        time.sleep(0.25)


if __name__ == "__main__":
    sys.exit(main())
