"""Make epson-usb's D4 session work on macOS.

On macOS, libusb's bulk IN read on Epson printers (seen on an L3210) returns
zero-length packets immediately instead of waiting for data. epson_usb's
D4Session._fill() takes an empty read as "timed out", so every handshake
reply was missed and the printer looked like it "did not answer D4".

This patch keeps reading until the real timeout has elapsed. Import this
module before talking to a printer over USB; applying it twice is harmless.
"""
import time

try:
    from epson_usb import d4 as _d4
except ImportError:  # USB support not installed: nothing to patch
    _d4 = None


def _fill(self, timeout_ms: int) -> bool:
    deadline = time.monotonic() + max(timeout_ms, 1) / 1000.0
    while True:
        remaining = int((deadline - time.monotonic()) * 1000)
        if remaining <= 0:
            return False
        chunk = self.transport.read(self.timeouts.read_max, remaining)
        if chunk:
            self._trace("<raw", chunk)
            self._buf += chunk
            return True
        time.sleep(0.005)  # zero-length packet: the reply hasn't arrived yet


def apply():
    if _d4 is None:
        return
    for name in dir(_d4):
        cls = getattr(_d4, name)
        if isinstance(cls, type) and "_fill" in vars(cls) and not getattr(
                cls, "_macos_zlp_fix", False):
            cls._fill = _fill
            cls._macos_zlp_fix = True


apply()
