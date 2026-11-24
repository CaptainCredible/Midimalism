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
Builds cleanly at 4368 / 5370 bytes. **Nothing has been tested on hardware yet**: not the
new USB name/ID, not the SysEx kick, not the USB upload, and not the fixed uploader /
bootstrap image. The next step is to program one fresh chip with the uploader or the
bootstrap task, then update it over USB.
