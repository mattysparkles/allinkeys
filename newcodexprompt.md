I’m working on a Python project with a real-time terminal-based dashboard that displays metrics and includes control buttons (Start, Stop, Pause, Resume) for multiple subsystems like VanitySearch, Altcoin Derivation, CSV Checking, etc.

Right now, I’m experiencing three main issues:
1. **Metrics are not updating or displaying correctly** — values remain static or missing.
2. **Dashboard buttons do not work as intended** — for example, the "Start" button doesn't trigger the respective subsystem or reflect the running state visually.
3. **Alert methods (email, audio, PGP, Telegram, etc.) do not activate based on the settings file, and the GUI checkboxes don’t seem to alter or reflect the settings properly.**

Please do the following:

🔍 Analyze these files:
- `settings.py` — contains all metric toggles, display flags, alert settings, and button control flags.
- `dashboard.py` — manages real-time stats, metrics, and visual updates.
- `dashboard_gui.py` — handles GUI layout, button rendering, checkboxes, and user interactions.
- `main.py` — initializes and starts subsystems, dashboard, and control flow.
- Any relevant modules (e.g., `core/keygen.py`, `alerts.py`, `csv_checker.py`, `altcoin_derive.py`) for runtime process and alert handling.

🎯 Goals:

#### METRICS
- Identify why metric values from `settings.py` (like `SHOW_KEYS_PER_SEC`, `SHOW_MATCHES_TODAY`, etc.) aren’t updating live on the dashboard.
- Check how stats are passed to the dashboard update loop (shared state, globals, etc.).
- Ensure metrics are connected to actual output of the keygen, altcoin, and csv subprocesses.

#### BUTTONS
- Diagnose why Start/Stop/Pause/Resume buttons do not invoke their intended functions.
- Check if button callbacks are properly linked to backend process control.
- Determine why buttons don’t reflect a “Running” state at launch even when subprocesses are already active.

#### ALERTS + CHECKBOXES
- Investigate why alert methods (email, audio, SMS, desktop popup, etc.) defined in `settings.py` (e.g., `ENABLE_ALERTS`, `ALERT_EMAIL_ENABLED`, `ENABLE_AUDIO_ALERT_LOCAL`, etc.) are not being activated when a match is found.
- Determine whether the checkboxes in the dashboard GUI (`ALERT_CHECKBOXES` and `ALERT_OPTIONS`) override, mirror, or are completely disconnected from the settings in `settings.py`.
- Verify if changing a checkbox actually modifies alert behavior in real-time or if it requires a restart or re-sync.
- If broken, suggest or implement proper binding between checkbox state and runtime alert logic.

⚙️ Notes:
- GUI alert checkboxes reference `ALERT_OPTIONS` and `ALERT_CHECKBOXES` mappings from `settings.py`.
- Match alerts are triggered by functions in `alerts.py`.
- I want a setup where toggling a checkbox dynamically changes whether a given alert (e.g., Telegram) will fire without requiring a script restart.

Please inspect the update loop, GUI bindings, and alert conditions, then refactor or document what’s currently broken or disconnected. Recommend a solution or implement one if possible.
