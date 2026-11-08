"""Run an upstream script (epson_print_conf.py or ui.py) with the macOS USB fix applied.

    python run_upstream.py epson_print_conf.py ARGS...
"""
import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import macos_usb_fix  # noqa: E402,F401  (patches epson_usb before the script imports it)

script = HERE / sys.argv[1]
sys.argv = [str(script)] + sys.argv[2:]
runpy.run_path(str(script), run_name="__main__")
