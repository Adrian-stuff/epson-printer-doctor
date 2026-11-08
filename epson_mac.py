"""epson-debug: macOS front end for epson_print_conf with extra L-series models.

Adds the models from extra_models.py (L1110, L1210, L3110, L3210, L3250,
L3251, L3260, L5290 and the rest of the L-series) to the upstream library,
auto-detects the printer on Wi-Fi or USB, and wraps the common jobs.

    epson-debug find                  look for printers (network + USB)
    epson-debug models [FILTER]       list supported models
    epson-debug status   [opts]       full read-only report (ink, waste, counters, serial)
    epson-debug errors   [opts]       current error/warnings + last fatal error codes
    epson-debug waste    [opts]       waste ink pad levels only
    epson-debug reset-waste [opts]    reset waste ink counters (asks first, writes EEPROM)
    epson-debug temp-reset  [opts]    temporary waste reset (no EEPROM key needed)
    epson-debug gui                   open Epson Printer Doctor (simple window)
    epson-debug advanced              open the full upstream epson_print_conf window
    epson-debug raw ARGS...           pass ARGS straight to epson_print_conf.py

opts: -m MODEL  -a IP_ADDRESS  --usb  -d (debug)  --dry-run
Without -m / -a / --usb the printer is detected automatically.
"""
import os
import pickle
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable
PICKLE = HERE / "data" / "extra_models.pickle"


def build_pickle():
    """Write the extra models that upstream lacks to a pickle for -P."""
    from epson_print_conf import EpsonPrinter
    from extra_models import EXTRA_PRINTER_CONFIG

    builtin = EpsonPrinter().PRINTER_CONFIG
    known = set(builtin)
    for conf in builtin.values():
        known.update(conf.get("alias", []))
    extra = {}
    for name, conf in EXTRA_PRINTER_CONFIG.items():
        if name in known:
            # upstream's own definition wins; only add the fatal error log
            # addresses when upstream doesn't define them
            if name in builtin:
                add = {k: conf[k] for k in ("last_printer_fatal_errors", "fatal_log")
                       if conf.get(k) and not builtin[name].get(k)}
                if add:
                    extra[name] = add
            continue
        conf = dict(conf)
        if "alias" in conf:
            conf["alias"] = [a for a in conf["alias"] if a not in known]
            if not conf["alias"]:
                del conf["alias"]
        extra[name] = conf
    PICKLE.parent.mkdir(exist_ok=True)
    with PICKLE.open("wb") as f:
        pickle.dump(extra, f)


def ensure_pickle():
    srcs = [HERE / "extra_models.py", Path(__file__).resolve()]
    if not PICKLE.exists() or any(PICKLE.stat().st_mtime < s.stat().st_mtime
                                  for s in srcs):
        build_pickle()


def all_models():
    from epson_print_conf import EpsonPrinter
    with PICKLE.open("rb") as f:
        p = EpsonPrinter(conf_dict=pickle.load(f))
    names = {}
    for name, conf in p.PRINTER_CONFIG.items():
        names[name] = conf.get("alias", [])
    return names


def canonical_model(raw):
    """Map a reported name like 'EPSON L3150 Series' to a supported model."""
    if not raw:
        return None
    models = all_models()
    lookup = {}
    for name, aliases in models.items():
        lookup[name.upper()] = name
        for a in aliases:
            lookup[a.upper()] = a
    for token in re.findall(r"[A-Z]{1,3}-?\d{3,5}[A-Z]?", raw.upper()):
        if token in lookup:
            return lookup[token]
    return None


# ---------------------------------------------------------------- detection

EPSON_VID = 0x04B8


