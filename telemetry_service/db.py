from __future__ import annotations

import os
import sqlite3


DB_PATH = os.getenv(
    "CENTRAL_TELEMETRY_DB",
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), "../logs/central_telemetry.db")
    ),
)


def get_db_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS seed_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            seed_fingerprint TEXT NOT NULL,
            app_instance_id TEXT,
            client_version TEXT,
            mode TEXT,
            range_id TEXT,
            first_seen TEXT,
            last_seen TEXT,
            used INTEGER DEFAULT 0,
            match_found INTEGER DEFAULT 0
        );
        """
    )
    existing_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(seed_events)").fetchall()
    }
    optional_columns = {
        "machine_id": "TEXT",
        "machine_name": "TEXT",
        "range_recent": "TEXT",
        "range_distribution": "TEXT",
        "reference_overlays": "TEXT",
    }
    for name, col_type in optional_columns.items():
        if name not in existing_cols:
            conn.execute(f"ALTER TABLE seed_events ADD COLUMN {name} {col_type}")
    if "user_id" not in existing_cols:
        conn.execute("ALTER TABLE seed_events ADD COLUMN user_id INTEGER")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_seed_fingerprint ON seed_events(seed_fingerprint)"
    )
    conn.execute("DROP INDEX IF EXISTS idx_seed_range")
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_seed_range_user
        ON seed_events(seed_fingerprint, range_id, user_id)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS machines (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            machine_name TEXT,
            gpu_info TEXT,
            version TEXT,
            status TEXT DEFAULT 'online',
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """
    )
    machine_info = conn.execute("PRAGMA table_info(machines)").fetchall()
    machine_columns = {row[1]: row[2].upper() for row in machine_info}
    if machine_columns and machine_columns.get("id") != "TEXT":
        conn.execute("ALTER TABLE machines RENAME TO machines_old")
        conn.execute(
            """
            CREATE TABLE machines (
                id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                machine_name TEXT,
                gpu_info TEXT,
                version TEXT,
                status TEXT DEFAULT 'online',
                last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(user_id) REFERENCES users(id)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO machines (
                id, user_id, machine_name, gpu_info, version, status, last_seen
            )
            SELECT
                CAST(id AS TEXT),
                user_id,
                machine_name,
                gpu_info,
                NULL,
                status,
                last_seen
            FROM machines_old
            """
        )
        conn.execute("DROP TABLE machines_old")
        machine_columns = {
            row[1]: row[2].upper()
            for row in conn.execute("PRAGMA table_info(machines)").fetchall()
        }
    optional_machine_columns = {
        "gpu_info": "TEXT",
        "version": "TEXT",
        "status": "TEXT",
        "last_seen": "TIMESTAMP",
        "keys_per_sec": "REAL",
        "total_keys": "REAL",
        "uptime_seconds": "REAL",
        "mode": "TEXT",
        "process_state": "TEXT",
        "cpu_percent": "REAL",
        "ram_percent": "REAL",
        "disk_free_percent": "REAL",
        "gpu_load_percent": "REAL",
        "last_error": "TEXT",
        "last_activity": "TEXT",
    }
    for name, col_type in optional_machine_columns.items():
        if name not in machine_columns:
            conn.execute(f"ALTER TABLE machines ADD COLUMN {name} {col_type}")
    conn.execute("DROP INDEX IF EXISTS idx_machine_user_name")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_machine_user ON machines(user_id)"
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pending_control (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id TEXT NOT NULL,
            command TEXT NOT NULL,
            value TEXT,
            issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'pending',
            FOREIGN KEY(machine_id) REFERENCES machines(id)
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_pending_control_machine
        ON pending_control(machine_id)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pairing_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pair_code TEXT UNIQUE NOT NULL,
            status TEXT DEFAULT 'pending',
            user_id INTEGER,
            token TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            claimed_at TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_pairing_code
        ON pairing_requests(pair_code)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS machine_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            machine_id TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            payload TEXT,
            keys_per_sec REAL,
            total_keys REAL,
            uptime_seconds REAL,
            mode TEXT,
            process_state TEXT,
            cpu_percent REAL,
            ram_percent REAL,
            disk_free_percent REAL,
            gpu_load_percent REAL,
            last_error TEXT,
            last_activity TEXT,
            FOREIGN KEY(machine_id) REFERENCES machines(id),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_machine_snapshots_machine_time
        ON machine_snapshots(machine_id, timestamp)
        """
    )
    return conn
