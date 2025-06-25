# Codex Evaluation: AllInKeys Modular Stack

## 🧠 Project Summary

AllInKeys is a modular GPU-accelerated Bitcoin and altcoin key generator that:
- Uses VanitySearch to explore the BTC keyspace
- Derives altcoin addresses (BTC, LTC, DOGE, DASH, BCH, ETH, etc.)
- Rotates output files every N seconds
- Converts each `.txt` output into a `.csv` with both compressed and uncompressed addresses
- Scans these CSVs against funded address lists
- Triggers alerts for matches via email, desktop popup, etc.

---

## ⚠️ Observed Issues

### 1. **VanitySearch Always Starts With the Same Seed**
- The script is designed to generate seeds ≥ `2^128` using `generate_seed_from_batch()` and then switch to **random high-entropy seeds** after file rotation.
- However, we’ve observed:
  - The **first seed is always the same** after restart
  - Subsequent seeds appear **low entropy** — sometimes too small, potentially violating the design requirement that all seeds be ≥ `2^128`

> Codex should verify the logic in `generate_random_seed()` and confirm that seeds are:
> - Cryptographically random
> - Within the expected `2^128 ≤ seed < secp256k1_order` range

---

### 2. **.txt File Rotation Works (Time-Based)**
- Rotation now happens after a fixed interval (via `ROTATE_INTERVAL_SECONDS`) instead of by file size.
- Codex should verify:
  - Rotation happens cleanly and terminates the VanitySearch subprocess
  - Files are complete and not mid-write when picked up by the converter
  - Each file uses a **new, correct seed**, not one repeated or reused

---

### 3. **CSV Conversion Fails**
- Each `.txt` file from VanitySearch should be parsed by `altcoin_derive.py` and converted into a `.csv`
- The `.csv` must contain all applicable altcoin addresses:
  - Compressed (C) and Uncompressed (U) variants for BTC, LTC, DOGE, DASH, BCH, RVN, PEP
  - ETH address derived from compressed pubkey

**Failure Symptoms:**
- `.csv` files are empty or missing
- No warnings or errors are thrown
- Used to work in older non-modular version

> Codex should check:
> - Whether `convert_txt_to_csv()` is correctly detecting all 3-line blocks (`PubAddress`, `Priv (WIF)`, `Priv (HEX)`)
> - That `derive_altcoin_addresses_from_hex()` isn't failing silently
> - Whether GPU kernel (`sha256_kernel.cl`) or device detection fails and breaks address generation
> - If line parsing fails due to encoding, malformed output, or missing flush

---

## 🧩 Evaluation Objectives for Codex

1. **Review seed generation** logic in `keygen.py` and confirm that:
   - Initial deterministic seeds use batch_id/index
   - Rotated seeds are random and ≥ `2^128`

2. **Validate VanitySearch subprocess logic** in `run_vanitysearch_stream()`:
   - Confirm seeds are passed in correctly
   - Ensure each file reflects a new seed
   - Confirm time-based rotation exits cleanly

3. **Inspect `.txt` → `.csv` pipeline**:
   - Confirm that `backlog.py` picks up complete `.txt` files only
   - Ensure `altcoin_derive.py` creates full rows with all altcoin fields
   - Trace any silent failures in parsing or derivation

4. **Recommend fixes or diagnostics** for:
   - Broken line parsing
   - GPU fallback logic
   - Logging missing errors
   - Race conditions in file writing/conversion

---

## ✅ Codex Prompt to Use

> "This is a modular Bitcoin key generator that runs VanitySearch with seed rotation and derives altcoin addresses. It's designed to start with a high-entropy seed (≥ 2^128) and rotate output files every N seconds. Each `.txt` output should be converted into a `.csv` with all derived altcoin addresses (compressed/uncompressed). Diagnose why the seeds appear low-entropy after the first run, and why the CSV converter isn't producing usable output files. Focus on the parsing logic and GPU derivation in `altcoin_derive.py` and the subprocess loop in `keygen.py`."

---

## 📁 Priority Files for Review

- `keygen.py` – seed generation, subprocess, rotation
- `vanitysearch.py` – standalone seed testing
- `altcoin_derive.py` – CSV creation and altcoin derivation
- `backlog.py` – conversion loop and file readiness checks

---

## 🧩 Optional Follow-Up Suggestions

- Add better logs for seed entropy and file parse counts
- Add fallback for GPU errors (non-GPU mode)
- Use hash-based seed validation to verify randomness
