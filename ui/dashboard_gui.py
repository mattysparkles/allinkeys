# dashboard_gui.py – Extended Full Metrics and Controls Dashboard

import tkinter as tk
from tkinter import ttk, messagebox
import os
import subprocess
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
    DASHBOARD_PASSWORD
)
from core.dashboard import get_current_metrics, reset_all_metrics


class DashboardGUI:
    def __init__(self, master):
        self.master = master
        self.master.title("ALLINKEYS Live Dashboard")
        self.metrics = {}
        self.checkbox_vars = {}
        self.module_states = {}
        self.create_widgets()
        self.refresh_loop()

    def create_widgets(self):
        # Centered ASCII Logo
        logo_frame = tk.Frame(self.master)
        logo_frame.pack(fill="x", padx=10)
        logo_label = tk.Label(logo_frame, text=LOGO_ASCII, font=("Courier", 7), justify="center")
        logo_label.pack()

        # Organized Metric Group Boxes
        self.section_frame = tk.Frame(self.master)
        self.section_frame.pack(fill="both", expand=True, padx=10)

        # Group all active metrics from settings.py
        grouped_keys = {
            "System Stats": [],
            "Keygen Metrics": [],
            "CSV Checker": [],
            "Match Info": [],
            "Backlog": [],
            "Uptime & Misc": []
        }

        for key in STATS_TO_DISPLAY:
            if not STATS_TO_DISPLAY[key]:
                continue
            label = METRICS_LABEL_MAP.get(key, key)
            key_lower = key.lower()
            if any(x in key_lower for x in ["cpu", "ram", "disk"]):
                grouped_keys["System Stats"].append((key, label))
            elif any(x in key_lower for x in ["keygen", "keys_per_sec", "batches"]):
                grouped_keys["Keygen Metrics"].append((key, label))
            elif any(x in key_lower for x in ["csv", "address"]):
                grouped_keys["CSV Checker"].append((key, label))
            elif "match" in key_lower:
                grouped_keys["Match Info"].append((key, label))
            elif "backlog" in key_lower:
                grouped_keys["Backlog"].append((key, label))
            else:
                grouped_keys["Uptime & Misc"].append((key, label))

        row = 0
        col = 0
        max_cols = 3
        for group, keys in grouped_keys.items():
            if not keys:
                continue
            frame = tk.LabelFrame(self.section_frame, text=group, padx=10, pady=5, font=("Arial", 10, "bold"))
            frame.grid(row=row, column=col, padx=5, pady=5, sticky="nsew")
            self.section_frame.grid_columnconfigure(col, weight=1, uniform="metric")
            for i, (key, label_text) in enumerate(keys):
                tk.Label(frame, text=label_text + ":", anchor="e").grid(row=i, column=0, sticky="e")
                if "usage" in key or "keys_per_sec" in key:
                    pb = ttk.Progressbar(frame, length=100, mode="determinate")
                    pb.grid(row=i, column=1, sticky="w")
                    self.metrics[key] = pb
                else:
                    lbl = tk.Label(frame, text="Loading...", anchor="w", fg="blue")
                    lbl.grid(row=i, column=1, sticky="w")
                    self.metrics[key] = lbl
            col += 1
            if col >= max_cols:
                col = 0
                row += 1

        # Match Alert Methods
        alert_frame = tk.LabelFrame(self.master, text="Match Alert Methods", padx=10, pady=5)
        alert_frame.pack(padx=10, pady=(5, 0), fill="x")

        third = (len(ALERT_CHECKBOXES) + 2) // 3
        for idx, option in enumerate(ALERT_CHECKBOXES):
            row = idx // third
            col = (idx % third) * 2
            var = tk.BooleanVar(value=ALERT_OPTIONS.get(option, False))
            self.checkbox_vars[option] = var
            cb = tk.Checkbutton(alert_frame, text=option, variable=var)
            cb.grid(row=row, column=col, sticky="w", padx=(0, 2))
            if ALERT_CREDENTIAL_WARNINGS.get(option):
                tk.Label(alert_frame, text="⚠", fg="red").grid(row=row, column=col + 1)

        # Control Buttons
        btn_frame = tk.Frame(self.master)
        btn_frame.pack(pady=10)
        col = 0
        for label in ["vanity", "altcoin", "csv_check", "csv_recheck", "alerts"]:
            if BUTTONS_ENABLED.get(label):
                self._group_button_set(btn_frame, label.capitalize(), col)
                col += 1

        # Reset + Config + Delete
        control_frame = tk.Frame(self.master)
        control_frame.pack(pady=(0, 10))
        tk.Button(control_frame, text="Open Config File", command=self.open_config_file).pack(side="left", padx=5)
        tk.Button(control_frame, text="Reset Metrics", command=self.reset_metrics_prompt).pack(side="left", padx=5)
        tk.Button(control_frame, text="Delete All Data", command=self.delete_data_prompt).pack(side="left", padx=5)

    def _group_button_set(self, parent, label, col):
        sub_frame = tk.LabelFrame(parent, text=label)
        sub_frame.grid(row=0, column=col, padx=5)
        self.module_states[label] = "stopped"

        btns = {}

        def update_buttons():
            state = self.module_states[label]
            for bname, btn in btns.items():
                if state == "running" and bname == "Start":
                    btn.config(text="RUNNING", bg="green", fg="white")
                elif state == "stopped" and bname == "Stop":
                    btn.config(text="STOPPED", bg="red", fg="white")
                elif state == "paused" and bname == "Pause":
                    btn.config(text="PAUSED", bg="goldenrod", fg="black")
                else:
                    btn.config(text=bname, bg="SystemButtonFace", fg="black")

        def set_state(new_state):
            self.module_states[label] = new_state
            update_buttons()

        btns["Start"] = tk.Button(sub_frame, text="Start", width=8, command=lambda: set_state("running"))
        btns["Stop"] = tk.Button(sub_frame, text="Stop", width=8, command=lambda: set_state("stopped"))
        btns["Pause"] = tk.Button(sub_frame, text="Pause", width=8, command=lambda: set_state("paused"))
        btns["Resume"] = tk.Button(sub_frame, text="Resume", width=8, command=lambda: set_state("running"))

        btns["Start"].grid(row=0, column=0)
        btns["Stop"].grid(row=0, column=1)
        btns["Pause"].grid(row=1, column=0)
        btns["Resume"].grid(row=1, column=1)

        update_buttons()

    def refresh_loop(self):
        try:
            stats = get_current_metrics()
            for key, widget in self.metrics.items():
                value = stats.get(key, "N/A")
                if isinstance(widget, ttk.Progressbar):
                    try:
                        widget["value"] = float(value.strip('%')) if isinstance(value, str) else float(value)
                    except:
                        widget["value"] = 0
                else:
                    widget.config(text=value)
        except Exception as e:
            print(f"[Dashboard Error] {e}")
        self.master.after(int(DASHBOARD_REFRESH_INTERVAL * 1000), self.refresh_loop)

    def open_config_file(self):
        if os.path.exists(CONFIG_FILE_PATH):
            subprocess.Popen(["notepad.exe", CONFIG_FILE_PATH])
        else:
            messagebox.showerror("Missing Config", f"Could not find: {CONFIG_FILE_PATH}")

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
    root = tk.Tk()
    style = ttk.Style()
    style.theme_use("clam")
    app = DashboardGUI(root)
    root.mainloop()


if __name__ == "__main__":
    start_dashboard()
