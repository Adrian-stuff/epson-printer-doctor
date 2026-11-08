#!/bin/zsh
# One-time setup: creates .venv with the Python packages, and checks libusb (for USB).
set -e
cd "${0:A:h}"
PY=${PYTHON:-python3}
"$PY" -c 'import sys, tkinter; assert sys.version_info >= (3, 9)' 2>/dev/null || {
  echo "Needs Python 3.9+ with Tk. Install it from https://www.python.org/downloads/macos/"; exit 1; }
"$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt
if ! { command -v brew >/dev/null && brew list libusb >/dev/null 2>&1; }; then
  echo "Note: for USB connections install libusb:  brew install libusb"
fi
chmod +x epson-debug "Epson Printer Doctor.app/Contents/MacOS/EpsonPrinterDoctor"
echo "Done. Open 'Epson Printer Doctor.app', or run ./epson-debug --help"
