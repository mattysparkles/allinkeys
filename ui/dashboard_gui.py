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
)

from core.dashboard import get_current_metrics, reset_all_metrics, set_metric


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
            "gpu_stats", "gpu_assignments", "uptime",
            "vanity_progress_percent", "last_updated", "status",
        }
        csv_stats = {
            "csv_checked_today", "csv_rechecked_today",
            "addresses_checked_today", "addresses_checked_lifetime",
            "matches_found_lifetime", "csv_created_today", "csv_created_lifetime",
        }
        backlog_stats = {
            "batches_completed", "avg_keygen_time", "backlog_files_queued",
            "backlog_eta", "backlog_avg_time", "backlog_current_file",
            "keys_per_sec", "keys_generated_today", "keys_generated_lifetime",
            "current_seed_index",
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

                if key not in ("cpu_usage", "ram_usage") and any(x in key for x in ["usage", "percent", "progress", "keys_per_sec"]):
                    pb = ttk.Progressbar(frame, length=150, mode="determinate")
                    pb.grid(row=i, column=1, sticky="w", padx=2, pady=2)
                    self.metrics[key] = pb
                elif key in ("gpu_stats", "gpu_assignments", "status", "matches_found_lifetime", "addresses_checked_lifetime", "addresses_checked_today"):
                    txt = tk.Text(frame, height=4, width=45, wrap="word", font=FONT)
                    txt.grid(row=i, column=1, sticky="w", padx=2, pady=2)
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

            # Insert backlog progress bar below backlog metrics
            if group == "Backlog":
                bp_row = len(keys)
                ttk.Label(
                    frame,
                    text="Backlog Progress:",
                    anchor="w",
                    wraplength=150,
                    justify="left",
                    font=FONT,
                ).grid(row=bp_row, column=0, sticky="nw", padx=2, pady=2)
                pb = ttk.Progressbar(frame, length=150, mode="determinate")
                pb.grid(row=bp_row, column=1, sticky="w", padx=2, pady=2)
                self.metrics["backlog_progress"] = pb
                pct = tk.Label(frame, text="0%", font=FONT)
                pct.grid(row=bp_row + 1, column=1, sticky="w", padx=2, pady=(0, 2))
                self.metrics["backlog_percent"] = pct

            row += 1

        # Alert Configuration Checkboxes
        if SHOW_ALERT_TYPE_SELECTOR_CHECKBOXES:
            alert_frame = ttk.LabelFrame(self.container, text="Alert Methods")
            alert_frame.pack(fill="x", padx=10, pady=(5, 0))

            from core.alerts import ALERT_FLAGS
            third = (len(ALERT_CHECKBOXES) + 2) // 3
            for idx, option in enumerate(ALERT_CHECKBOXES):
                row = idx // third
                col = (idx % third) * 2
                var = tk.BooleanVar(value=ALERT_FLAGS.get(option, False))
                self.checkbox_vars[option] = var
                cb = tk.Checkbutton(alert_frame, text=option, variable=var,
                                     command=lambda o=option, v=var: self.update_alert_option(o, v.get()))
                cb.grid(row=row, column=col, sticky="w", padx=(0, 2))
                if ALERT_CREDENTIAL_WARNINGS.get(option):
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
        ttk.Button(bottom_frame, text="Test Alerts", command=self.test_alerts).pack(side="left", padx=5)
        if SHOW_DELETE_DASHBOARD_DATA_BUTTON:
            ttk.Button(bottom_frame, text="Delete All Data", command=self.delete_data_prompt).pack(side="left", padx=5)

        if SHOW_DONATION_MESSAGE:
            msg = "If you find AllInKeys useful, consider donating! BTC: 18RWVyEciKq8NLz5Q1uEzNGXzTs5ivo37y"
            ttk.Label(self.container, text=msg, font=("Segoe UI", 9, "italic"), foreground="gray").pack(pady=(0, 10))

    def _group_button_set(self, parent, label, col):
        sub_frame = ttk.LabelFrame(parent, text=label)
        sub_frame.grid(row=0, column=col, padx=5)
        self.module_states[label] = "stopped"

        btns = {}

        def update_buttons():
            state = self.module_states[label]
            for bname, btn in btns.items():
                if state == "running" and bname == "Start":
                    btn.config(text="RUNNING", bootstyle="success")
                elif state == "stopped" and bname == "Stop":
                    btn.config(text="STOPPED", bootstyle="danger")
                elif state == "paused" and bname == "Pause":
                    btn.config(text="PAUSED", bootstyle="warning")
                else:
                    btn.config(text=bname, bootstyle="secondary")

        def set_state(new_state):
            self.module_states[label] = new_state
            try:
                mod_key = label.lower()
                from core.dashboard import (
                    get_shutdown_event,
                    get_pause_event,
                    set_thread_health,
                )

                if new_state == "stopped":
                    ev = get_shutdown_event()
                    if ev:
                        ev.set()
                    set_thread_health(mod_key, False)
                    set_metric(f"status.{mod_key}", False)
                elif new_state == "paused":
                    pe = get_pause_event()
                    if pe:
                        pe.set()
                    set_thread_health(mod_key, True)
                    set_metric(f"status.{mod_key}", True)
                elif new_state == "running":
                    pe = get_pause_event()
                    if pe and pe.is_set():
                        pe.clear()
                    ev = get_shutdown_event()
                    if ev and ev.is_set():
                        ev.clear()
                    set_thread_health(mod_key, True)
                    set_metric(f"status.{mod_key}", True)

                if mod_key == "vanity" and new_state in ("paused", "running"):
                    set_metric("global_run_state", new_state)
            except Exception:
                pass

            update_buttons()

        btns["Start"] = ttk.Button(sub_frame, text="Start", width=8, command=lambda: set_state("running"))
        btns["Stop"] = ttk.Button(sub_frame, text="Stop", width=8, command=lambda: set_state("stopped"))
        btns["Pause"] = ttk.Button(sub_frame, text="Pause", width=8, command=lambda: set_state("paused"))
        btns["Resume"] = ttk.Button(sub_frame, text="Resume", width=8, command=lambda: set_state("running"))

        btns["Start"].grid(row=0, column=0)
        btns["Stop"].grid(row=0, column=1)
        btns["Pause"].grid(row=1, column=0)
        btns["Resume"].grid(row=1, column=1)

        update_buttons()
        self.module_buttons[label] = update_buttons

    def update_alert_option(self, name, value):
        try:
            from core import alerts
            alerts.set_alert_flag(name, value)
        except Exception as e:
            print(f"[GUI] Failed to update alert option: {e}")

    def _flash_widget(self, widget, color="#228B22", duration=500):
        orig = widget.cget("background") if hasattr(widget, "cget") else None
        try:
            widget.config(background=color)
            widget.after(duration, lambda: widget.config(background=orig))
        except Exception:
            pass

    def refresh_loop(self):
        try:
            from core import alerts
            stats = get_current_metrics()
            # Sync checkbox states with ALERT_FLAGS
            for name, var in self.checkbox_vars.items():
                var.set(alerts.ALERT_FLAGS.get(name, False))
            # Update module button states based on metrics
            status_dict = stats.get("thread_health_flags", stats.get("status", {}))
            for label, updater in self.module_buttons.items():
                state = "running" if status_dict.get(label.lower(), False) else "stopped"
                if self.module_states.get(label) != state:
                    self.module_states[label] = state
                    updater()
            for key, widget in self.metrics.items():
                # Compute backlog progress locally
                if key == "backlog_progress":
                    backlog = stats.get("backlog_files_queued", 0) or 0
                    rechecked = stats.get("csv_rechecked_today", 0) or 0
                    total = backlog + rechecked
                    progress = (rechecked / total) * 100 if total > 0 else 100
                    widget["value"] = progress
                    pct_lbl = self.metrics.get("backlog_percent")
                    if pct_lbl:
                        pct_lbl.config(text=f"{progress:.1f}%")
                    continue

                value = stats.get(key, "N/A")
                if isinstance(widget, ttk.Progressbar):
                    try:
                        widget["value"] = float(value.strip('%')) if isinstance(value, str) else float(value)
                    except:
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
                            for mod, running in value.items():
                                title = name_map.get(mod, mod.title())
                                icon = "✅" if running else "❌"
                                state = "Running" if running else "Stopped"
                                lines.append(f"{title}: {state} {icon}")
                        elif key == "gpu_assignments":
                            name_map = {
                                "vanitysearch": "VanitySearch",
                                "altcoin_derive": "Altcoin Derive",
                            }
                            for mod, name in value.items():
                                title = name_map.get(mod, mod.replace('_', ' ').title())
                                lines.append(f"{title} → {name}")
                        elif key in ("matches_found_lifetime", "addresses_checked_lifetime", "addresses_checked_today"):
                            items = [f"{k.upper()}: {v}" for k, v in value.items()]
                            for i in range(0, len(items), 3):
                                lines.append("   ".join(items[i:i+3]))
                        else:
                            for gid, info in value.items():
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
                        lines.append(str(value))
                    widget.config(state="normal")
                    widget.delete("1.0", "end")
                    widget.insert("end", "\n".join(lines) or "N/A")
                    widget.config(state="disabled")
                else:
                    if isinstance(value, dict):
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
        except Exception as e:
            print(f"[Dashboard Error] {e}")
        self.master.after(int(DASHBOARD_REFRESH_INTERVAL * 1000), self.refresh_loop)

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
