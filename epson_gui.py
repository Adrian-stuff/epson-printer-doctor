"""Epson Printer Doctor: a simple Mac window for epson-debug.

Detect the printer, then read its status, current error, ink levels, waste ink
pad levels, fatal error log and counters. Resets are behind confirmations.
"""
import datetime
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import epson_mac  # noqa: E402  (detection helpers, model list)
import printer_core  # noqa: E402

INK_COLOURS = {"Black": "#555555", "Cyan": "#00A9E0", "Magenta": "#D6248A",
               "Yellow": "#F5C400"}
WASTE_NAMES = {"main_waste": "Main pad", "borderless_waste": "Borderless pad",
               "third_waste": "Third pad", "first_waste": "First pad",
               "second_waste": "Second pad"}


class Bar(tk.Canvas):
    """A labelled horizontal level bar (ttk progress bars can't be coloured on macOS)."""

    def __init__(self, master, label, colour="#4a90d9", width=260):
        super().__init__(master, width=width + 220, height=24,
                         highlightthickness=0, bg="systemWindowBackgroundColor")
        self.label, self.colour, self.w = label, colour, width

    def set(self, pct, colour=None, note=""):
        self.delete("all")
        self.create_text(0, 12, text=self.label, anchor="w", font=("Helvetica", 12),
                         fill="systemTextColor")
        x0 = 110
        self.create_rectangle(x0, 4, x0 + self.w, 20, outline="#999", fill="#eee")
        if pct is not None:
            fill = max(0.0, min(100.0, float(pct)))
            self.create_rectangle(x0, 4, x0 + self.w * fill / 100, 20, width=0,
                                  fill=colour or self.colour)
            txt = f"{pct:.1f}%" + (f"  {note}" if note else "")
        else:
            txt = "n/a"
        self.create_text(x0 + self.w + 8, 12, text=txt, anchor="w",
                         font=("Helvetica", 12), fill="systemTextColor")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Epson Printer Doctor")
        self.minsize(760, 640)
        self.q = queue.Queue()
        self.busy = False
        self.report = None
        self.printer_args = None

        epson_mac.ensure_pickle()
        self.models = sorted(epson_mac.all_models())

        self._build()
        self.after(100, self._poll)
        self.log("Ready. Click Detect, or pick a model and connection, then Read Printer.")

    # ------------------------------------------------------------- layout
    def _build(self):
        pad = dict(padx=10, pady=4)
        top = ttk.LabelFrame(self, text="Printer")
        top.pack(fill="x", **pad)

        ttk.Label(top, text="Model").grid(row=0, column=0, sticky="w", padx=6)
        self.model = ttk.Combobox(top, values=self.models, width=12)
        self.model.grid(row=0, column=1, sticky="w")

        ttk.Label(top, text="Connection").grid(row=0, column=2, padx=(14, 4))
        self.conn = tk.StringVar(value="wifi")
        ttk.Radiobutton(top, text="Wi-Fi", variable=self.conn, value="wifi").grid(row=0, column=3)
        ttk.Radiobutton(top, text="USB", variable=self.conn, value="usb").grid(row=0, column=4)

        ttk.Label(top, text="IP address").grid(row=0, column=5, padx=(14, 4))
        self.ip = ttk.Entry(top, width=15)
        self.ip.grid(row=0, column=6)

        btns = ttk.Frame(top)
        btns.grid(row=1, column=0, columnspan=7, sticky="w", pady=(6, 4), padx=4)
        self.b_detect = ttk.Button(btns, text="Detect", command=self.detect)
        self.b_read = ttk.Button(btns, text="Read Printer", command=self.read)
        self.b_detect.pack(side="left")
        self.b_read.pack(side="left", padx=6)
        self.headline = ttk.Label(btns, text="", font=("Helvetica", 12, "bold"))
        self.headline.pack(side="left", padx=12)

        # status + error
        st = ttk.LabelFrame(self, text="Status and errors")
        st.pack(fill="x", **pad)
        self.v_status = tk.StringVar(value="–")
        self.v_error = tk.StringVar(value="–")
        self.v_warn = tk.StringVar(value="–")
        self.v_info = tk.StringVar(value="–")
        for r, (lbl, var) in enumerate([("Status", self.v_status), ("Current error", self.v_error),
                                        ("Warnings", self.v_warn), ("Firmware / serial", self.v_info)]):
            ttk.Label(st, text=lbl).grid(row=r, column=0, sticky="w", padx=6)
            w = ttk.Label(st, textvariable=var, wraplength=560)
            w.grid(row=r, column=1, sticky="w")
            if lbl == "Current error":
                self.err_label = w

        levels = ttk.Frame(self)
        levels.pack(fill="x", **pad)
        inkf = ttk.LabelFrame(levels, text="Ink")
        inkf.pack(side="left", fill="both", expand=True, padx=(0, 5))
        self.ink_bars = {}
        for name, colour in INK_COLOURS.items():
            b = Bar(inkf, name, colour, width=160)
            b.pack(anchor="w", padx=6)
            b.set(None)
            self.ink_bars[name] = b
        wf = ttk.LabelFrame(levels, text="Waste ink pads")
        wf.pack(side="left", fill="both", expand=True, padx=(5, 0))
        self.waste_frame = wf

        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, **pad)
        self.fatal = self._table(tabs, ("slot", "code", "meaning"),
                                 ("Log slot", "Code", "Meaning"), (260, 90, 300))
        tabs.add(self.fatal.master, text="Fatal error log")
        self.counters = self._table(tabs, ("name", "value"), ("Counter", "Value"), (420, 200))
        tabs.add(self.counters.master, text="Counters")
        logf = ttk.Frame(tabs)
        self.logbox = tk.Text(logf, height=8, font=("Menlo", 11), state="disabled")
        self.logbox.pack(fill="both", expand=True)
        tabs.add(logf, text="Log")
        self.tabs = tabs

        act = ttk.Frame(self)
        act.pack(fill="x", padx=10, pady=(0, 10))
        self.b_reset = ttk.Button(act, text="Reset Waste Counters…", command=self.reset_waste)
        self.b_temp = ttk.Button(act, text="Temporary Reset…", command=self.temp_reset)
        self.b_save = ttk.Button(act, text="Save Report…", command=self.save_report)
        self.b_adv = ttk.Button(act, text="Advanced Tool", command=self.advanced)
        for b in (self.b_reset, self.b_temp, self.b_save):
            b.pack(side="left", padx=(0, 6))
        self.b_adv.pack(side="right")

    def _table(self, master, cols, heads, widths):
        f = ttk.Frame(master)
        t = ttk.Treeview(f, columns=cols, show="headings", height=8)
        for c, h, w in zip(cols, heads, widths):
            t.heading(c, text=h)
            t.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(f, orient="vertical", command=t.yview)
        t.configure(yscrollcommand=sb.set)
        t.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        t.tag_configure("bad", foreground="#c0392b")
        return t

    # ------------------------------------------------------------- helpers
    def log(self, msg):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", datetime.datetime.now().strftime("%H:%M:%S  ") + msg + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                fn, args = self.q.get_nowait()
                fn(*args)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def ui(self, fn, *args):
        """Run fn on the Tk thread."""
        self.q.put((fn, args))

    def run_bg(self, title, work):
        if self.busy:
            return
        self.busy = True
        self.headline.configure(text=title + "…")
        for b in (self.b_detect, self.b_read, self.b_reset, self.b_temp):
            b.state(["disabled"])
        self.configure(cursor="watch")

        def wrapper():
            try:
                work()
            except Exception as e:
                self.ui(self.log, f"Error: {e}")
                self.ui(messagebox.showerror, "Epson Printer Doctor", str(e))
            finally:
                self.ui(self._done)
        threading.Thread(target=wrapper, daemon=True).start()

    def _done(self):
        self.busy = False
        self.configure(cursor="")
        for b in (self.b_detect, self.b_read, self.b_reset, self.b_temp):
            b.state(["!disabled"])
        if self.headline.cget("text").endswith("…"):
            self.headline.configure(text="")

    def target(self):
        model = epson_mac.canonical_model(self.model.get().strip()) or self.model.get().strip().upper()
        usb = self.conn.get() == "usb"
        ip = self.ip.get().strip()
        if not model:
            raise ValueError("Pick the printer model first (or click Detect).")
        if model not in self.models:
            raise ValueError(f"Model {model} is not in the supported list.")
        if not usb and not ip:
            raise ValueError("Enter the printer's IP address, choose USB, or click Detect.")
        return model, ip, usb

    # ------------------------------------------------------------- actions
    def detect(self):
        def work():
            self.ui(self.log, "Looking for Epson printers on USB…")
            devs = epson_mac.usb_epson_devices()
            if devs:
                self.ui(self.conn.set, "usb")
                model = epson_mac.usb_model()
                self.ui(self.log, f"Found EPSON {devs[0]} on USB" +
                        (f" → model {model}" if model else " (pick the model by hand)"))
                if model:
                    self.ui(self.model.set, model)
                self.ui(self.headline.configure,
                        {"text": f"Found {model or devs[0]} on USB"})
                return
            self.ui(self.log, "Checking printers known to macOS…")
            for name, uri in epson_mac.cups_printers():
                cand = epson_mac.resolve_bonjour(uri) if uri.startswith("dnssd://") else None
                if not cand:
                    import re
                    m = re.match(r"\w+://([\d.]+)", uri)
                    cand = m.group(1) if m else None
                if cand and epson_mac.snmp_name(cand):
                    self._found(cand, epson_mac.canonical_model(name.replace("_", " ")), name)
                    return
            self.ui(self.log, "Scanning the local network (takes a few seconds)…")
            for p in epson_mac.scan_network():
                if "EPSON" in (p.get("name") or "").upper():
                    self._found(p["ip"], epson_mac.canonical_model(p["name"]), p["name"])
                    return
            self.ui(self.log, "No Epson printer found. Is it on, and on the same Wi-Fi or plugged in by USB?")
            self.ui(self.headline.configure, {"text": "No printer found"})
        self.run_bg("Detecting", work)

    def _found(self, ip, model, name):
        self.ui(self.conn.set, "wifi")
        self.ui(self.ip.delete, 0, "end")
        self.ui(self.ip.insert, 0, ip)
        if model:
            self.ui(self.model.set, model)
        self.ui(self.log, f"Found {name} at {ip}" + (f" → model {model}" if model else
                                                      " (pick the model by hand)"))
        self.ui(self.headline.configure, {"text": f"Found at {ip}"})

    def read(self):
        try:
            model, ip, usb = self.target()
        except ValueError as e:
            messagebox.showinfo("Epson Printer Doctor", str(e))
            return

        def work():
            if usb:
                for b in epson_mac.usb_blockers():
                    self.ui(self.log, "⚠ " + epson_mac.USB_BLOCKER_HINT.format(b))
            self.ui(self.log, f"Reading {model} via {'USB' if usb else ip}…")
            with printer_core.open_printer(model, ip, usb) as p:
                rep = printer_core.read_report(p)
            rep["model"], rep["via"] = model, ("USB" if usb else ip)
            rep["time"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            self.ui(self.show, rep)
        self.run_bg("Reading", work)

    def show(self, rep):
        self.report = rep
        self.v_status.set(rep.get("status") or "no answer")
        err = rep.get("error")
        self.v_error.set(err or "none")
        self.err_label.configure(foreground="#c0392b" if err else "#1e8449")
        self.v_warn.set("; ".join(rep.get("warnings") or []) or "none")
        self.v_info.set(f"{rep.get('firmware') or '?'}   /   {rep.get('serial') or '?'}")

        inks = {i[3] if i[3] in INK_COLOURS else i[2]: i[4] for i in rep.get("ink_level") or []}
        for name, bar in self.ink_bars.items():
            v = inks.get(name)
            bar.set(v, colour=None, note="LOW" if v is not None and v < 15 else "")

        for w in self.waste_frame.winfo_children():
            w.destroy()
        waste = rep.get("waste")
        if waste:
            for k, v in waste.items():
                b = Bar(self.waste_frame, WASTE_NAMES.get(k, k), width=160)
                b.pack(anchor="w", padx=6)
                colour = "#c0392b" if v >= 100 else "#e67e22" if v >= 85 else "#27ae60"
                b.set(v, colour, "FULL" if v >= 100 else "")
        else:
            ttk.Label(self.waste_frame, text="Not readable (try USB)").pack(anchor="w", padx=6)

        self.fatal.delete(*self.fatal.get_children())
        if not rep.get("fatal_supported"):
            self.fatal.insert("", "end", values=("Not defined for this model", "", ""))
        for kind, name, code, meaning in rep.get("fatal") or []:
            tag = () if meaning == "no error recorded" else ("bad",)
            self.fatal.insert("", "end", values=(name, code, meaning), tags=tag)

        self.counters.delete(*self.counters.get_children())
        for k, v in (rep.get("counters") or {}).items():
            self.counters.insert("", "end", values=(k, v))

        for pr in rep.get("problems") or []:
            self.log("⚠ " + pr)
        bad = [f for f in rep.get("fatal") or [] if f[3] != "no error recorded"]
        summary = "Error: " + err if err else (f"{len(bad)} fatal code(s) logged" if bad else "No errors")
        self.headline.configure(text=summary)
        self.log(f"Read complete: {summary}.")

    def _confirm_reset(self, what):
        if not messagebox.askyesno(
                "Reset waste ink counters?",
                f"{what}\n\nOnly do this after replacing the waste ink pad or fitting an "
                "external waste ink tank. Otherwise ink can overflow inside the printer.\n\nContinue?",
                icon="warning"):
            return False
        return True

    def reset_waste(self):
        try:
            model, ip, usb = self.target()
        except ValueError as e:
            messagebox.showinfo("Epson Printer Doctor", str(e))
            return
        if not self._confirm_reset(f"This writes the waste ink counters in the {model}'s "
                                   "EEPROM back to 0%."):
            return

        def work():
            with printer_core.open_printer(model, ip, usb) as p:
                ok = p.reset_waste_ink_levels()
            msg = ("Waste counters reset. Turn the printer off and on again."
                   if ok else "The printer did not accept the reset. On newer firmware "
                   "use USB, or try Temporary Reset.")
            self.ui(self.log, msg)
            self.ui(messagebox.showinfo if ok else messagebox.showwarning,
                    "Reset waste counters", msg)
        self.run_bg("Resetting", work)

    def temp_reset(self):
        try:
            model, ip, usb = self.target()
        except ValueError as e:
            messagebox.showinfo("Epson Printer Doctor", str(e))
            return
        if not self._confirm_reset("This sends Epson's temporary waste reset command. "
                                   "It lets the printer work again for a limited time "
                                   "without changing the EEPROM."):
            return

        def work():
            with printer_core.open_printer(model, ip, usb) as p:
                ok = p.temporary_reset_waste()
            msg = ("Temporary reset accepted. Turn the printer off and on again."
                   if ok else "The printer did not confirm the temporary reset.")
            self.ui(self.log, msg)
            self.ui(messagebox.showinfo if ok else messagebox.showwarning,
                    "Temporary reset", msg)
        self.run_bg("Resetting", work)

    def save_report(self):
        rep = self.report
        if not rep:
            messagebox.showinfo("Epson Printer Doctor", "Read the printer first.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".txt",
            initialfile=f"{rep['model']}-report-{rep['time'].replace(':', '').replace(' ', '_')}.txt")
        if not path:
            return
        lines = [f"Epson Printer Doctor report — {rep['model']} via {rep['via']} — {rep['time']}",
                 f"Firmware: {rep.get('firmware')}   Serial: {rep.get('serial')}", "",
                 printer_core.format_errors(rep), "", "Ink:"]
        for i in rep.get("ink_level") or []:
            lines.append(f"  {i[3]}: {i[4]}%")
        lines.append("Waste ink pads:")
        for k, v in (rep.get("waste") or {}).items():
            lines.append(f"  {WASTE_NAMES.get(k, k)}: {v}%")
        lines.append("Counters:")
        for k, v in (rep.get("counters") or {}).items():
            lines.append(f"  {k}: {v}")
        Path(path).write_text("\n".join(lines) + "\n")
        self.log(f"Report saved to {path}")

    def advanced(self):
        subprocess.Popen([sys.executable, str(HERE / "run_upstream.py"), "ui.py", "-P",
                          str(epson_mac.PICKLE)], cwd=HERE)


if __name__ == "__main__":
    App().mainloop()
