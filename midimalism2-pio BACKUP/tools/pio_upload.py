# PlatformIO extra script.
#
#   pio run -t upload      USB upload via the bootloader: SysEx kick F0 08 F7,
#                          then avrdude -c usbasp -D (tools/midimalism_upload.py)
#   pio run -t bootstrap   FIRST flash of a blank chip with a real USBasp
#                          programmer on the ISP pins: bootloader + sketch +
#                          fuses. hfuse 0x5D disables the reset pin, so after
#                          this, ISP needs a high-voltage programmer; all later
#                          updates go over USB.
from os.path import join

Import("env")

avrdude_dir = env.PioPlatform().get_package_dir("tool-avrdude") or ""
AVRDUDE = '"%s" -C "%s"' % (join(avrdude_dir, "bin", "avrdude"),
                            join(avrdude_dir, "avrdude.conf"))
TOOLS = join("$PROJECT_DIR", "tools")
FIRMWARE = join("$BUILD_DIR", "${PROGNAME}.hex")
BOOTSTRAP = join("$BUILD_DIR", "bootstrap.hex")

env.Replace(
    UPLOADCMD=" ".join([
        '"$PYTHONEXE"',
        '"%s"' % join(TOOLS, "midimalism_upload.py"),
        '--avrdude "%s"' % join(avrdude_dir, "bin", "avrdude"),
        '--conf "%s"' % join(avrdude_dir, "avrdude.conf"),
        "$UPLOAD_FLAGS",
        "$SOURCE",
    ])
)

env.AddCustomTarget(
    name="bootstrap",
    dependencies=FIRMWARE,
    actions=[
        '"$PYTHONEXE" "%s" "%s" "%s" "%s"' % (
            join(TOOLS, "make_bootstrap_image.py"), FIRMWARE,
            join(TOOLS, "bootloader", "teenyriot-usbtiny.hex"), BOOTSTRAP),
        # -B4: slow ISP clock for a fresh chip still on its 1 MHz default fuses
        AVRDUDE + ' -p attiny85 -c usbasp -B4 -e -U flash:w:"%s":i'
                  ' -U lfuse:w:0xE1:m -U hfuse:w:0x5D:m -U efuse:w:0xFE:m' % BOOTSTRAP,
    ],
    title="Bootstrap (USBasp programmer)",
    description="First flash of a blank chip: bootloader + sketch + fuses",
)
