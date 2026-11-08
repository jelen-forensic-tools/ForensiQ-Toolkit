"""
gui/ios_ramdisk_panel.py — Panel pro forenzní akvizici z SSH ramdisku
(checkm8 / usbliter8). Připojí se na běžící ramdisk přes SSH, spustí mount_ich
a stáhne vybrané artefakty. Pwned DFU + boot ramdisku se dělá externě (macOS + hw).
"""
from __future__ import annotations

import os
import datetime
import threading
import json
import customtkinter as ctk

from core.i18n import t
from .iconset import module_icon
from core.ios_ramdisk import iOSRamdiskAcquirer, IOS_RAMDISK_MODULES, paramiko_available

ACCENT     = "#5AC8FA"
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
        body.grid_columnconfigure(1, weight=2)
        body.grid_rowconfigure(0, weight=1)
        self._build_log(body)
        self._build_config(body)

    def _build_topbar(self):
        top = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=12)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        top.grid_columnconfigure(0, weight=1)

        # ── Řádek 0 – připojení ──
        row0 = ctk.CTkFrame(top, fg_color="transparent")
        row0.grid(row=0, column=0, padx=16, pady=(12, 4), sticky="ew")

        def _field(label_key, default, width):
            ctk.CTkLabel(row0, text=t(label_key),
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=TEXT_MUTED).pack(side="left", padx=(0, 4))
            var = ctk.StringVar(value=default)
            e = ctk.CTkEntry(row0, textvariable=var, width=width, height=28,
                             font=ctk.CTkFont(size=11))
            e.pack(side="left", padx=(0, 12))
            return var

        self._host_var = _field("rd.host", "localhost", 110)
        self._port_var = _field("rd.port", "2222", 60)
        self._user_var = _field("rd.user", "root", 70)
        self._pass_var = _field("rd.pass", "alpine", 90)
        self._root_var = _field("rd.data_root", "/mnt2", 80)

        self._test_btn = ctk.CTkButton(
            row0, text=t("rd.test"), width=90, height=28,
            fg_color="#2a2a4a", hover_color="#3a3a5a",
            font=ctk.CTkFont(size=11), command=self._test_connection,
        )
        self._test_btn.pack(side="left")

        # ── Řádek 1 – výstup + akce ──
        row1 = ctk.CTkFrame(top, fg_color="transparent")
        row1.grid(row=1, column=0, padx=16, pady=(0, 12), sticky="ew")
        row1.grid_columnconfigure(0, weight=1)

        info = ctk.CTkFrame(row1, fg_color="transparent")
        info.grid(row=0, column=0, sticky="w")
        ctk.CTkButton(
            info, text=t("rd.pick_output"), width=120, height=28,
            fg_color="#2a2a4a", hover_color="#3a3a5a",
            font=ctk.CTkFont(size=11), command=self._pick_output,
        ).pack(side="left", padx=(0, 10))
        self._output_label = ctk.CTkLabel(
            info, text=t("bkp.no_output"),
            font=ctk.CTkFont(size=11), text_color=TEXT_MUTED, anchor="w",
        )
        self._output_label.pack(side="left")

        actions = ctk.CTkFrame(row1, fg_color="transparent")
        actions.grid(row=0, column=1, sticky="e")
        self._status_bar = ctk.CTkLabel(
            actions, text="", font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED, anchor="e", width=150,
        )
        self._status_bar.pack(side="left", padx=(0, 12))
        self._btn_run = ctk.CTkButton(
            actions, text=t("rd.run"),
            font=ctk.CTkFont(size=13, weight="bold"),
            fg_color="#1D6F42", hover_color="#145230",
            text_color="#fff", width=150, height=36, command=self._run,
        )
        self._btn_run.pack(side="left", padx=(0, 4))
        self._btn_stop = ctk.CTkButton(
            actions, text=t("ext.stop"),
            font=ctk.CTkFont(size=13),
            fg_color="#f44336", hover_color="#c62828",
            text_color="#fff", width=100, height=36,
            state="disabled", command=self._stop,
        )
        self._btn_stop.pack(side="left")

    def _build_log(self, parent):
        log_card = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=12)
        log_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        log_card.grid_columnconfigure(0, weight=1)
        log_card.grid_rowconfigure(2, weight=1)

        hdr = ctk.CTkFrame(log_card, fg_color="transparent")
        hdr.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
        ctk.CTkLabel(hdr, text=t("rd.log_title"),
                     font=ctk.CTkFont(size=12, weight="bold"),
                     text_color=self.accent).pack(side="left")
        ctk.CTkFrame(log_card, height=1, fg_color="#2a2a3e").grid(
            row=1, column=0, sticky="ew", padx=14)

        self._log_box = ctk.CTkTextbox(
            log_card, fg_color=BG_LOG, text_color="#cccccc",
            font=ctk.CTkFont(family="Consolas", size=11),
            corner_radius=8, state="disabled", wrap="word",
        )
        self._log_box.grid(row=2, column=0, sticky="nsew", padx=10, pady=(6, 10))
        self._log_box.tag_config("ok",    foreground=GREEN)
        self._log_box.tag_config("err",   foreground=RED)
        self._log_box.tag_config("info",  foreground=self.accent)
        self._log_box.tag_config("muted", foreground=TEXT_MUTED)
        self._log_box.tag_config("sep",   foreground="#2a3a5a")

        if not paramiko_available():
            self._log(t("rd.no_paramiko"), "err")
        else:
            self._log(t("rd.hint"), "muted")

    def _build_config(self, parent):
        right = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)

        for i, (key, _) in enumerate(IOS_RAMDISK_MODULES.items()):
            icon = MODULE_ICONS.get(key, "📁")
            card = ctk.CTkFrame(right, fg_color=BG_CARD, corner_radius=10)
            card.grid(row=i, column=0, sticky="ew", padx=5, pady=4)
            card.grid_columnconfigure(1, weight=1)
            _img = module_icon(key, size=30)
            if _img is not None:
                ctk.CTkLabel(card, text="", image=_img, width=36).grid(
                    row=0, column=0, padx=(12, 4), pady=8)
            else:
                ctk.CTkLabel(card, text=icon, font=ctk.CTkFont(size=20), width=36).grid(
                    row=0, column=0, padx=(12, 4), pady=10)
            ctk.CTkLabel(card, text=t(f"rd.mod.{key}"),
                         font=ctk.CTkFont(size=12, weight="bold"), anchor="w").grid(
                row=0, column=1, sticky="w", padx=4)
            ctk.CTkCheckBox(card, text="", variable=self._module_vars[key],
                            checkbox_width=20, checkbox_height=20,
                            fg_color=self.accent, checkmark_color="#000").grid(
                row=0, column=2, padx=12)

        sel_idx = len(IOS_RAMDISK_MODULES)
        ctk.CTkFrame(right, height=1, fg_color="#2a2a3e").grid(
            row=sel_idx, column=0, sticky="ew", padx=8, pady=(10, 6))
        sel = ctk.CTkFrame(right, fg_color="transparent")
        sel.grid(row=sel_idx + 1, column=0, padx=8, pady=(0, 12), sticky="ew")
        sel.grid_columnconfigure((0, 1), weight=1)
        ctk.CTkButton(sel, text=t("ext.select_all"), font=ctk.CTkFont(size=10),
                      fg_color="#2a2a4a", hover_color="#3a3a5a", height=26,
                      command=lambda: [v.set(True) for v in self._module_vars.values()]).grid(
            row=0, column=0, padx=(0, 3), sticky="ew")
        ctk.CTkButton(sel, text=t("ext.deselect_all"), font=ctk.CTkFont(size=10),
                      fg_color="#2a2a4a", hover_color="#3a3a5a", height=26,
                      command=lambda: [v.set(False) for v in self._module_vars.values()]).grid(
            row=0, column=1, padx=(3, 0), sticky="ew")

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
        # Souhrnný JSON pro evidence
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
