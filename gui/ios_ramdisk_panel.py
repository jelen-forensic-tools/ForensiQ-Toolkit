"""
gui/ios_ramdisk_panel.py — Panel for forensic acquisition from an SSH ramdisk
(checkm8 / usbliter8). Connects to a running ramdisk over SSH, runs mount_ich
and pulls the selected artefacts. Pwned DFU + ramdisk boot are done externally (macOS + hw).
"""
from __future__ import annotations

import os
import datetime
import threading
import json
import customtkinter as ctk

from core.i18n import t
from .iconset import module_icon
from . import theme as th
from core.ios_ramdisk import iOSRamdiskAcquirer, IOS_RAMDISK_MODULES, paramiko_available

ACCENT     = "#00d4ff"
BG_CARD    = "#16213e"
BG_DARK    = "#0a0f1e"
BG_LOG     = "#0a0f1e"
TEXT_MUTED = "#888888"
WHITE      = "#ffffff"
GREEN      = "#00c853"
RED        = "#ff5252"
ORANGE     = "#ff9800"

MODULE_ICONS = {
    "sms":        "💬",
    "contacts":   "👥",
    "call_logs":  "📞",
    "safari":     "🌐",
    "notes":      "📝",
    "accounts":   "👤",
    "photos":     "🖼",
    "whatsapp":   "💚",
    "keychain":   "🔑",
}


