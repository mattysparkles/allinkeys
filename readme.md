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
* Optional: CUDA/OpenCL drivers and `pyopencl` for GPU support
* Optional: additional development tools in `requirements-dev.txt` if you plan to run tests or linters

```bash
git clone https://github.com/mattysparkles/allinkeys.git
cd allinkeys
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
# For development and tests
pip install -r requirements-dev.txt
```

Copy `.env.example` to `.env` and fill in any credentials needed for alert channels (email, Telegram, Twilio, etc.).

## 🚀 Quick Start

1. **Start the full pipeline (default vanity mode)**

   ```bash
   python main.py
   ```

   The orchestration script launches key generation, backlog conversion, and any enabled alert or dashboard modules according to `config/settings.py`.

   You can also explicitly select a mode:

   ```bash
   python main.py --mode vanity
   python main.py --mode btc_only
   python main.py --mode mnemonic
   ```

2. **Run individual modules**

   Each component can be invoked directly for isolated testing or debugging:

   ```bash
   python -m core.keygen           # GPU/CPU vanity key generation
   python -m core.altcoin_derive   # Derive altcoin addresses from seeds
   python -m core.csv_checker      # Compare generated addresses to funded lists
   python -m ui.dashboard_gui      # Launch Tkinter dashboard only
   ```

3. **Run in Docker**

   ```bash
   docker compose up --build
   ```

   Use the Compose file to scale across multiple GPUs or hosts.

4. **Run tests**

   ```bash
   pytest
   ```
   Requires packages from `requirements-dev.txt`.

## 📡 Telemetry

Minimal, opt‑out telemetry helps guide project development. Only anonymized
seed processing statistics are sent to a privacy‑safe central service through an encrypted queue.
See [docs/TELEMETRY.md](docs/TELEMETRY.md) for full details. Disable telemetry at runtime
with the `--no-telemetry` command-line flag.

