# 🧠 AllInKeys — Modular Key Discovery System

AllInKeys is a high-performance Python-based tool designed for Bitcoin and altcoin key discovery, address monitoring, and wallet analysis. It supports GPU-accelerated key generation, altcoin derivation, vanity address searches, PGP-encrypted alerts, daily balance list scanning, and a live system dashboard.

## 🚧 Project Status

This repository was recently opened to the public and is still a work in progress. Several modules are being refactored and may not behave perfectly yet. I wanted to share the code early for fun and to get feedback while the remaining pieces are fixed up.

> 🔐 Whether you're a security researcher, digital archaeologist, or white-hat crypto enthusiast, AllInKeys is a fully modular suite for probing and understanding blockchain address keyspace.

---

## ⚙️ Installation & Setup

### 🧱 Requirements

Install Python 3.9+ and Git first, then:

```bash
git clone https://github.com/mattysparkles/allinkeys.git
cd allinkeys
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 📁 Directory Structure

```
allinkeys/
├── config/
│   ├── settings.py           # Master config for all modules
│   ├── coin_definitions.py   # Address column mapping by coin
├── core/
│   ├── keygen.py             # Bitcoin key generator (VanitySearch)
│   ├── altcoin_derive.py     # Seed → WIF + address derivation
│   ├── downloader.py         # Balance list downloader
│   ├── csv_checker.py        # CSV address matching logic
│   ├── logger.py             # Logger system
│   ├── alerts.py             # All alert types (PGP, popup, email, etc.)
│   ├── checkpoint.py         # Save/restore progress
│   ├── pgp_utils.py          # PGP encryption helpers
├── ui/
│   ├── dashboard_gui.py      # Tkinter-based system dashboard
├── Downloads/                # Funded address lists
├── VanityOutput/             # Raw VanitySearch output
├── CSVs/                     # Altcoin-derived address files
├── Matches/                  # Alert match logs
├── main.py                   # Central orchestration script
├── README.md
├── requirements.txt
```

---

## 🧩 Configuration

### 🛠 settings.py (Found in `/config/`)

All runtime behavior is controlled from `settings.py`, a Python-based config file.

Example:

```python
USE_GPU = True
CHECKPOINT_ENABLED = True
CHECKPOINT_INTERVAL_SECONDS = 30
ENABLE_ALERTS = True
ENABLE_PGP = True
PGP_PUBLIC_KEY_PATH = os.path.join(BASE_DIR, "my_pgp_key.asc")
ENABLE_KEYGEN = True
ENABLE_DASHBOARD = True
ENABLE_UNIQUE_RECHECK = True
```

> ✅ Edit this file directly. It acts like `raspi-config` — all modules read from it.

---

## 🖥 Usage

### 🔹 Default Run

Launch the full system:

```bash
python main.py
```

This will:
- Load or create a checkpoint
- Download address lists (BTC, ETH, DOGE, etc.)
- Start the GUI dashboard
- Begin key generation and CSV monitoring
- Trigger match alerts if enabled

---

## 🧪 Features by Module

| Feature                     | Module                 | Config Setting                            |
|----------------------------|------------------------|--------------------------------------------|
| GPU Vanity Key Generation  | `core/keygen.py`       | `USE_GPU`, `PATTERN`, `MAX_BATCH_SIZE`     |
| Altcoin Address Derivation | `core/altcoin_derive.py` | `ENABLE_ALTCOIN_DERIVATION`                |
| CSV Address Check          | `core/csv_checker.py`  | `ENABLE_DAY_ONE_CHECK`, `ENABLE_UNIQUE_RECHECK` |
| Daily Download of Lists    | `core/downloader.py`   | N/A (auto-enabled)                         |
| Alerts (PGP, audio, popup) | `core/alerts.py`       | `ENABLE_ALERTS`, `PGP_PUBLIC_KEY_PATH`, etc. |
| Live System Dashboard      | `ui/dashboard_gui.py`  | `ENABLE_DASHBOARD`, various SHOW_* flags   |
| Logging                    | `core/logger.py`       | `LOG_LEVEL`, `LOG_TO_FILE`                 |
| Checkpoint Save/Restore    | `core/checkpoint.py`   | `CHECKPOINT_ENABLED`, `CHECKPOINT_PATH`    |

---

## 🔔 Supported Alert Channels

- 🔊 Audio file alert (`.wav`, `.mp3`)
- 🖥 Desktop popup window
- 🔐 PGP-encrypted email (SMTP)
- 📩 Telegram bot
- 📱 SMS / phone call via Twilio
- 💬 Discord webhook
- 🏠 Home Assistant integration
- ☁️ Upload match files to: iCloud, Dropbox, Google Drive

---

## 🔐 Example: Add Your PGP Key

To enable match alerts via encrypted PGP:

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

Yields `dist/main.exe` — standalone binary.

---

## 💵 Donate to Support Development

| Coin | Address |
|------|---------|
| BTC  | `18RWVyEciKq8NLz5Q1uEzNGXzTs5ivo37y` |
| DOGE | `DPoHJNbYHEuvNHyCFcUnvtTVmRDMNgnAs5` |
| ETH  | `0xCb8B2937D60c47438562A2E53d08B85865B57741` |

---

## 🧩 Included Component: VanitySearch Binary (MIT License)

This project includes a precompiled binary of **VanitySearch**, a GPU-accelerated Bitcoin vanity address generator.
### Binary Tools

`bin/VanitySearch.exe`, a precompiled binary from a third-party MIT-licensed fork of [VanitySearch](https://github.com/JeanLucPons/VanitySearch). See `third_party_licenses.md` for details.


- **Original project**: [VanitySearch by Jean-Luc Pons](https://github.com/JeanLucPons/VanitySearch)
- **License**: MIT
- **Binary origin**: A third-party fork of the VanitySearch project that adds seed-based deterministic search capability.
- **Compiler**: Not compiled by us — this executable was distributed by the forked project directly.

We make **no claims or guarantees** about the performance, security, or accuracy of the included VanitySearch binary. Use at your own discretion.

> If you are the author of the specific fork used, and would like attribution or changes, feel free to open an issue or PR.

---

**License Notice**: The original VanitySearch project and most of its forks are distributed under the MIT License. A copy of the license is included below.

## 🚨 Legal Notice

AllInKeys is provided for **educational and research use only**. The authors do not condone or support illegal behavior. Use responsibly.

🧠 _Created with love and paranoia by Sparkles_