def usb_epson_devices():
    """Product names of Epson USB devices, e.g. ['L3210 Series'].

    Uses ioreg (present on every macOS; system_profiler's SPUSBDataType
    returns nothing on recent versions), falling back to libusb.
    """
    found = []
    try:
        out = subprocess.run(["ioreg", "-p", "IOUSB", "-l", "-w0"],
                             capture_output=True, text=True, timeout=15).stdout
        # each device is a "+-o Name@..." node followed by its properties
        for node in re.split(r"\+-o ", out):
            vid = re.search(r'"idVendor" = (\d+)', node)
            if vid and int(vid.group(1)) == EPSON_VID:
                name = re.search(r'"USB Product Name" = "([^"]*)"', node)
                found.append(name.group(1) if name else "EPSON device")
    except (OSError, subprocess.SubprocessError):
        pass
    if not found:
        try:
            import usb.core
            for d in usb.core.find(find_all=True, idVendor=EPSON_VID) or []:
                try:
                    found.append(d.product or "EPSON device")
                except Exception:
                    found.append("EPSON device")
        except Exception:
            pass
    return found


USB_BLOCKERS = ("Epson Printer Utility", "EPSON Scan", "Epson ScanSmart")


def usb_blockers():
    """Names of running Epson apps that hold the printer's USB interface."""
    try:
        out = subprocess.run(["ps", "-axo", "comm"], capture_output=True,
                             text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted({b for b in USB_BLOCKERS for line in out.splitlines()
                   if b.lower() in line.lower()})


USB_BLOCKER_HINT = ("{} is running and holds the printer's USB connection; "
                    "quit it, or USB reads will fail.")


def usb_model():
    """Supported model name of the first Epson printer on USB, if any."""
    for name in usb_epson_devices():
        m = canonical_model(name)
        if m:
            return m
    return None


def cups_printers():
    """(name, uri) for every Epson queue CUPS knows about."""
    out = subprocess.run(["lpstat", "-v"], capture_output=True, text=True).stdout
    res = []
    for line in out.splitlines():
        m = re.match(r"device for (\S+): (\S+)", line)
        if m and "EPSON" in (m.group(1) + m.group(2)).upper():
            res.append((m.group(1), m.group(2)))
    return res


def resolve_bonjour(uri):
    """dnssd://EPSON%20L3150%20Series._pdl-datastream._tcp.local./ -> IP."""
    from urllib.parse import unquote
    m = re.match(r"dnssd://([^/]+?)\._", uri)
    if not m:
        return None
    host = unquote(m.group(1)).replace(" ", "-") + ".local"
    try:
        import socket
        return socket.gethostbyname(host)
    except OSError:
        return None


def snmp_name(ip):
    from find_printers import PrinterScanner
    try:
        return PrinterScanner().get_printer_name(ip)
    except Exception:
        return None


def scan_network():
    from find_printers import PrinterScanner
    print("Scanning the local network for printers (SNMP)...", flush=True)
    return PrinterScanner().get_all_printers()


def autodetect(model, ip, usb):
    """Fill in whatever of model / ip / usb the user didn't give."""
    if not ip and not usb:
        devs = usb_epson_devices()
        if devs:
            usb = True
            print(f"Using the Epson printer connected by USB ({devs[0]}).")
        else:
            for name, uri in cups_printers():
                cand = None
                if uri.startswith("dnssd://"):
                    cand = resolve_bonjour(uri)
                else:
                    m = re.match(r"\w+://([\d.]+)", uri)
                    cand = m.group(1) if m else None
                if cand and snmp_name(cand):
                    ip = cand
                    model = model or canonical_model(name.replace("_", " "))
                    print(f"Using {name} at {ip} (from macOS printer list).")
                    break
            if not ip:
                found = scan_network()
                epsons = [p for p in found if "EPSON" in (p.get("name") or "").upper()]
                if epsons:
                    ip = epsons[0]["ip"]
                    model = model or canonical_model(epsons[0]["name"])
                    print(f"Found {epsons[0]['name']} at {ip}.")
    if usb and not model:
        model = usb_model()
    if usb:
        for b in usb_blockers():
            print("Warning: " + USB_BLOCKER_HINT.format(b))
    if ip and not model:
        model = canonical_model(snmp_name(ip) or "")
    if not ip and not usb:
        sys.exit("No Epson printer found. Turn it on, connect it by USB or Wi-Fi,"
                 " or pass -a IP_ADDRESS / --usb.")
    if not model:
        sys.exit("Could not tell the printer model. Pass it with -m, e.g. -m L3210"
                 " (see: epson-debug models).")
    return model, ip, usb


# ---------------------------------------------------------------- commands

def upstream(args):
    cmd = [PY, str(HERE / "run_upstream.py"), "epson_print_conf.py", "-P", str(PICKLE)] + args
    return subprocess.call(cmd, cwd=HERE)


def parse_opts(argv):
    model = ip = None
    usb = False
    rest = []
    it = iter(argv)
    for a in it:
        if a in ("-m", "--model"):
            model = next(it, None)
        elif a in ("-a", "--address"):
            ip = next(it, None)
        elif a == "--usb":
            usb = True
        else:
            rest.append(a)
    if model:
        model = canonical_model(model) or model.upper()
    return model, ip, usb, rest


def target_args(model, ip, usb):
    args = ["-m", model]
    args += ["--usb"] if usb else ["-a", ip]
    return args


def confirm(prompt):
    try:
        return input(prompt + " Type YES to continue: ").strip() == "YES"
    except EOFError:
        return False


def main():
    os.chdir(HERE)
    sys.path.insert(0, str(HERE))
    ensure_pickle()
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, argv = argv[0], argv[1:]

    if cmd == "find":
        usb = usb_epson_devices()
        print("USB:", ", ".join(usb) if usb else "no Epson printer on USB")
        for name, uri in cups_printers():
            print(f"macOS queue: {name}  {uri}")
        for p in scan_network():
            m = canonical_model(p.get("name") or "")
            tag = f"supported as {m}" if m else "model not in list"
            print(f"Network: {p['ip']}  {p.get('name')}  ({tag})")
        return 0

    if cmd == "models":
        flt = argv[0].upper() if argv else ""
        for name, aliases in sorted(all_models().items()):
            line = name + (f"  (also {', '.join(aliases)})" if aliases else "")
            if flt in line.upper():
                print(line)
        return 0

    if cmd == "gui":
        return subprocess.call([PY, str(HERE / "epson_gui.py")] + argv, cwd=HERE)
    if cmd == "advanced":
        return subprocess.call([PY, str(HERE / "run_upstream.py"), "ui.py", "-P", str(PICKLE)] + argv,
                               cwd=HERE)

    if cmd == "raw":
        return upstream(argv)

    model, ip, usb, rest = parse_opts(argv)
    model, ip, usb = autodetect(model, ip, usb)
    tgt = target_args(model, ip, usb)
    where = "USB" if usb else ip
    print(f"Printer: {model} via {where}\n", flush=True)

    if cmd == "status":
        return upstream(tgt + ["-i"] + rest)
    if cmd == "errors":
        from printer_core import open_printer, read_errors, format_errors
        with open_printer(model, ip, usb) as p:
            print(format_errors(read_errors(p)))
        return 0
    if cmd == "waste":
        return upstream(tgt + ["-q", "waste_ink_levels"] + rest)
    if cmd == "reset-waste":
        if "--dry-run" not in rest and not confirm(
                f"This writes the waste ink counters in the {model}'s EEPROM to 0."
                " Only do this if the waste pad has been replaced or an external"
                " waste tank is fitted, or ink can overflow."):
            print("Cancelled.")
            return 1
        return upstream(tgt + ["--reset_waste_ink"] + rest)
    if cmd == "temp-reset":
        if "--dry-run" not in rest and not confirm(
                "This sends Epson's temporary waste-reset command (rw)."):
            print("Cancelled.")
            return 1
        return upstream(tgt + ["--temp_reset_waste_ink"] + rest)

    print(f"Unknown command: {cmd}\n")
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
