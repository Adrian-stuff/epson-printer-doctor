# Epson Printer Doctor

A native macOS tool for diagnosing Epson EcoTank and L-series printers. It reads the
status, current error, fatal error log, ink levels, waste ink pad levels and counters,
and can reset the waste ink counters. It works over **Wi-Fi** or **USB**, with no
Windows or Wine needed.

It's built on [epson_print_conf](https://github.com/Ircama/epson_print_conf) and adds
EEPROM definitions for **62 L-series models** (plus regional variants) generated from
[ez-reset](https://github.com/CiRIP/ez-reset)'s device list, including:

L1110 · L1210 · L1250 · L3110 · L3150 · L3160 · L3210 · L3250 · L3251 · L3260 ·
L4150 · L4160 · L5190 · L5290 · L6170 · L6190 … (`./epson-debug models` lists them all)

## Install

Needs macOS 11+ and Python 3.9+ ([python.org](https://www.python.org/downloads/macos/)
installer, which includes Tk).

```bash
git clone https://github.com/Adrian-stuff/epson-printer-doctor.git
cd epson-printer-doctor
./setup.sh
brew install libusb   # only needed for USB connections
```

## The app

Double-click **Epson Printer Doctor.app** (keep it inside the repo folder). Click
**Detect**, then **Read Printer**.

- Status, current error and warnings, firmware and serial number
- Ink levels and waste pad levels (orange at 85%, red when full)
- Fatal error log, counters and an activity log
- **Reset Waste Counters…** and **Temporary Reset…** (both ask for confirmation)
- **Save Report…** writes everything to a text file
- **Advanced Tool** opens the full epson_print_conf window (EEPROM read and write, and more)

## Command line

```
./epson-debug find              # look for printers on USB and the network
./epson-debug models L32        # list supported models
./epson-debug status            # full read-only report (auto-detects the printer)
./epson-debug errors            # current error + fatal error log
./epson-debug waste             # waste ink pad levels
./epson-debug reset-waste       # reset waste counters (asks for YES first)
./epson-debug temp-reset        # Epson's temporary reset, for firmware that locks the EEPROM
./epson-debug gui               # the app window
./epson-debug advanced          # full upstream epson_print_conf window
./epson-debug raw ARGS...       # any epson_print_conf.py option
```

Options: `-m MODEL`, `-a IP_ADDRESS`, `--usb`, `-d` (debug log), `--dry-run`.

## Error codes

- **Current error** is read live and decoded: paper jam, cover open, ink out, waste ink
  pad overflow (counter overflow), and so on.
- **Fatal error log**: the printer stores its last 5 printer, scanner and general fatal
  error codes in EEPROM (#1 is the most recent). Their meanings vary by model and come
  from Epson's service manuals, so add the ones you know to `data/fatal_codes.json`,
  keyed exactly as shown (e.g. `"0x0002": "..."`). Pull requests with verified code
  tables are welcome.

## Notes and warnings

- Newer firmware (for example the L3250 and some L3260 releases) blocks EEPROM access
  over Wi-Fi. Use a USB cable, or **Temporary Reset**.
- **Only reset waste counters after replacing the waste ink pad or fitting an external
  waste tank.** Otherwise ink can overflow inside the printer.
- Writing the EEPROM is at your own risk. Every write is read back to verify it.
- To regenerate model definitions after updating `data/devices.xml`, run
  `.venv/bin/python build_models.py`.

## License

AGPL-3.0. Bundled upstream files are EUPL-1.2 (see [NOTICE.md](NOTICE.md)). This
project is not affiliated with Epson.
