"""Shared printer access for epson-debug (CLI) and the Mac GUI.

make_printer() builds an EpsonPrinter (Wi-Fi/SNMP) or its USB variant with the
extra L-series models loaded; read_errors() and read_report() collect what the
tools display. All reads here are read-only.
"""
import json
import os
import pickle
from pathlib import Path

HERE = Path(__file__).resolve().parent
PICKLE = HERE / "data" / "extra_models.pickle"
FATAL_CODES = HERE / "data" / "fatal_codes.json"


def extra_conf():
    with PICKLE.open("rb") as f:
        return pickle.load(f)


def make_printer(model, ip=None, usb=False, timeout=None, retries=None):
    import epson_print_conf as epc
    cls = epc.EpsonPrinter
    if usb:
        from epson_usb_bridge import usb_printer
        cls = usb_printer(epc.EpsonPrinter)
    kw = dict(conf_dict=extra_conf(), model=model)
    if usb and os.environ.get("EPSON_DOCTOR_MOCK"):
        kw["backend"] = "mock"  # testing without a printer
    if not usb:
        kw.update(hostname=ip, timeout=timeout, retries=retries)
    return cls(**kw)


def fatal_code_meanings():
    try:
        return {k.upper().replace("0X", "0x"): v
                for k, v in json.loads(FATAL_CODES.read_text()).items()
                if not k.startswith("_")}
    except (OSError, ValueError):
        return {}


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception as e:  # keep going so one failed read doesn't hide the rest
        return e


def read_errors(p):
    """Current error state + last fatal error log.

    Returns dict with keys: status, ready, error, warnings, fatal (list of
    (hex_code, meaning)), fatal_supported (bool), problems (list of str).
    """
    out = {"problems": []}
    st = _safe(p.get_printer_status)
    if isinstance(st, dict):
        status = st.get("status")
        out["status"] = status[1] if isinstance(status, tuple) else status
        out["ready"] = st.get("ready")
        out["error"] = st.get("errcode")
        out["warnings"] = st.get("warning_code") or []
        out["ink_level"] = st.get("ink_level") or []
        out["serial"] = st.get("serial_number_info")
    else:
        out["problems"].append(f"Could not read printer status: {st or 'no answer'}")

    log = (p.parm or {}).get("fatal_log")
    out["fatal_supported"] = bool(log)
    out["fatal"] = []  # list of (kind, slot name, hex code, meaning)
    if log:
        names = fatal_code_meanings()
        failed = False
        for g in log:
            raw = _safe(p.read_eeprom_many, g["registers"], g["name"])
            if not isinstance(raw, list) or None in raw:
                failed = True
                break
            value = 0
            for b in raw:  # registers are listed most significant byte first
                value = (value << 8) + int(b, 16)
            width = 2 * len(g["registers"])
            code = "0x%0*X" % (width, value)
            empty = value == 0 or value == (1 << (8 * len(g["registers"]))) - 1
            meaning = "no error recorded" if empty else names.get(
                code, "see service manual")
            out["fatal"].append((g["kind"], g["name"], code, meaning))
        if failed:
            out["fatal"] = []
            out["problems"].append(
                "Could not read the fatal error log from EEPROM"
                " (no answer, or the firmware blocks EEPROM reads over Wi-Fi; try USB).")
    return out


def read_report(p):
    """Everything the GUI shows: errors + waste levels + counters."""
    rep = read_errors(p)
    w = _safe(p.get_waste_ink_levels)
    rep["waste"] = w if isinstance(w, dict) else None
    if not isinstance(w, dict):
        rep["problems"].append("Could not read waste ink counters from EEPROM.")
    fw = _safe(p.get_firmware_version)
    rep["firmware"] = fw if isinstance(fw, str) else None
    if not rep.get("serial"):
        sn = _safe(p.get_serial_number)
        rep["serial"] = sn if isinstance(sn, str) else None
    c = _safe(p.get_stats)
    rep["counters"] = c if isinstance(c, dict) else {}
    return rep


def format_errors(rep):
    lines = []
    if rep.get("status") is not None:
        lines.append(f"Status:   {rep['status']}")
    lines.append(f"Error:    {rep.get('error') or 'none'}")
    if rep.get("warnings"):
        lines.append("Warnings: " + "; ".join(rep["warnings"]))
    if rep["fatal_supported"]:
        if rep["fatal"]:
            lines.append("Fatal error log (#1 = most recent):")
            for kind, name, code, meaning in rep["fatal"]:
                lines.append(f"  {name:<36} {code:<8} {meaning}")
    else:
        lines.append("Fatal error log: not defined for this model")
    for pr in rep["problems"]:
        lines.append(f"! {pr}")
    return "\n".join(lines)
