# midimalism2 (PlatformIO)

PlatformIO copy of `../midimalism2` (left untouched as the Arduino IDE fallback).

    pio run                 # build
    pio run -t upload       # update over USB, no programmer (see below)
    pio run -t bootstrap    # FIRST flash of a blank chip with a USBasp programmer

## Updating over USB (how the CocoMake7 system works)

The fuses (hfuse `0x5D`) turn the reset pin into an I/O pin, so ISP is gone
after the first flash. Updates go through the **teenyriot-usbtiny** bootloader
(`tools/bootloader/`, a USBasp emulator living at `0x1500`):

1. After every reset the bootloader shows up as a **USBasp** (`16c0:05dc`) for
   about 2 s, then starts the sketch. If the sketch area fails its checksum,
   it stays in the bootloader.
2. The firmware restarts into the bootloader when it receives the SysEx
   **`F0 08 F7`** (watchdog reset, see `restartToBootloader()` in
   `include/midimalism.h`). This is the same "kick" CocoMake7's CocoMidi
   library uses.
3. `pio run -t upload` runs `tools/midimalism_upload.py`. It asks
   "Found Midimalism. Kick it into the bootloader? [Y/n]", sends the kick to
   the "Midimalism" (or old "TeenageUSB") MIDI port, waits for the bootloader
   and flashes it **itself over libusb, not with avrdude**: chip erase, one
   64-byte page per USB transfer with a 20 ms pause, read-back verify, then
   disconnect. The bootloader rewrites the vectors and checksum itself.
   Each page goes out as eight 8-byte transfers (one USB packet each), and
   after the disconnect the script checks that the sketch (1209:3003) comes
   back rather than the bootloader. Failed tries are redone (up to 5).
   avrdude doesn't work here: the bootloader commits each page with
   interrupts off and can't take a transfer that crosses a page boundary,
   but avrdude sends 200-byte blocks back to back (both avrdude 6.3 and
   CocoMake7's own fail; tested 24 Nov 2026). Needs Homebrew's `libusb`.
   **On Apple Silicon Macs, plug the Midimalism into a USB 2.0 hub.** On the
   built-in USB-C ports 25–40 % of the bootloader's transfers fail; through a
   hub none did.
4. **No kickable port?** That means the board has older firmware without the
   kick, or a crashed sketch. The script then asks you to unplug and replug,
   and catches the 2 s window. `upload_flags = --no-kick` skips the kick.

CocoMake7's patched avrdude did steps 3 and 4, but it only looked for a port
named "TeenageUSB". That's why the library used that name.

Don't have a real USBasp programmer plugged in while uploading this way. It
has the same USB ID as the bootloader.

## First flash of a blank chip (`pio run -t bootstrap`)

You need a USBasp programmer on the ISP pins. The target builds
`.pio/build/midimalism/bootstrap.hex` with `tools/make_bootstrap_image.py`,
then erases the chip, writes the image and sets the fuses
(`lfuse E1, hfuse 5D, efuse FE`).

The image can't just be the two hex files pasted together, which is what the
old `~/combined.hex` was. There, the reset vector pointed at the sketch, so the
bootloader **never ran**, and with the reset pin disabled it can't be reached
any more. The image builder reproduces the bootloader's own layout:

| Address  | Contents |
|----------|----------|
| `0x0000` | `rjmp 0x1500` (reset → bootloader) |
| `0x0004` | `rjmp 0x1504` (USB pin-change IRQ → bootloader, forwarded to the sketch) |
| `0x14FA` | checksum: bytes `0x0000..0x14FA` add up to 0 mod 256, else the bootloader stays active |
| `0x14FC` / `0x14FE` | the sketch's own reset / IRQ jumps |
| `0x1500` | bootloader |

So sketches are limited to **5370 bytes**. CocoMake7's boards.txt says 5375,
which would overlap the checksum byte. `make_bootstrap_image.py --selftest
<teenyriot-usbtiny-touchkeyboard.bin>` un-patches CocoMake7's official image
and rebuilds it byte for byte.

**Boards flashed with the old `combined.hex`:** their bootloader can't be
reached, and their reset pin is disabled. To bring them onto this system you
need a high-voltage (HVSP) programmer to re-flash them once.

## Why the device showed up as "TeenageUSB"

In the Arduino IDE build, V-USB's `usbdrv.c` is compiled as part of the
CocoMake7 board package's **CocoMidi** library. A quoted `#include "usbconfig.h"`
finds the file next to it first, so it uses the library's own
`CocoMidi/src/usbconfig.h` (`TeenageUSB` / `lifepatch.org`), never the sketch's
copy. That file is where the string descriptors are built. The sketch's
`usbconfig.h` only affected code compiled inside the sketch. On top of that, the
device descriptor in `midimalism.h` hard-coded VID/PID `16c0:05e4`, so the PID
in `usbconfig.h` was ignored as well.

Fixes in this copy:

- `lib/vusb` holds the V-USB driver **without** a `usbconfig.h`. The only one is
  `include/usbconfig.h`, so the driver and the sketch share the same settings.
- `include/midimalism.h`: the device descriptor uses `USB_CFG_VENDOR_ID`,
  `USB_CFG_DEVICE_ID` and `USB_CFG_DEVICE_VERSION`.
- `include/usbconfig.h`: VID/PID set to the pid.codes allocation `1209:3003`
  (pid.codes folder names are hex; the old `0x0BBB` was 3003 read as decimal).
- `usbconfig.h` no longer includes `<CocoMidiConfig.h>`. That include is what
  pulled in the CocoMidi library.

## Layout

- `src/main.cpp`: the sketch (`midimalism2.ino` + `#include <Arduino.h>`)
- `include/`: `midimalism.h`, `usbconfig.h`, `TeenyMidiConfig.h`
- `lib/teenyriot-core`: the CocoMake7 0.0.18 Arduino core, vendored so timing
  and pins match the IDE build
- `lib/vusb`: V-USB plus osccal (the clock is calibrated on every USB reset)
- `lib/EEPROM`: CocoMake7's EEPROM library