You can explore the live telemetry dashboard at [https://telemetry.sparkleserver.site](https://telemetry.sparkleserver.site). The site shows all of your registered machines, live performance metrics, actual seed ranges and their distribution, granular machine controls, and the neighbor lookup map that highlights searched ranges plus the closest peers. Every page links back to the [GitHub repository](https://github.com/mattysparkles/allinkeys) so visitors can download the same code that feeds the dashboard.

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
│   ├── csv/                 # Converted address batches
│   ├── vanity_output/       # Raw VanitySearch batches (.txt)
│   └── mnemonic_output/     # Mnemonic mode output
├── .env.example
├── main.py                  # Orchestrates modules
└── requirements.txt
```

VanitySearch results are saved under `output/vanity_output/`.

## 🐳 Docker

A Docker setup is included for running AllInKeys in a containerized
environment.

### Build the Image

```bash
docker build -t allinkeys .
```

### Run a Single Container

```bash
docker run --gpus all allinkeys
```

### Scale with Docker Compose

The provided `docker-compose.yml` supports multi‑GPU or clustered
deployments. Increase the number of replicas to distribute work across
GPUs or nodes:

```bash
docker compose up --scale allinkeys=2
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
RETENTION_DAYS = 30  # how long to keep downloaded files
```

### 🌐 Path Customization

AllInKeys stores logs, downloads and output under the repository root by default. You can override these locations with environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `ALLINKEYS_BASE_DIR` | repo root | Base directory used for relative paths |
| `ALLINKEYS_LOG_DIR` | `BASE_DIR/logs` | Where log files and checkpoints are written |
| `ALLINKEYS_CSV_DIR` | `BASE_DIR/output/csv` | Converted CSV output |
| `ALLINKEYS_DOWNLOADS_DIR` | `BASE_DIR/Downloads` | Downloaded address lists |
| `ALLINKEYS_MATCHES_DIR` | `BASE_DIR/matches` | Archive of matches and alerts |
| `ALLINKEYS_VANITY_OUTPUT_DIR` | `BASE_DIR/output/vanity_output` | VanitySearch text batches |
| `ALLINKEYS_MNEMONIC_TXT_DIR` | `BASE_DIR/output/mnemonic_output` | Mnemonic mode output |

To retain the legacy top-level ``vanity_output`` directory, set
``ALLINKEYS_VANITY_OUTPUT_DIR`` to ``BASE_DIR/vanity_output``.

Example:

```bash
export ALLINKEYS_LOG_DIR=/var/tmp/allinkeys/logs
export ALLINKEYS_CSV_DIR=/data/allinkeys/csv
python main.py
```

### ⚙️ VanitySearch Tuning

Use these environment variables to control VanitySearch tuning:

| Variable | Default | Description |
|----------|---------|-------------|
| `ALLINKEYS_VANITYSEARCH_AUTOTUNE` | `true` | Enable auto-tuning for `-g` and `-m`. |
| `ALLINKEYS_VANITYSEARCH_AUTOTUNE_GRID` | `false` | Enable auto-tuning for `-g` (grid size). |
| `ALLINKEYS_VANITYSEARCH_MAX_FOUND` | unset | Override `-m` (maxFound) directly. |
| `ALLINKEYS_VANITYSEARCH_GPU_THREADS` | unset | Override `-g` (GPU threads/grid size) directly. |
| `ALLINKEYS_VANITYSEARCH_GPU_ID_ARGUMENT` | `false` | Enable passing a GPU id flag (see `ALLINKEYS_VANITYSEARCH_GPU_ID_FLAG`). |
| `ALLINKEYS_VANITYSEARCH_GPU_ID_FLAG` | `gpuId` | GPU id flag name, e.g. `gpuId` for `-gpuId <id>`. |

Example:

```bash
export ALLINKEYS_VANITYSEARCH_AUTOTUNE=false
export ALLINKEYS_VANITYSEARCH_MAX_FOUND=1000000
export ALLINKEYS_VANITYSEARCH_GPU_THREADS=4096
python main.py
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
| `--mode {btc_only,vanity,mnemonic}` | Select execution mode (default: `vanity`) |
| `--skip-backlog` | Start without backlog conversion |
| `--no-dashboard` | Do not launch the GUI dashboard |
| `--dashboard-password <pw>` | Protect dashboard with password `pw` |
| `--skip-downloads` | Skip downloading balance files |
| `--headless` | Run without any GUI components |
| `--no-telemetry` | Disable telemetry reporting |
| `--telemetry-setup` | Run the telemetry setup wizard |
| `--auth-token <token>` | Bearer token for authenticated telemetry endpoints |
| `--control-url <url>` | Override the telemetry machine control polling URL |
| `--match-test` | Trigger a fake match alert on startup |
| `--purge [days]` | Delete old files (default 30) in `output/vanity_output/` and `output/csv/`, then exit |
| `--dry-run` | Preview purge actions without deleting |
| `--only <coins>` | Restrict processing to coin flow(s); comma-separated list |
| `--addr-format {compressed,uncompressed}` | BTC-only: choose address format |
| `--compressed` / `--uncompressed` | BTC-only convenience flags overriding `--addr-format` |
| `--all` | BTC-only: use "all BTC addresses ever used" range mode |
| `--funded` | BTC-only: use daily funded BTC list |
| `--gpu-index <id>` | Force use of a specific GPU device index |
| `--puzzle N` | BTC puzzle mode for puzzle number `N` |
| `--every` | With `--puzzle`: keep generic `1**` prefix |
| `--target` | With `--puzzle`: target specific puzzle address (default) |
| `--chunk INDEX` | With `--puzzle`: start at chunk `INDEX` (0-based) |
| `-v`, `--vanity <pattern>` | Custom VanitySearch prefix/pattern (e.g., `1nasty`) |
| `-q`, `--case-insensitive` | Case-insensitive VanitySearch matching |
| `--enable-bc1` | Enable bc1 (Bech32 v0/v1) address generation alongside legacy P2PKH |
| `--bc1` | Use bech32 funded list, disable legacy P2PKH, and default pattern to `bc1**` |

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

### 🧬 VanitySearch + bc1 (Bech32) Options

Use these flags when targeting bc1 (Bech32/Bech32m) addresses:

- `--enable-bc1` turns on bc1q (P2WPKH) and bc1p (Taproot) generation while still
  retaining legacy P2PKH (`1...`) matches.
- `--bc1` switches the funded list to Bech32 only, disables legacy P2PKH, and
  sets the default vanity pattern to `bc1**` (unless `--puzzle` or `--vanity`
  is supplied).

VanitySearch-specific pattern helpers:

- `-v/--vanity <pattern>` customizes the vanity prefix/pattern.
- `-q/--case-insensitive` enables case-insensitive matching.

Example invocations:

```bash
python main.py --enable-bc1 --vanity bc1qak**          # bc1q with custom prefix
python main.py --bc1 --case-insensitive                # bc1-only funded list + pattern
python main.py --bc1 --vanity bc1pdead**               # taproot-style prefix
```

### 🧠 Mnemonic Mode

AllInKeys can also generate BIP‑39 mnemonics and derive keys directly
without running VanitySearch. Enable it with `--mode mnemonic` (or the
legacy `--mnemonic` flag) and select the
mnemonic length via flags like `--12words` or `--24words`.  Output files
are written to `output/mnemonic_output/`.  All related options are grouped under
**Mnemonic Mode** in `python main.py --help`.

Example invocations:

```bash
python main.py --mnemonic --12words                          # 12‑word mnemonic → BTC address
python main.py --mnemonic --24words --coins btc,eth --atomic  # BTC + ETH using Atomic paths
```

Additional options mirror the specification:

- `--bip39` (default) or `--custom-words-file <path>` choose the word list
- Language options `--spanish`, `--french`, `--italian`, `--japanese`,
  `--korean`, `--czech`, `--portuguese`, `--chinese`, `--chinese-simple`
  select a BIP‑39 wordlist
- `--coins btc,eth` or `--allcoins` choose which coins to derive
- `--atomic`, `--ledger`, `--trezor`, `--coinomi`, `--trust` wallet path presets
- `--path`, `--btc-path`, `--eth-path`, … supply explicit derivation paths
- `--rng-seed <n>` deterministic mnemonic generation for testing
- `--gpu` / `--no-gpu` toggle OpenCL acceleration (falls back to CPU)
- Performance controls: `--batch-size`, `--threads`, `--rate-limit`,
  `--progress-interval`

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

### 🌱 Seed usage database

AllInKeys keeps a rolling SQLite database of every seed range that has been
scanned so workers never repeat each other.  The file lives at
`logs/used_seeds.db` and is managed by `core.seed_tracker`:

- `record_seed_range(first, last, range_id="default")` writes every seed in the
  inclusive range.  The keygen loop calls this automatically once a
  VanitySearch file is parsed so your local progress is persisted.
- `get_condensed_ranges(range_id="default")` merges the individual seeds back
  into contiguous spans.  `core.keygen.generate_random_seed` uses this to skip
  previously scanned space before queuing fresh candidates.
- `seed_in_used_range(seed, range_id="default")` returns `True` when a seed has
  already been seen.

To merge ranges produced by friends or other rigs, feed them into
`record_seed_range` under a shared `range_id`:

```python
from core.seed_tracker import record_seed_range

# Merge a partner's batch and track it separately
record_seed_range(0x1234, 0x4321, range_id="community")
```

Any ranges stored under a given `range_id` are excluded the next time new seeds
are queued, keeping the search pointed at fresh, never‑scanned territory.

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

### Rate limiting

Each alert channel can enforce its own cooldown. Set `DEFAULT_ALERT_RATE_LIMIT`
(in seconds) in your `.env` to apply a global limit or override a specific
channel with `<CHANNEL>_ALERT_RATE_LIMIT` — for example,
`EMAIL_ALERT_RATE_LIMIT=120` limits email alerts to once every two minutes.

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

## 🛡️ Security Options

- Set `OUTPUT_ENCRYPTION=pgp` to stream VanitySearch and match logs through your PGP public key (`PGP_PUBLIC_KEY_PATH`).
- Set `OUTPUT_ENCRYPTION=aes` with `AES_PASSPHRASE` to AES‑GCM encrypt those files.
- Use `secure_delete(path)` from `utils.file_utils` to overwrite and remove sensitive data.

---

## 🧰 Tools Used

- Python 3.9+
- PGPy for OpenPGP
- VanitySearch for GPU keygen
- PyInstaller (optional, for `.exe`)
- Tkinter + psutil for dashboard

---

## 📦 Building into `.exe`

You can download the Windows installer from GitHub Releases (starting with `v0.1.1`).
The release build bundles all required assets (VanitySearch binaries, wordlists,
sounds, plugins, etc.) so it runs out of the box.

```bash
pip install pyinstaller
pyinstaller --onefile main.py
```

Produces `dist/main.exe` — a standalone binary.
For the official Windows installer, the GitHub Actions workflow packages a full
`dist/AllInKeys` directory and builds an Inno Setup installer.

---

## 💵 Donate to Support Development

| Coin | Address |
|------|---------|
| BTC  | `18RWVyEciKq8NLz5Q1uEzNGXzTs5ivo37y` |
| DOGE | `DPoHJNbYHEuvNHyCFcUnvtTVmRDMNgnAs5` |
| ETH  | `0xCb8B2937D60c47438562A2E53d08B85865B57741` |

---

## 📝 Changelog

### [Unreleased]

### v0.1.1

- Added privacy-safe central telemetry with durable seed queue
- Enforced puzzle mode range validation and hardened seed tracker
- Added rolling metrics and mode-aware GUI for real-time insights
- Added `env_path` helper and migrated many modules to `pathlib`-based paths
- Introduced `--purge` command with dry-run for cleaning old downloads
- Added opt-in telemetry module and consent logging with alert redaction
- Added Docker support and compose configuration
- Implemented dashboard authentication and premium licensing module
- Added plugin entry point system and templates
- Improved GPU detection, selection, and scheduler tests
- Enforced HTTPS downloads with checksum verification
- Added processing throughput metrics and SQLite fallback for funded address lookup
- Stream VanitySearch output to track seeds and expanded binary detection
- Enhanced mnemonic mode with full BIP-39 language support and multilingual output

### v0.1.0

- Shipped the first Windows release build and installer via GitHub Releases.
- Added public telemetry observer range distribution search with neighbor lookup and multiple visualizations.
- Normalized range distribution ingest so ranges show real IDs and persist across submissions.
- Expanded user machine dashboard controls, machine detail view, and live metrics snapshot.

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