class iOSRamdiskPanel(ctk.CTkFrame):
    def __init__(self, master, accent: str = ACCENT, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self.accent = accent
        self._output_dir = ""
        self._running = False
        self._acq: iOSRamdiskAcquirer | None = None
        self._module_vars: dict[str, ctk.BooleanVar] = {
            k: ctk.BooleanVar(value=(k != "keychain")) for k in IOS_RAMDISK_MODULES
        }
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build()

    # ── UI ──────────────────────────────────────────────────────────────────
    def _build(self):
        self._build_topbar()
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=3, minsize=380)
        body.grid_columnconfigure(1, weight=2, minsize=340)
        body.grid_rowconfigure(0, weight=1)
        self._build_log(body)
        self._build_config(body)

    def _build_topbar(self):
        tb = th.Toolbar(self)
        tb.grid(row=0, column=0, sticky="ew", pady=(0, th.GAP))

        # Source: output folder selection + status (SSH parameters are in the right panel)
        th.secondary_button(tb.source, t("ui.output"), self._pick_output,
                            width=150).pack(side="left", padx=(0, 10))
        self._output_label = ctk.CTkLabel(tb.source, text=t("bkp.no_output"), font=th.f_small(),
                                          text_color=TEXT_MUTED, anchor="w")
        self._output_label.pack(side="left")

        self._status_bar = ctk.CTkLabel(tb.info, text="", font=th.f_body(),
                                        text_color=TEXT_MUTED, anchor="e")
        self._status_bar.pack(side="left")

        # Actions: Test connection, Pull, Stop
        self._test_btn = th.secondary_button(tb.actions, t("rd.test"), self._test_connection,
                                             width=120)
        self._btn_run = th.primary_button(tb.actions, t("rd.run"), self._run, width=150)
        self._btn_stop = th.danger_button(tb.actions, t("ext.stop"), self._stop,
                                          state="disabled")
        th.pack_row(self._test_btn, self._btn_run, self._btn_stop)

    def _build_log(self, parent):
        self._log_card = th.LogCard(parent, t("ui.log"))
        self._log_card.grid(row=0, column=0, sticky="nsew", padx=(0, th.GAP))
        self._log_box = self._log_card.box

        if not paramiko_available():
            self._log(t("rd.no_paramiko"), "err")
        else:
            self._log(t("rd.hint"), "muted")

    def _build_config(self, parent):
        self._modules = th.ModuleList(parent)
        self._modules.grid(row=0, column=1, sticky="nsew")
        right = self._modules.scroll

        # SSH connection – compact card above the modules
        self._modules.add_section(t("rd.conn_section"))
        conn = ctk.CTkFrame(right, fg_color=BG_CARD, corner_radius=th.RADIUS_ITEM)
        conn.grid(row=self._modules.next_row(), column=0, sticky="ew", padx=4, pady=3)
        conn.grid_columnconfigure(1, weight=1)
        conn.grid_columnconfigure(3, weight=1)

        def _field(r, c, label_key, default, span=1):
            th.field_label(conn, t(label_key)).grid(row=r, column=c * 2, sticky="w",
                                                    padx=(12, 6), pady=6)
            var = ctk.StringVar(value=default)
            ctk.CTkEntry(conn, textvariable=var, height=th.BTN_H, font=th.f_body()).grid(
                row=r, column=c * 2 + 1, columnspan=span, sticky="ew", padx=(0, 12), pady=6)
            return var

        self._host_var = _field(0, 0, "rd.host", "localhost")
        self._port_var = _field(0, 1, "rd.port", "2222")
        self._user_var = _field(1, 0, "rd.user", "root")
        self._pass_var = _field(1, 1, "rd.pass", "alpine")
        self._root_var = _field(2, 0, "rd.data_root", "/mnt2", span=3)

        # Modules to extract
        self._modules.add_section(t("ui.modules"), all_text=t("ui.all"), none_text=t("ui.none"))
        for key in IOS_RAMDISK_MODULES:
            self._modules.add(key, t(f"rd.mod.{key}"), self._module_vars[key],
                              image=module_icon(key, size=30),
                              emoji=MODULE_ICONS.get(key, "📁"))

    # ── Akce ──────────────────────────────────────────────────────────────
    def _make_acq(self) -> iOSRamdiskAcquirer:
        return iOSRamdiskAcquirer(
            output_dir=self._output_dir or ".",
            host=self._host_var.get().strip() or "localhost",
            port=int(self._port_var.get().strip() or "2222"),
            user=self._user_var.get().strip() or "root",
            password=self._pass_var.get(),
            data_root=self._root_var.get().strip() or "/mnt2",
        )

    def _test_connection(self):
        self._test_btn.configure(state="disabled", text="…")
        def _worker():
            try:
                ok, msg = self._make_acq().test_connection()
            except Exception as e:
                ok, msg = False, str(e)
            self.after(0, lambda: self._test_done(ok, msg))
        threading.Thread(target=_worker, daemon=True).start()

    def _test_done(self, ok: bool, msg: str):
        self._test_btn.configure(state="normal", text=t("rd.test"))
        self._log((("✓ " if ok else "✗ ") + msg), "ok" if ok else "err")
        self._status_bar.configure(
            text=t("rd.connected") if ok else t("rd.conn_failed"),
            text_color=GREEN if ok else RED)

    def _pick_output(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(title=t("rd.pick_output"))
        if path:
            self._output_dir = path
            short = "…" + path[-55:] if len(path) > 58 else path
            self._output_label.configure(text=short, text_color=WHITE)

    def _run(self):
        if not paramiko_available():
            self._status_bar.configure(text=t("rd.no_paramiko"), text_color=RED)
            return
        selected = [k for k, v in self._module_vars.items() if v.get()]
        if not selected:
            self._status_bar.configure(text=t("ios_bkp.err_no_modules"), text_color=RED)
            return
        if not self._output_dir:
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self._output_dir = os.path.join(
                os.path.expanduser("~"), "Documents", f"ForenziQ_Ramdisk_{ts}")
            short = "…" + self._output_dir[-55:] if len(self._output_dir) > 58 else self._output_dir
            self._output_label.configure(text=short, text_color=WHITE)

        self._running = True
        self._btn_run.configure(state="disabled", text=t("ext.running"))
        self._btn_stop.configure(state="normal")
        self._status_bar.configure(text=t("ext.running"), text_color=self.accent)
        self._log_clear()
        self._log_raw("─" * 48, "sep")
        self._log(t("rd.log_started"), "info")
        self._log(f"{t('ext.log_modules')} {len(selected)}", "muted")
        self._log_raw("─" * 48, "sep")

        self._acq = self._make_acq()
        self._acq.run(
            modules=selected,
            on_progress=lambda lbl, i, n: self.after(
                0, lambda l=lbl, c=i, tot=n: self._on_progress(l, c, tot)),
            on_done=lambda results, err: self.after(
                0, lambda r=results, e=err: self._on_done(r, e)),
            on_module_done=lambda res, i, n, el, av: self.after(
                0, lambda r=res, c=i, tot=n, e=el: self._on_module_done(r, c, tot, e)),
            on_log=lambda msg: self.after(0, lambda m=msg: self._log(m, "muted")),
        )

    def _stop(self):
        if self._acq:
            self._acq.stop()
        self._btn_stop.configure(state="disabled")
        self._status_bar.configure(text=t("ios_bkp.stopping") + "…", text_color=ORANGE)

    # ── Callbacks ─────────────────────────────────────────────────────────
    def _on_progress(self, label, current, total):
        self._log_raw("", "")
        self._log(f"▶  [{current}/{total}]  {label}", "info")

    def _on_module_done(self, result, current, total, elapsed):
        if result.success:
            self._log(f"✓  {result.label}", "ok")
            self._log(f"{t('ext.log_result')} {result.count} {t('ext.records')}  "
                      f"|  {elapsed:.2f}s", "muted")
        else:
            self._log(f"✗  {result.label}", "err")
            self._log(f"{t('ext.log_error_lbl')} {result.error}", "err")

    def _on_done(self, results, err):
        self._running = False
        self._btn_run.configure(state="normal", text=t("rd.run"))
        self._btn_stop.configure(state="disabled")
        if err:
            self._log(f"✗  {err}", "err")
            self._status_bar.configure(text=t("rd.conn_failed"), text_color=RED)
            return
        ok = sum(1 for r in results if r.success)
        total_files = sum(r.count for r in results)
        # Summary JSON for the evidence
        try:
            summary = {
                "acquired_at": datetime.datetime.now().isoformat(timespec="seconds"),
                "host": self._host_var.get(), "data_root": self._root_var.get(),
                "modules": [{
                    "module": r.module, "success": r.success,
                    "count": r.count, "error": r.error,
                    "files": r.data,
                } for r in results],
            }
            with open(os.path.join(self._output_dir, "ramdisk_summary.json"),
                      "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        self._log_raw("─" * 48, "sep")
        self._log(f"✓  {t('rd.done')} — {ok}/{len(results)} | {total_files} {t('ext.records')}", "ok")
        self._status_bar.configure(text=t("rd.done"), text_color=GREEN)

    # ── Log ───────────────────────────────────────────────────────────────
    def _log(self, text, tag="muted"):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self._log_box.configure(state="normal")
        self._log_box.insert("end", f"[{ts}] ", "muted")
        self._log_box.insert("end", text + "\n", tag)
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _log_raw(self, text, tag=""):
        self._log_box.configure(state="normal")
        self._log_box.insert("end", text + "\n", tag)
        self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _log_clear(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")

    # ── Jazyk ─────────────────────────────────────────────────────────────
    def refresh_lang(self):
        for w in self.winfo_children():
            w.destroy()
        self._module_vars = {
            k: ctk.BooleanVar(value=self._module_vars[k].get()) for k in IOS_RAMDISK_MODULES
        }
        self._build()
        if self._output_dir:
            short = "…" + self._output_dir[-55:] if len(self._output_dir) > 58 else self._output_dir
            self._output_label.configure(text=short, text_color=WHITE)
