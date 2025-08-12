# dashboard_gui.py – Themed Live Dashboard for AllInKeys
import os
import sys
import subprocess
import tkinter as tk
from tkinter import messagebox
import ttkbootstrap as ttk
from ttkbootstrap.constants import *

# Add repo root so `config` package can be imported reliably
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, 'config'))
import settings

from config.settings import (
    STATS_TO_DISPLAY,
    BUTTONS_ENABLED,
    DASHBOARD_REFRESH_INTERVAL,
    CONFIG_FILE_PATH,
    METRICS_LABEL_MAP,
    LOGO_ASCII,
    ALERT_OPTIONS,
    ALERT_CHECKBOXES,
    ALERT_CREDENTIAL_WARNINGS,
    DASHBOARD_PASSWORD,
    SHOW_ALERTS_SUCCESSFULLY_CONFIGURED_TYPES,
    SHOW_ALERT_TYPE_SELECTOR_CHECKBOXES,
    SHOW_CONTROL_BUTTONS_MAIN,
    SHOW_REFRESH_DASHBOARD_DATA_BUTTON,
    SHOW_DELETE_DASHBOARD_DATA_BUTTON,
    SHOW_DONATION_MESSAGE,
    DELETE_VANITY_SEARCH_LOGS,
    DELETE_CSV_FILES,
    DELETE_SYSTEM_LOGS,
    DELETE_CSV_CHECKING_LOGS,
    OPEN_CONFIG_FILE_FROM_DASHBOARD,
    GPU_STRATEGY,
)

from core.dashboard import (
    get_current_metrics,
    reset_all_metrics,
    reset_lifetime_metrics,
    set_metric,
)


