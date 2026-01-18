# core/backlog.py

import os
import csv
import time
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Set, TextIO

from core.altcoin_derive import derive_altcoin_addresses_from_hex, convert_txt_to_csv
from core.paths import VANITY_OUTPUT_DIR as VANITY_DIR_P, CSV_DIR as CSV_DIR_P, LOG_DIR as LOG_DIR_P, ensure_dirs
from core.logger import log_message
from utils.thread_guard import can_spawn_threads

# === Portable Config for Batch Parsing Mode ===
LOG_PATH = Path(LOG_DIR_P)
CSV_BASE = Path(CSV_DIR_P)
BATCH_LOG = str((LOG_PATH / "backlog_history.log").resolve())
MAX_CSV_MB = 750
SKIP_FILE_NAME = "batch_0_part_0_seed_10000000.txt"
SKIP_FILE_MIN_SIZE_KB = 50_000  # Skip anything < 50MB

ensure_dirs()
CSV_BASE.mkdir(parents=True, exist_ok=True)
LOG_PATH.mkdir(parents=True, exist_ok=True)


def safe_str(obj: object) -> str:
    """Best-effort conversion of any object to string for logging.

    Never raises; falls back to ``repr`` or a sentinel string.
    """
    try:
        return str(obj)
    except Exception:
        try:
            return repr(obj)
        except Exception:
            return "<unprintable exception>"


