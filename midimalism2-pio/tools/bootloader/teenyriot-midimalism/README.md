# teenyriot-midimalism bootloader

A patched rebuild of the CocoMake7 "teenyriot-usbtiny" bootloader.

teenyriot is [USBaspLoader-tiny85](https://github.com/embedded-creations/USBaspLoader-tiny85)
(commit in `upstream-commit.txt`), configured for D- on PB5, D+ on PB3 and a 2 s timeout.
The stock teenyriot binary also clears MCUSR. Rebuilt with avr-gcc 7.3 (PlatformIO's
toolchain), it comes out at 2710 bytes, against the stock binary's 2728.

## Patches (`*.patch` are diffs against upstream; `src/` is the patched tree)

1. **Page writes wait for the USB transfer to end** (`waitForUsbTransferEnd()` in `main.c`).
   Upstream waits a fixed 2 ms before the flash write. The write switches interrupts off
   (4.5–9 ms), so if the host hadn't finished the transfer by then, it failed. Now the
   bootloader waits until V-USB has handed the last packet to the host (`usbTxLen ==
   USBPID_NAK`), at most 20 ms, then waits another 0.5 ms. The chip erase works the same way.
2. **Faster USB interrupt dispatch** (`usbdrvasm165.inc`). Upstream tells the bootloader's
   interrupts from the sketch's by checking a magic word at the bottom of the stack. That
   cost about 10 cycles (bootloader) or 16 (sketch) before V-USB syncs to the packet, where
   1 bit is 11 cycles. Now `GPIOR0` bit 0 is set while the bootloader runs:
   `sbis GPIOR0,0 / rjmp app` costs 2 or 3 cycles. The sketch must not use GPIOR0.
3. **`MCUSR = 0` before `wdt_disable()`**: without it, a watchdog reset leaves the watchdog
   forced on and the bootloader resets every 16 ms until the power is cycled. The stock
   teenyriot binary has this line.

## Status (24 Nov 2026)

One chip runs patches 1 and 2 (without 3). Its bootloader and sketch work, and directly on
an Apple Silicon USB-C port 8/8 uploads succeeded with the current uploader. The build with
patch 3 (`teenyriot-midimalism.hex`) hasn't been on a chip yet. `bootstrap-test-24nov.hex`
is the image that went on that first chip (without patch 3). Don't use it for new chips.

Build: `cd src && PATH=~/.platformio/packages/toolchain-atmelavr/bin:$PATH make main.hex`.
The Makefile doesn't track the `.inc` files, so run `make clean` first after editing them.

License: GPL v2 (`License.txt`), like upstream.
