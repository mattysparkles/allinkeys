# 🧠 AllInKeys — Modular Key Discovery System

AllInKeys is a Python toolkit for discovering and monitoring cryptocurrency keys and addresses. It wraps GPU-accelerated tools like VanitySearch (CUDA/NVIDIA) and Vanitygen++ (OpenCL/AMD) and adds a modular pipeline for downloading balance lists, deriving altcoin addresses, checking matches, and notifying you via encrypted alerts or a live dashboard.

The key generator can run NVIDIA and AMD GPUs in parallel, producing separate output files for each backend so that one workflow can continue even if the other encounters an error.

## 🚧 Project Status

This repository was recently opened to the public and remains a work in progress. Modules are actively being refactored and new features are added frequently.

> 🔐 Whether you're a security researcher, digital archaeologist, or white‑hat crypto enthusiast, AllInKeys is a modular suite for probing and understanding blockchain address keyspace.

---

## ⚙️ Installation & Setup

### 🧱 Requirements

* Python 3.9+
* Git
* Optional: CUDA/OpenCL drivers for GPU support

```bash
git clone https://github.com/mattysparkles/allinkeys.git
cd allinkeys
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in any credentials needed for alert channels (email, Telegram, Twilio, etc.).

### 📁 Directory Overview

```
allinkeys/
├── alerts/                  # Alert sounds and assets
├── bin/                     # Third‑party binaries (VanitySearch, Vanitygen++)
├── config/
│   ├── settings.py          # Master configuration
│   ├── constants.py         # Shared constants
│   └── coin_definitions.py  # Column mapping per coin
├── core/
│   ├── keygen.py            # Bitcoin key generation (VanitySearch wrapper)
│   ├── altcoin_derive.py    # Seed → WIF + altcoin address derivation
│   ├── csv_checker.py       # CSV address matching logic
│   ├── downloader.py        # Balance list downloader
│   ├── backlog.py           # Convert VanitySearch output to CSV
│   ├── gpu_scheduler.py     # Assign work across GPUs
│   ├── gpu_selector.py      # GPU role selection helpers
│   ├── alerts.py            # PGP, desktop, Telegram, etc.
│   ├── checkpoint.py        # Save/restore keygen progress
│   ├── logger.py            # Central logging setup
│   ├── dashboard.py         # Metrics for the GUI
│   └── utils/               # Misc helpers
├── ui/
│   └── dashboard_gui.py     # Tkinter-based dashboard
├── utils/
│   ├── balance_checker.py
│   ├── file_utils.py
│   ├── network_utils.py
│   ├── pgp_utils.py
│   └── puzzle.py
├── Downloads/               # Downloaded funded address lists
├── logs/                    # Runtime logs and checkpoints
├── output/
│   └── csv/                 # Converted address batches
├── vanity_output/           # Raw VanitySearch batches (.txt)
├── .env.example
├── main.py                  # Orchestrates modules
└── requirements.txt
```

---

## 🧩 Configuration

### 🛠 `settings.py` (in `/config`)
All runtime behaviour is configured in `config/settings.py`. Tweak this file to enable or disable modules, change GPU strategy, alert options and more.

Example snippet:

```python
USE_GPU = True
ENABLE_ALERTS = True
ENABLE_BACKLOG_CONVERSION = True
CHECKPOINT_INTERVAL_SECONDS = 30
PGP_PUBLIC_KEY_PATH = os.path.join(BASE_DIR, "my_pgp_key.asc")
```

---

## 🖥 Usage

### 🔹 Default Run

```bash
python main.py
```

The default run will:

- Restore or create checkpoints
- Download funded address lists
- Start the GUI dashboard
- Launch key generation and CSV monitoring
- Convert VanitySearch backlog to CSV
- Send match alerts if enabled

### 🔸 Command Line Options

`python main.py --help` displays all flags. Common examples:

| Flag | Description |
|------|-------------|
| `--skip-backlog` | Start without backlog conversion |
| `--no-dashboard` | Do not launch the GUI dashboard |
| `--skip-downloads` | Skip downloading balance files |
| `--headless` | Run without any GUI components |
| `--match-test` | Trigger a fake match alert on startup |
| `--only <coins>` | Restrict processing to coin flow(s); comma-separated list |
| `--addr-format {compressed,uncompressed}` | BTC-only: choose address format |
| `--compressed` / `--uncompressed` | BTC-only convenience flags overriding `--addr-format` |
| `--all` | BTC-only: use "all BTC addresses ever used" range mode |
| `--funded` | BTC-only: use daily funded BTC list |
| `--puzzle N` | BTC puzzle mode for puzzle number `N` |
| `--every` | With `--puzzle`: keep generic `1**` prefix |
| `--target` | With `--puzzle`: target specific puzzle address (default) |
| `--chunk INDEX` | With `--puzzle`: start at chunk `INDEX` (0-based) |

### 🧩 BTC Puzzle Mode

Use the Bitcoin puzzle challenge ranges by supplying `--puzzle` with a puzzle number.
The generator targets the published address by default (`--target`); use `--every`
to keep the generic `1**` prefix and search the entire range. Puzzle mode
automatically enables compressed addresses and is typically paired with
`--only btc` to run a lightweight search.

Puzzle ranges are divided into ~1M-key chunks and tracked in a SQLite database
(`logs/work_queue.db`) so multiple workers do not overlap. Progress within a
chunk is saved to `logs/puzzleN_checkpoint.json`, enabling restart after
interruptions. Pass `--chunk` with a zero-based index to claim a specific
starting chunk.

```bash
python main.py --only btc --puzzle 71            # target puzzle 71 address
python main.py --only btc --puzzle 71 --every    # search full puzzle range
python main.py --only btc --puzzle 71 --chunk 5  # resume at chunk 5
```

---

## 🧪 Features by Module

| Feature                         | Module                     | Config Toggle / Notes               |
|---------------------------------|----------------------------|------------------------------------|
| GPU Vanity Key Generation       | `core/keygen.py`           | `USE_GPU`, `VANITY_PATTERN`, etc.  |
| Altcoin Address Derivation      | `core/altcoin_derive.py`   | `ENABLE_ALTCOIN_DERIVATION`        |
| CSV Address Checking            | `core/csv_checker.py`      | `ENABLE_DAY_ONE_CHECK`, `ENABLE_UNIQUE_RECHECK` |
| Daily Download of Lists         | `core/downloader.py`       | auto-enabled                       |
| Vanity Output → CSV Backlog     | `core/backlog.py`          | `ENABLE_BACKLOG_CONVERSION`        |
| GPU Scheduling                  | `core/gpu_scheduler.py`    | `GPU_STRATEGY`                     |
| GPU Role Assignment             | `core/gpu_selector.py`     | `VANITY_GPU_INDEX`, `ALTCOIN_GPUS_INDEX` |
| Alerts (PGP, audio, popup...)   | `core/alerts.py`           | `ENABLE_ALERTS`, `PGP_PUBLIC_KEY_PATH` |
| Live System Dashboard           | `ui/dashboard_gui.py`      | `ENABLE_DASHBOARD`, `ENABLE_GUI`   |
| Logging                         | `core/logger.py`           | `LOG_LEVEL`, `LOG_TO_FILE`         |
| Checkpoint Save/Restore         | `core/checkpoint.py`       | `CHECKPOINT_INTERVAL_SECONDS`      |

---

## 🔔 Supported Alert Channels

- 🔊 Audio file alert (`.wav`, `.mp3`)
- 🖥 Desktop popup window
- 🔐 PGP‑encrypted email (SMTP)
- 📩 Telegram bot
- 📱 SMS / phone call via Twilio
- 💬 Discord webhook
- 🏠 Home Assistant integration
- ☁️ Upload match files to iCloud, Dropbox, Google Drive

---

## 🔐 Example: Add Your PGP Key

```bash
gpg --armor --export you@example.com > my_pgp_key.asc
```

Then set in `settings.py`:

```python
ENABLE_PGP = True
PGP_PUBLIC_KEY_PATH = os.path.join(BASE_DIR, "my_pgp_key.asc")
```

---

## 🧰 Tools Used

- Python 3.9+
- PGPy for OpenPGP
- VanitySearch for GPU keygen
- PyInstaller (optional, for `.exe`)
- Tkinter + psutil for dashboard

---

## 📦 Building into `.exe`

```bash
pip install pyinstaller
pyinstaller --onefile main.py
```

Produces `dist/main.exe` — a standalone binary.

---

## 💵 Donate to Support Development

| Coin | Address |
|------|---------|
| BTC  | `18RWVyEciKq8NLz5Q1uEzNGXzTs5ivo37y` |
| DOGE | `DPoHJNbYHEuvNHyCFcUnvtTVmRDMNgnAs5` |
| ETH  | `0xCb8B2937D60c47438562A2E53d08B85865B57741` |

---

## 🧩 Included Component: VanitySearch Binary (MIT License)

This project includes a precompiled binary of **VanitySearch**, a GPU‑accelerated Bitcoin vanity address generator.

`bin/VanitySearch.exe` comes from a third‑party MIT‑licensed fork. See `third_party_licenses.md` for details.

- **Original project**: [VanitySearch by Jean-Luc Pons](https://github.com/JeanLucPons/VanitySearch)
- **License**: MIT
- **Binary origin**: Third‑party fork with deterministic seed search
- **Compiler**: Provided by the forked project

We make **no claims or guarantees** about the performance, security or accuracy of the included VanitySearch binary. Use at your own discretion.

> If you are the author of the specific fork used and would like attribution or changes, feel free to open an issue or PR.

**License Notice**: The original VanitySearch project and most forks are distributed under the MIT License. A copy of the license is included below.

---

## 🚨 Legal Notice

AllInKeys is provided for **educational and research use only**. The authors do not condone or support illegal behaviour. Use responsibly.

🧠 _Created with love and paranoia by Sparkles_