def log(msg: str) -> None:
    """Print a timestamped message for the legacy backlog CLI."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] {msg}")


def is_file_locked(path: str) -> bool:
    """Return True if ``path`` appears locked (cannot be opened appendable)."""
    try:
        with open(path, 'a+'):
            return False
    except (OSError, PermissionError):
        return True


def is_file_still_writing(path: str, delay: float = 2.0) -> bool:
    """Heuristic: file is writing if its size changes within ``delay`` seconds."""
    try:
        size1 = Path(path).stat().st_size
        time.sleep(delay)
        size2 = Path(path).stat().st_size
        return size1 != size2
    except Exception:
        return True


def start_backlog_conversion_loop(shared_metrics=None, shutdown_event=None, pause_event=None) -> None:
    """
    Monitors VANITY_OUTPUT_DIR for .txt files and converts to .csv if ready.
    Skips files that are too small, locked, or recently modified.
    """
    from core.worker_bootstrap import ensure_metrics_ready, _safe_set_metric, _safe_inc_metric
    from core.dashboard import register_control_events
    try:
        ensure_metrics_ready(shared_metrics)
        register_control_events(shutdown_event, pause_event, module="backlog")
    except Exception:
        pass
    from core.dashboard import set_thread_health
    _safe_set_metric("status.backlog", "Running")
    _safe_set_metric("backlog_files_queued", 0)
    _safe_set_metric("backlog_files_completed", 0)
    set_thread_health("backlog", True)
    log_message("📦 Backlog converter started...", "INFO")

    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        executor = None
        if can_spawn_threads(4, "backlog_executor"):
            executor = ThreadPoolExecutor(max_workers=4)
        else:
            log_message("⚠️ Backlog thread pool skipped; thread limit reached", "WARN")
        from core.dashboard import get_shutdown_event, get_pause_event
        while True:
            if get_shutdown_event() and get_shutdown_event().is_set():
                break
            try:
                vdir = Path(VANITY_DIR_P)
                files = [p.name for p in vdir.iterdir() if p.is_file() and p.suffix == ".txt" and not p.name.endswith(".part")]
                update_dashboard_stat("backlog_files_queued", len(files))
                futures = []
                for file in files:
                    txt_path = str((Path(VANITY_DIR_P) / file).resolve())
                    output_path = str((Path(CSV_DIR_P) / file.replace(".txt", ".csv")).resolve())

                    too_small = Path(txt_path).stat().st_size < SKIP_FILE_MIN_SIZE_KB * 1024
                    locked = is_file_locked(txt_path)
                    writing = is_file_still_writing(txt_path)

                    if file == SKIP_FILE_NAME:
                        log_message(f"⏭️ Skipping {file} (explicit skip file)", "DEBUG")
                        continue

                    if too_small:
                        log_message(f"⏭️ Skipping {file} (too small: {Path(txt_path).stat().st_size} bytes)", "DEBUG")
                    if locked:
                        log_message(f"⏭️ Skipping {file} (file is locked)", "DEBUG")
                    if writing:
                        log_message(f"⏭️ Skipping {file} (file may still be writing)", "DEBUG")

                    if too_small or locked or writing:
                        continue

                    try:
                        batch_id = int(file.split("_")[1]) if "part_" in file and "_seed_" in file else None
                    except Exception as e:
                        log_message(f"⚠️ Could not extract batch_id from {file}: {safe_str(e)}", "WARNING")
                        batch_id = None

                    if not os.path.exists(output_path):
                        log_message(f"🔁 Converting {file} to CSV...", "INFO")
                        if executor:
                            futures.append(executor.submit(convert_txt_to_csv, txt_path, batch_id, pause_event, get_shutdown_event()))
                        else:
                            try:
                                convert_txt_to_csv(txt_path, batch_id, pause_event, get_shutdown_event())
                                _safe_inc_metric("backlog_files_completed", 1)
                            except Exception as e:
                                log_message(f"❌ Backlog task error: {safe_str(e)}", "ERROR")
                    else:
                        log_message(f"✅ Already converted: {file}", "DEBUG")

                if executor:
                    for fut in as_completed(futures):
                        try:
                            fut.result()
                        except Exception as e:
                            log_message(f"❌ Backlog task error: {safe_str(e)}", "ERROR")
                        else:
                            _safe_inc_metric("backlog_files_completed", 1)

            except Exception as e:
                log_message(f"❌ Error in backlog conversion loop: {safe_str(e)}", "ERROR")

            if get_pause_event() and get_pause_event().is_set():
                time.sleep(1)
                continue
            time.sleep(10)
    finally:
        _safe_set_metric("status.backlog", "Stopped")
        try:
            set_thread_health("backlog", False)
        except Exception:
            pass


# === Legacy Parsing Mode (Rarely Used) ===

def get_parsed_log() -> Set[str]:
    """Return set of previously processed vanity batch filenames."""
    if not Path(BATCH_LOG).exists():
        return set()
    with open(BATCH_LOG, "r", encoding="utf-8") as f:
        return set(line.strip() for line in f if line.strip())


def append_to_log(filename: str) -> None:
    """Append a processed filename to the legacy backlog history log."""
    with open(BATCH_LOG, "a", encoding="utf-8") as f:
        f.write(filename + "\n")


def get_file_size_mb(path: str) -> float:
    """Return size of ``path`` in megabytes."""
    return Path(path).stat().st_size / (1024 * 1024)


def open_new_csv_writer(index: int) -> Tuple[TextIO, csv.DictWriter, str]:
    """Create a new CSV writer directly in CSV_BASE_DIR.

    Returns a tuple of (file_handle, csv_writer, file_path).
    """
    CSV_BASE.mkdir(parents=True, exist_ok=True)
    path = str((CSV_BASE / f"keys_batch_{index:05d}.csv").resolve())
    f = open(path, "w", newline='', encoding="utf-8")
    writer = csv.DictWriter(f, fieldnames=[
        "original_seed", "hex_key", "btc_C", "btc_U", "ltc_C", "ltc_U",
        "doge_C", "doge_U", "bch_C", "bch_U", "eth", "dash_C", "dash_U",
        "private_key", "compressed_address", "uncompressed_address", "batch_id", "index"
    ])
    writer.writeheader()
    return f, writer, path


def parse_vanity_file(txt_file: str, batch_id: Optional[int]) -> int:
    with open(txt_file, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    log(f"📄 {len(lines):,} lines read from {Path(txt_file).name}")

    parsed_count = 0
    csv_index = sum(1 for _ in CSV_BASE.rglob("*.csv"))
    address_tally = {k: 0 for k in [
        "btc_C", "btc_U", "ltc_C", "ltc_U", "doge_C", "doge_U",
        "bch_C", "bch_U", "eth", "dash_C", "dash_U"
    ]}

    f, writer, path = open_new_csv_writer(csv_index)
    i = 0
    while i < len(lines):
        if not lines[i].startswith("PubAddress:") or i + 2 >= len(lines):
            i += 1
            continue

        try:
            addr_line = lines[i]
            wif_line = lines[i + 1]
            hex_line = lines[i + 2]

            compressed_address = addr_line.replace("PubAddress:", "").strip()
            wif = wif_line.replace("Priv (WIF):", "").replace("p2pkh:", "").strip()
            hex_seed = hex_line.replace("Priv (HEX):", "").replace("0x", "").strip().zfill(64)
            seed = int(hex_seed, 16)

            altcoins = derive_altcoin_addresses_from_hex(hex_seed)

            for k in address_tally:
                if altcoins.get(k):
                    address_tally[k] += 1

            row = {
                "original_seed": seed,
                "hex_key": hex_seed,
                "btc_C": altcoins.get("btc_C", ""),
                "btc_U": altcoins.get("btc_U", ""),
                "ltc_C": altcoins.get("ltc_C", ""),
                "ltc_U": altcoins.get("ltc_U", ""),
                "doge_C": altcoins.get("doge_C", ""),
                "doge_U": altcoins.get("doge_U", ""),
                "bch_C": altcoins.get("bch_C", ""),
                "bch_U": altcoins.get("bch_U", ""),
                "eth": altcoins.get("eth", ""),
                "dash_C": altcoins.get("dash_C", ""),
                "dash_U": altcoins.get("dash_U", ""),
                "private_key": wif,
                "compressed_address": compressed_address,
                "uncompressed_address": "",
                "batch_id": batch_id,
                "index": parsed_count
            }

            writer.writerow(row)
            parsed_count += 1

            if parsed_count % 2000 == 0:
                f.flush()
                size_mb = get_file_size_mb(path)
                log(f"🧾 Written {parsed_count} rows, file size: {size_mb:.2f}MB")
                if size_mb >= MAX_CSV_MB:
                    f.close()
                    csv_index += 1
                    f, writer, path = open_new_csv_writer(csv_index)

            i += 3
        except Exception as e:
            log(f"⚠️ Error at line {i}: {safe_str(e)}")
            i += 1

    f.close()
    log(f"✅ Done. {parsed_count:,} rows written.")
    for coin, count in address_tally.items():
        log(f"🔢 {coin.upper()} addresses: {count:,}")
    return parsed_count


def main() -> None:
    parsed_files = get_parsed_log()
    files = sorted(p.name for p in LOG_PATH.iterdir() if p.is_file() and p.name.endswith(".txt") and p.name.startswith("vanitysearch_batch_"))

    for txt in files:
        if txt in parsed_files:
            continue

        path = str((LOG_PATH / txt).resolve())
        log(f"\n🚀 Processing {txt}...")

        try:
            batch_id = int(txt.split("_")[2])
            written = parse_vanity_file(path, batch_id)
            if written:
                append_to_log(txt)
                Path(path).unlink(missing_ok=True)
                log(f"🧹 Removed {txt}")
        except Exception as e:
            log(f"❌ Failed to process {txt}: {safe_str(e)}")


if __name__ == "__main__":
    main()