class DashboardGUI:
    def __init__(self, master):
        self.master = master
        self.master.title("ALLINKEYS Live Dashboard")
        self.metrics = {}
        self.prev_values = {}
        self.checkbox_vars = {}
        self.module_states = {}
        self.module_buttons = {}
        self.create_widgets()
        self.load_settings_into_checkboxes()
        # Allow other modules a moment to update metrics before syncing button states
        self.master.after(2000, self.sync_module_states)
        self.refresh_loop()

    def create_widgets(self):
        # ----- Scrollable container -----
        self.canvas = tk.Canvas(self.master, borderwidth=0)
        self.scrollbar = ttk.Scrollbar(self.master, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.container = ttk.Frame(self.canvas)
        self.container_window = self.canvas.create_window((0, 0), window=self.container, anchor="nw")
        self.container.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfig(self.container_window, width=e.width)
        )
        self.canvas.bind_all(
            "<MouseWheel>",
            lambda e: self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        )

        # Logo
        if LOGO_ASCII:
            logo_frame = ttk.Frame(self.container)
            logo_frame.pack(fill="x", padx=10)
            logo_label = tk.Label(logo_frame, text=LOGO_ASCII, font=("Courier", 7), justify="center")
            logo_label.pack()

        # Metric Panels
        self.section_frame = ttk.Frame(self.container)
        self.section_frame.pack(fill="both", expand=True, padx=10)

        grouped_keys = {"System Stats": [], "CSV Checker": [], "Backlog": []}

        system_stats = {
            "cpu_usage", "ram_usage", "disk_free_gb", "disk_fill_eta",
            "gpu_stats", "gpu_assignments", "gpu_strategy", "gpu_assignment",
            "vanity_gpu_on", "altcoin_gpu_on", "uptime",
            "vanity_progress_percent", "last_updated", "status",
            "btc_ranges_download_size_bytes", "btc_ranges_download_progress_bytes",
            "btc_ranges_files_ready", "btc_ranges_updated_today",
        }
        csv_stats = {
            "csv_checked_today", "csv_rechecked_today",
            "addresses_checked_today", "addresses_checked_lifetime",
            "matches_found_lifetime",
            "csv_created_today", "csv_created_lifetime",
            "alerts_sent_today", "csv_checker",
        }
        backlog_stats = {
            "batches_completed", "avg_keygen_time", "backlog_files_queued",
            "backlog_eta", "backlog_avg_time", "backlog_current_file",
            "keys_per_sec", "keys_generated_today", "keys_generated_lifetime",
            "current_seed_index", "altcoin_files_converted",
            "derived_addresses_today", "vanity_backlog_count",
            "btc_only_files_checked_today", "btc_only_matches_found_today",
        }

        for key, enabled in STATS_TO_DISPLAY.items():
            if not enabled:
                continue
            label = METRICS_LABEL_MAP.get(key, key)
            if key in system_stats:
                grouped_keys["System Stats"].append((key, label))
            elif key in csv_stats:
                grouped_keys["CSV Checker"].append((key, label))
            elif key in backlog_stats:
                grouped_keys["Backlog"].append((key, label))

        frames = [(g, k) for g, k in grouped_keys.items() if k]
        per_col = (len(frames) + 2) // 3
        col = 0
        row = 0
        FONT = ("Segoe UI", 9)
        SMALL_FONT = ("Segoe UI", 8)

        for idx, (group, keys) in enumerate(frames):
            if idx and idx % per_col == 0:
                col += 1
                row = 0
            frame = ttk.LabelFrame(self.section_frame, text=group)
            frame.grid(row=row, column=col, padx=5, pady=10, sticky="nsew")
            self.section_frame.grid_columnconfigure(col, weight=1, uniform="metric")
            frame.grid_columnconfigure(1, weight=1)
            SMALL_FONT_KEYS = {
                "addresses_checked_today", "addresses_checked_lifetime",
                "matches_found_lifetime"
            }
            for i, (key, label_text) in enumerate(keys):
                ttk.Label(
                    frame,
                    text=label_text + ":",
                    anchor="w",
                    wraplength=150,
                    justify="left",
                    font=FONT,
                ).grid(row=i, column=0, sticky="nw", padx=2, pady=2)

                if key not in ("cpu_usage", "ram_usage") and any(x in key for x in ["usage", "percent", "progress"]) and key != "keys_per_sec":
                    pb = ttk.Progressbar(frame, length=150, mode="determinate")
                    pb.grid(row=i, column=1, sticky="w", padx=2, pady=2)
                    self.metrics[key] = pb
                elif key in ("gpu_stats", "gpu_assignments", "status", "csv_checker", "alerts_sent_today"):
                    txt = tk.Text(frame, height=1, width=45, wrap="none", font=FONT)
                    txt.grid(row=i, column=1, sticky="nsew", padx=2, pady=2)
                    txt.configure(state="disabled")
                    self.metrics[key] = txt
                else:
                    font_opt = SMALL_FONT if key in SMALL_FONT_KEYS else FONT
                    lbl = tk.Label(
                        frame,
                        text="Loading...",
                        fg="white",
                        bg="#222222",
                        font=font_opt,
                        wraplength=300,
                        justify="left",
                    )
                    lbl.grid(row=i, column=1, sticky="w", padx=2, pady=2)
                    self.metrics[key] = lbl

            # Insert active CSV conversion progress bars below backlog metrics
            if group == "Backlog":
                bp_row = len(keys)
                ttk.Label(
                    frame,
                    text="🛠️ CSV Conversions In Progress",
                    anchor="w",
                    wraplength=150,
                    justify="left",
                    font=FONT,
                ).grid(row=bp_row, column=0, sticky="nw", padx=2, pady=2)
                self.backlog_progress_canvas = tk.Canvas(frame, height=120)
                self.backlog_progress_canvas.grid(row=bp_row + 1, column=0, columnspan=2, sticky="nsew")
                self.backlog_scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.backlog_progress_canvas.yview)
                self.backlog_scrollbar.grid(row=bp_row + 1, column=2, sticky="ns")
                self.backlog_progress_canvas.configure(yscrollcommand=self.backlog_scrollbar.set)
                self.backlog_progress_inner = ttk.Frame(self.backlog_progress_canvas)
                self.backlog_progress_canvas.create_window((0, 0), window=self.backlog_progress_inner, anchor="nw")
                self.backlog_progress_inner.bind(
                    "<Configure>",
                    lambda e: self.backlog_progress_canvas.configure(
                        scrollregion=self.backlog_progress_canvas.bbox("all")
                    ),
                )
                self.metrics["backlog_progress"] = {}

            row += 1

            if group == "Backlog":
                backlog_col = col
                backlog_row = row

        # GPU swing mode toggle below backlog stats (column 3)
        self.gpu_swing_mode_enabled = tk.BooleanVar(value=(GPU_STRATEGY == "swing"))
        self.swing_mode_button = ttk.Checkbutton(
            self.section_frame,
            text="Enable Swing Mode",
            variable=self.gpu_swing_mode_enabled,
            command=self.toggle_swing_mode,
        )
        swing_row = backlog_row if 'backlog_row' in locals() else 1
        self.swing_mode_button.grid(
            row=swing_row,
            column=backlog_col if 'backlog_col' in locals() else 2,
            padx=5,
            pady=(5, 10),
            sticky="w",
        )

        # Ensure Alerts module starts running on launch
        try:
            set_metric("status.alerts", "Running")
            set_metric("alerts_status", "Running")
        except Exception:
            pass

        # Alert Configuration Checkboxes
        if SHOW_ALERT_TYPE_SELECTOR_CHECKBOXES:
            alert_frame = ttk.LabelFrame(self.container, text="Alert Methods")
            alert_frame.pack(fill="x", padx=10, pady=(5, 0))

            from core import alerts
            third = (len(ALERT_CHECKBOXES) + 2) // 3
            for idx, name in enumerate(ALERT_CHECKBOXES):
                row = idx // third
                col = (idx % third) * 2
                initial = getattr(settings, name, alerts.ALERT_FLAGS.get(name, False))
                var = tk.BooleanVar(value=initial)
                var.trace_add(
                    "write",
                    lambda *_, n=name, v=var: self.on_checkbox_toggle(n, v.get())
                )
                self.checkbox_vars[name] = var
                cb = tk.Checkbutton(alert_frame, text=name, variable=var)
                cb.grid(row=row, column=col, sticky="w", padx=(0, 2))
                if ALERT_CREDENTIAL_WARNINGS.get(name):
                    tk.Label(alert_frame, text="⚠", fg="red").grid(row=row, column=col + 1)

        # Control Panel
        if SHOW_CONTROL_BUTTONS_MAIN:
            btn_frame = ttk.Frame(self.container)
            btn_frame.pack(pady=10)
            col = 0
            for label in ["vanity", "altcoin", "csv_check", "csv_recheck", "alerts"]:
                if BUTTONS_ENABLED.get(label):
                    self._group_button_set(btn_frame, label.capitalize(), col)
                    col += 1

        # Config and Reset
        bottom_frame = ttk.Frame(self.container)
        bottom_frame.pack(pady=10)

        if OPEN_CONFIG_FILE_FROM_DASHBOARD:
            ttk.Button(bottom_frame, text="Open Config File", command=self.open_config_file).pack(side="left", padx=5)
        ttk.Button(bottom_frame, text="Reset Metrics", command=self.reset_metrics_prompt).pack(side="left", padx=5)
        ttk.Button(bottom_frame, text="Reset Lifetime", command=self.reset_lifetime_prompt).pack(side="left", padx=5)
        ttk.Button(bottom_frame, text="Test Alerts", command=self.test_alerts).pack(side="left", padx=5)
        if SHOW_DELETE_DASHBOARD_DATA_BUTTON:
            ttk.Button(bottom_frame, text="Delete All Data", command=self.delete_data_prompt).pack(side="left", padx=5)

        if SHOW_DONATION_MESSAGE:
            msg = "If you find AllInKeys useful, consider donating! BTC: 18RWVyEciKq8NLz5Q1uEzNGXzTs5ivo37y"
            ttk.Label(self.container, text=msg, font=("Segoe UI", 9, "italic"), foreground="gray").pack(pady=(0, 10))

    def _group_button_set(self, parent, label, col):
        sub_frame = ttk.LabelFrame(parent, text=label)
        sub_frame.grid(row=0, column=col, padx=5)
        # Default to running so buttons show correct state until metrics sync
        self.module_states[label] = "running"

        btns = {}

        def update_buttons():
            state = self.module_states[label]
            for bname, btn in btns.items():
                btn.config(text=bname, bootstyle="secondary", state=tk.NORMAL)

            if state == "running":
                btns["Start"].config(text="RUNNING", bootstyle="success", state=tk.DISABLED)
                btns["Resume"].config(state=tk.DISABLED)
                btns["Pause"].config(state=tk.NORMAL)
                btns["Stop"].config(state=tk.NORMAL)
            elif state == "paused":
                btns["Pause"].config(text="PAUSED", bootstyle="warning", state=tk.DISABLED)
                btns["Resume"].config(state=tk.NORMAL)
                btns["Start"].config(state=tk.DISABLED)
                btns["Stop"].config(state=tk.NORMAL)
            elif state == "stopped":
                btns["Stop"].config(text="STOPPED", bootstyle="danger", state=tk.DISABLED)
                btns["Start"].config(state=tk.NORMAL)
                btns["Pause"].config(state=tk.DISABLED)
                btns["Resume"].config(state=tk.DISABLED)

        # Map display labels to metric keys
        key_map = {
            "vanity": "keygen",
        }

        def set_state(new_state):
            print(f"[GUI] set_state({label}, {new_state})", flush=True)
            self.module_states[label] = new_state
            try:
                mod_key = key_map.get(label.lower(), label.lower())
                from core.dashboard import (
                    get_shutdown_event,
                    get_pause_event,
                    set_thread_health,
                )

                if new_state == "stopped":
                    ev = get_shutdown_event(mod_key)
                    if ev:
                        ev.set()
                    set_thread_health(mod_key, False)
                    set_metric(f"status.{mod_key}", "Stopped")
                elif new_state == "paused":
                    pe = get_pause_event(mod_key)
                    if pe:
                        pe.set()
                    set_thread_health(mod_key, True)
                    set_metric(f"status.{mod_key}", "Paused")
                elif new_state == "running":
                    pe = get_pause_event(mod_key)
                    if pe and pe.is_set():
                        pe.clear()
                    ev = get_shutdown_event(mod_key)
                    if ev and ev.is_set():
                        ev.clear()
                    set_thread_health(mod_key, True)
                    set_metric(f"status.{mod_key}", "Running")

                if label.lower() == "vanity" and new_state in ("paused", "running"):
                    set_metric("global_run_state", new_state)
            except Exception as exc:
                print(f"[GUI] set_state error for {label}: {exc}", flush=True)

            update_buttons()

        mapped = key_map.get(label.lower(), label.lower())

        btns["Start"] = ttk.Button(sub_frame, text="Start", width=8,
                                   command=lambda: set_state("running"))
        btns["Stop"] = ttk.Button(sub_frame, text="Stop", width=8,
                                  command=lambda: set_state("stopped"))
        btns["Pause"] = ttk.Button(sub_frame, text="Pause", width=8,
                                   command=lambda m=mapped, lbl=label: self.handle_pause_resume(m, lbl))
        btns["Resume"] = ttk.Button(sub_frame, text="Resume", width=8,
                                    command=lambda m=mapped, lbl=label: self.handle_pause_resume(m, lbl))

        btns["Start"].grid(row=0, column=0)
        btns["Stop"].grid(row=0, column=1)
        btns["Pause"].grid(row=1, column=0)
        btns["Resume"].grid(row=1, column=1)

        update_buttons()
        self.module_buttons[label] = update_buttons

    def toggle_swing_mode(self):
        new_state = self.gpu_swing_mode_enabled.get()
        strategy = "swing" if new_state else "vanity_priority"
        set_metric("gpu_strategy", strategy)
        set_metric("swing_mode", new_state)

    def update_alert_option(self, name, value):
        try:
            from core import alerts
            alerts.set_alert_flag(name, value)
        except Exception as e:
            print(f"[GUI] Failed to update alert option: {e}")

    def on_checkbox_toggle(self, name, value):
        self.update_alert_option(name, value)
        try:
            cfg_path = CONFIG_FILE_PATH
            with open(cfg_path, "r", encoding="utf-8") as f:
                lines = f.readlines()
            new_lines = []
            updated = False
            for line in lines:
                if line.lstrip().startswith(f"{name} "):
                    new_lines.append(f"{name} = {value}\n")
                    updated = True
                else:
                    new_lines.append(line)
            if updated:
                with open(cfg_path, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
                print(f"[GUI] ✅ Checkbox {name} updated to {value}", flush=True)
        except Exception as e:
            print(f"[GUI] Failed to persist {name}: {e}", flush=True)

    def load_settings_into_checkboxes(self):
        try:
            import importlib
            cfg = importlib.import_module('config.settings')
            importlib.reload(cfg)
            for name, var in self.checkbox_vars.items():
                var.set(getattr(cfg, name, var.get()))
        except Exception as e:
            print(f"[GUI] Failed to load settings: {e}", flush=True)

    def handle_pause_resume(self, module_name, display_label=None):
        from core.dashboard import module_pause_events
        ev = module_pause_events.get(module_name)
        if not ev:
            print(f"[GUI] ⚠️ No pause event for {module_name}", flush=True)
            return
        label = display_label or module_name.capitalize()
        if ev.is_set():
            print(f"[GUI] ▶️ Resuming module: {module_name}", flush=True)
            ev.clear()
            self.module_states[label] = "running"
            set_metric(f"status.{module_name}", "Running")
        else:
            print(f"[GUI] ⏸️ Pausing module: {module_name}", flush=True)
            ev.set()
            self.module_states[label] = "paused"
            set_metric(f"status.{module_name}", "Paused")
        updater = self.module_buttons.get(label)
        if updater:
            updater()
        if module_name == "keygen":
            set_metric("global_run_state", "paused" if ev.is_set() else "running")

    def _flash_widget(self, widget, color="#228B22", duration=500):
        orig = widget.cget("background") if hasattr(widget, "cget") else None
        try:
            widget.config(background=color)
            widget.after(duration, lambda: widget.config(background=orig))
        except Exception:
            pass

    def _format_coin_dict(self, data, per_row=2):
        """Return multiline string with coin values in aligned columns."""
        if not isinstance(data, dict):
            return str(data)
        items = list(data.items())
        lines = []
        for i in range(0, len(items), per_row):
            row_parts = [f"{c.upper():<5}: {v:<8}" for c, v in items[i:i+per_row]]
            lines.append("   ".join(row_parts))
        return "\n".join(lines)

    def sync_module_states(self):
        """Synchronize button states with current metrics on startup."""
        try:
            stats = get_current_metrics()
            status_dict = stats.get("status", {})
            if not isinstance(status_dict, dict):
                status_dict = {}
            key_map = {"vanity": "keygen"}
            for label, updater in self.module_buttons.items():
                metric_key = key_map.get(label.lower(), label.lower())
                state_str = str(status_dict.get(metric_key, "stopped")).lower()
                if state_str not in {"running", "paused"}:
                    state = "stopped"
                else:
                    state = state_str
                self.module_states[label] = state
                updater()
        except Exception:
            pass

    def refresh_loop(self):
        try:
            from core import alerts
            stats = get_current_metrics()
            # Sync checkbox states with ALERT_FLAGS
            for name, var in self.checkbox_vars.items():
                live = alerts.ALERT_FLAGS.get(name, False)
                if var.get() != live:
                    var.set(live)
            # Update module button states based on metrics
            status_dict = stats.get("status", {})
            if not isinstance(status_dict, dict):
                status_dict = {}
            key_map = {"vanity": "keygen"}
            for label, updater in self.module_buttons.items():
                metric_key = key_map.get(label.lower(), label.lower())
                state_str = str(status_dict.get(metric_key, "stopped")).lower()
                if state_str not in {"running", "paused"}:
                    state = "stopped"
                else:
                    state = state_str
                if self.module_states.get(label) != state:
                    self.module_states[label] = state
                    updater()
            self.update_metrics_display(stats)
        except Exception as e:
            print(f"[Dashboard Error] {e}")
        self.master.after(int(DASHBOARD_REFRESH_INTERVAL * 1000), self.refresh_loop)

    def update_metrics_display(self, stats):
        for key, widget in self.metrics.items():
            if key == "backlog_progress":
                for child in self.backlog_progress_inner.winfo_children():
                    child.destroy()
                progress_data = stats.get("backlog_progress", {}) or {}
                progress_data = {k: v for k, v in progress_data.items() if "_gpu" in k}
                assign = stats.get("gpu_assignments", {}).get("altcoin_derive", "")
                gpu_count = (
                    len([g for g in assign.split(",") if g.strip()])
                    if assign and assign != "N/A"
                    else len(progress_data)
                )
                for idx, (fname, pct) in enumerate(list(progress_data.items())[:gpu_count]):
                    ttk.Label(self.backlog_progress_inner, text=fname).grid(row=idx, column=0, sticky="w")
                    bar = ttk.Progressbar(self.backlog_progress_inner, length=120, mode="determinate")
                    bar.grid(row=idx, column=1, padx=2)
                    try:
                        bar["value"] = float(pct)
                    except Exception:
                        bar["value"] = 0
                continue

            value = stats.get(key, "N/A")
            if isinstance(widget, ttk.Progressbar):
                try:
                    widget["value"] = float(value.strip('%')) if isinstance(value, str) else float(value)
                except Exception:
                    widget["value"] = 0
            elif isinstance(widget, tk.Text):
                lines = []
                if isinstance(value, dict):
                    if key == "status":
                        name_map = {
                            "keygen": "Keygen",
                            "csv_check": "CSV Checker",
                            "csv_recheck": "CSV Recheck",
                            "altcoin": "Altcoin",
                            "alerts": "Alerts",
                            "checkpoint": "Checkpoint",
                            "metrics": "Metrics",
                        }
                        icon_map = {"running": "✅", "stopped": "❌", "paused": "⏸"}
                        for mod, state in value.items():
                            title = name_map.get(mod, mod.title())
                            s = str(state).lower()
                            icon = icon_map.get(s, "")
                            lines.append(f"{title}: {state} {icon}")
                    elif key == "gpu_assignments":
                        name_map = {
                            "vanitysearch": "VanitySearch",
                            "altcoin_derive": "Altcoin Derive",
                        }
                        for mod, name in value.items():
                            title = name_map.get(mod, mod.replace('_', ' ').title())
                            lines.append(f"{title} → {name}")
                    elif key in (
                        "addresses_generated_lifetime",
                        "addresses_checked_lifetime",
                        "matches_found_lifetime",
                        "addresses_checked_today",
                        "addresses_generated_today",
                        "alerts_sent_today",
                        "alerts_sent_lifetime",
                    ):
                        if isinstance(value, dict):
                            lines.extend(self._format_coin_dict(value).splitlines())
                        else:
                            lines.append(f"{key}: {value}")
                    else:
                        for gid, info in value.items():
                            if isinstance(info, dict):
                                name = info.get('name', '')
                                usage = info.get('usage', 'N/A')
                                vram = info.get('vram', 'N/A')
                                temp = info.get('temp', 'N/A')
                                lines.append(f"{gid}: {name}")
                                detail = f"  Usage: {usage}  VRAM: {vram}"
                                if temp and temp != 'N/A':
                                    detail += f"  Temp: {temp}"
                                lines.append(detail)
                            else:
                                lines.append(f"{gid}: {info}")
                else:
                    lines.append(str(value))
                widget.config(state="normal")
                widget.delete("1.0", "end")
                widget.insert("end", "\n".join(lines) or "N/A")
                widget.config(state="disabled")
                line_count = max(1, len(lines))
                try:
                    if int(widget.cget("height")) != line_count:
                        widget.config(height=line_count)
                except Exception:
                    pass
            else:
                if key in (
                    "addresses_generated_today",
                    "addresses_generated_lifetime",
                    "matches_found_lifetime",
                    "addresses_checked_today",
                    "addresses_checked_lifetime",
                    "alerts_sent_today",
                    "alerts_sent_lifetime",
                ):
                    disp = self._format_coin_dict(value) if isinstance(value, dict) else str(value)
                elif isinstance(value, dict):
                    lines = [f"{k.upper()}: {v}" for k, v in value.items()]
                    disp = "\n".join(lines)
                else:
                    disp = str(value)
                    if len(disp) > 40:
                        disp = disp[:37] + "..."
                if self.prev_values.get(key) != disp:
                    self._flash_widget(widget)
                self.prev_values[key] = disp
                widget.config(text=disp)

    def open_config_file(self):
        if not os.path.exists(CONFIG_FILE_PATH):
            messagebox.showerror("Missing Config", f"Could not find: {CONFIG_FILE_PATH}")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(CONFIG_FILE_PATH)
            elif sys.platform.startswith("darwin"):
                subprocess.Popen(["open", CONFIG_FILE_PATH])
            else:
                subprocess.Popen(["xdg-open", CONFIG_FILE_PATH])
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def test_alerts(self):
        try:
            from core.alerts import run_test_alerts_from_csv
            run_test_alerts_from_csv()
            messagebox.showinfo("Test Alerts", "Test alerts dispatched. Check logs for details.")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to send test alerts: {e}")

    def reset_metrics_prompt(self):
        resp = messagebox.askyesno("Reset Metrics", "Include lifetime stats?")
        if resp:
            pw = self.prompt_password()
            if pw == DASHBOARD_PASSWORD:
                reset_all_metrics()
            else:
                messagebox.showerror("Invalid Password", "Reset canceled.")
        else:
            reset_all_metrics()

    def reset_lifetime_prompt(self):
        if messagebox.askyesno("Reset Lifetime", "Clear all lifetime metrics?"):
            pw = self.prompt_password()
            if pw == DASHBOARD_PASSWORD:
                reset_lifetime_metrics()
            else:
                messagebox.showerror("Invalid Password", "Reset canceled.")

    def delete_data_prompt(self):
        if messagebox.askyesno("Confirm Delete", "Permanently delete all key/data files?"):
            really = messagebox.askyesno("Double Check", "Are you really really sure?")
            if really:
                pw = self.prompt_password()
                if pw == DASHBOARD_PASSWORD:
                    print("[GUI] Deleting all data...")
                    # Here add actual deletion logic based on flags like DELETE_VANITY_SEARCH_LOGS
                else:
                    messagebox.showerror("Invalid Password", "Deletion canceled.")

    def prompt_password(self):
        pw_window = tk.Toplevel(self.master)
        pw_window.title("Enter Password")
        tk.Label(pw_window, text="Password: ").grid(row=0, column=0)
        pw_entry = tk.Entry(pw_window, show="*")
        pw_entry.grid(row=0, column=1)
        result = []

        def submit_pw():
            result.append(pw_entry.get())
            pw_window.destroy()

        tk.Button(pw_window, text="Submit", command=submit_pw).grid(row=1, column=0, columnspan=2)
        self.master.wait_window(pw_window)
        return result[0] if result else ""


def start_dashboard():
    root = ttk.Window(themename="darkly")
    root.geometry("900x600")
    app = DashboardGUI(root)
    root.mainloop()


if __name__ == "__main__":
    start_dashboard()
