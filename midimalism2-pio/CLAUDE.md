# Midimalism 2: PlatformIO firmware

ATtiny85 USB-MIDI controller (2 knobs + 2 buttons, settings changeable over SysEx, stored in
EEPROM). It runs V-USB at 16.5 MHz on the internal oscillator, using the CocoMake7
"teenyriot" core and bootloader. README.md explains the bootloader / upload system in
detail. Read it before touching anything USB-, bootloader- or upload-related.

## Related folders
- `../midimalism2/`: original Arduino IDE sketch. **Kept untouched as a fallback, don't
  edit it.** It enumerates as "TeenageUSB" 16c0:05e4 and has no bootloader kick (see below).
- `~/Documents/code/midimalismUploader/`: double-click `.command` scripts.
  `Upload MIDIMALISM.command` batch-programs fresh chips with a USBasp: bootloader +
  sketch + fuses. `Update MIDIMALISM over USB.command` batch-updates programmed devices.
  It uses copies of `tools/*.py`, the bootloader hex and `firmware/midimalism.hex`, all
  refreshed by the VS Code task "Midimalism: Export firmware to midimalismUploader".
  **After changing anything in `tools/`, run that export** so the copies stay in sync.
  Its `previous-version/` holds the broken pre-23-Nov-2026 uploader.
- `~/Documents/code/MidimalismSynth/`: browser synth (`index.html`, Web MIDI). Its
  autodetect matches port names containing "Midimalism" or "TeenageUSB".
- Git repo: `~/Documents/code/Midimalism` (the parent folder). This folder isn't committed yet.

## How it's built
- No PlatformIO `framework`. The core (`lib/teenyriot-core`, from the CocoMake7 0.0.18
  board package), V-USB (`lib/vusb`, from CocoMidi) and EEPROM are vendored.
- `include/usbconfig.h` is the **only** usbconfig.h, on purpose. `lib/vusb` must never get
  its own copy (`-I include` in platformio.ini makes the project's copy visible). In the
  Arduino build the library's copy won, which is why devices said "TeenageUSB".
- USB ID: pid.codes **VID 0x1209 / PID 0x3003** "Midimalism" (pid.codes folder names are
  hex). Name "Midimalism", vendor "Cpt.Credible". The device descriptor in
  `include/midimalism.h` takes these from usbconfig.h macros. Never hard-code them there.
- `src/main.cpp` = the sketch (formerly `midimalism2.ino`). Its header comment documents
  the pins, the SysEx config protocol (`F0 7D cmd idx val F7`) and the EEPROM layout.

## Bootloader facts (reverse-engineered, see README)
- The teenyriot-usbtiny bootloader sits at 0x1500 and enumerates as USBasp 16c0:05dc for
  ~2 s after every reset. Fuses E1/5D/FE: **hfuse 5D disables the reset pin**, so after the
  first flash the only ways in are a replug or the watchdog.
- Firmware kick: SysEx `F0 08 F7` → `restartToBootloader()` in `include/midimalism.h`.
- Flash layout: 0x0000 → `rjmp 0x1500`, 0x0004 → `rjmp 0x1504`, checksum byte at 0x14FA
  (bytes 0..0x14FA add up to 0 mod 256), saved sketch vectors at 0x14FC/0x14FE.
  **Sketch max 5370 bytes.** `tools/make_bootstrap_image.py` builds this layout (run
  `--selftest` against CocoMake7's `teenyriot-usbtiny-touchkeyboard.bin`). Pasting the
  hex files together does NOT work.
- Chips flashed by the old uploader or `~/combined.hex` have the reset vector on the
  sketch, so their bootloader never runs. They can't be updated over USB, only with a
  high-voltage programmer.

## Commands (also VS Code tasks in `.vscode/tasks.json`)
PlatformIO isn't on PATH. Use `~/.platformio/penv/bin/pio`.
- `pio run`: build
- `pio run -t upload`: kick + flash over USB (`tools/pio_upload.py` → `tools/midimalism_upload.py`)
- `pio run -t bootstrap`: first flash with a USBasp programmer, **writes fuses**. Never
  run this, or any avrdude fuse write, without the user explicitly asking: it's irreversible
  without HV programming.

## Status (24 Nov 2026)
Builds cleanly at 4374 / 5370 bytes. Two chips on hardware:
- **Chip A, stock teenyriot bootloader:** bootstrapped with `pio run -t bootstrap`. Uploads
  through a USB 2.0 hub worked 5/5. It hasn't been tested with the new kick (direct jump,
  see below) or the link check yet.
- **Chip B, patched bootloader `tools/bootloader/teenyriot-midimalism/`** (the first build,
  WITHOUT the MCUSR fix): directly on Apple Silicon USB-C, 8/8 uploads OK with the current
  uploader, 7 of them kicked.

Findings:
- USB upload uses the libusb page writer in `tools/midimalism_upload.py`, **not avrdude**
  (avrdude's 200-byte blocks break this bootloader, see README). It needs Homebrew libusb.
  Pages go out as 8-byte transfers (multi-packet OUT transfers failed on direct ports).
- **Oscillator calibration varies per USB bus reset.** On direct USB-C some calibrations lose
  5-15 % of packets, others none. The uploader tests 100 reads first and resets the bus
  (= recalibrates) until the link is clean. That's what made direct USB-C work. Through a
  hub every calibration was clean.
- The kick (`restartToBootloader()`) now jumps to 0x1500 instead of a watchdog reset. A
  watchdog reset leaves WDRF set, which forces the watchdog on, and upstream
  USBaspLoader-tiny85 doesn't clear MCUSR. The stock teenyriot binary does. Chip B has
  that bug: a watchdog kick sends it into a 16 ms reset loop until a power cycle.
- Patched bootloader = upstream USBaspLoader-tiny85 (= teenyriot's source: pins D- PB5,
  D+ PB3, 2 s) + (1) wait for the USB transfer end instead of a fixed 2 ms before page
  writes, (2) USB ISR dispatch via GPIOR0 bit 0 instead of the RAM magic word (2-3 cycles
  instead of 10-16), (3) MCUSR = 0 (added after chip B; this build is untested). The
  bootstrap target and the uploader still use the STOCK bootloader.
- Fused chips could only get a new bootloader via an "upgrader" sketch (ATtiny85 has no
  boot section). That's risky: a power cut mid-write needs HV programming.
