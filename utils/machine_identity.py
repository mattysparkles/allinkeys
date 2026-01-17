"""Stable machine identity and human-friendly naming utilities."""

from __future__ import annotations

import hashlib
import json
import platform
import socket
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import config.settings as settings
from config.directories import LOG_DIR

STATE_PATH = Path(LOG_DIR) / "machine_identity.json"

_ADJECTIVES = [
    "Amber",
    "Brisk",
    "Cosmic",
    "Dapper",
    "Electric",
    "Frosted",
    "Golden",
    "Harbor",
    "Indigo",
    "Jolly",
    "Keen",
    "Lunar",
    "Mellow",
    "Nimble",
    "Orbit",
    "Prismatic",
    "Quirky",
    "Radiant",
    "Solar",
    "Tidal",
    "Umbral",
    "Vivid",
    "Wry",
    "Zesty",
]

_NOUNS = [
    "Aurora",
    "Beacon",
    "Comet",
    "Drift",
    "Ember",
    "Falcon",
    "Gizmo",
    "Harbor",
    "Isotope",
    "Jade",
    "Kestrel",
    "Lattice",
    "Nimbus",
    "Orbit",
    "Pulse",
    "Quartz",
    "River",
    "Signal",
    "Tango",
    "Union",
    "Vector",
    "Whisper",
    "Zenith",
]


def _ensure_state_dir() -> None:
    Path(LOG_DIR).mkdir(parents=True, exist_ok=True)


def _load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: Dict[str, Any]) -> None:
    _ensure_state_dir()
    STATE_PATH.write_text(
        json.dumps(state, indent=2, sort_keys=True), encoding="utf-8"
    )


def _hash_machine_id(raw: str, source: str) -> str:
    digest = hashlib.sha256(f"{source}:{raw}".encode("utf-8")).hexdigest()
    return f"mid_{digest}"


def _read_machine_id_file(path: str) -> Optional[str]:
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except Exception:
        return None
    return value or None


def _get_os_machine_id() -> Tuple[Optional[str], Optional[str]]:
    system = platform.system()

    if system == "Windows":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Cryptography",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "MachineGuid")
                if value:
                    return str(value), "windows-machineguid"
        except Exception:
            return None, None

    if system == "Darwin":
        try:
            output = subprocess.check_output(
                ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                text=True,
            )
            for line in output.splitlines():
                if "IOPlatformUUID" in line:
                    parts = line.split("=")
                    if len(parts) > 1:
                        value = parts[1].strip().strip('"')
                        if value:
                            return value, "darwin-ioplatformuuid"
        except Exception:
            return None, None

    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        value = _read_machine_id_file(path)
        if value:
            return value, "linux-machine-id"

    return None, None


def get_machine_id() -> str:
    """Return a stable, opaque machine identifier."""

    state = _load_state()
    os_id, source = _get_os_machine_id()

    if os_id and source:
        machine_id = _hash_machine_id(os_id, source)
        if state.get("machine_id") != machine_id:
            state.update(
                {
                    "machine_id": machine_id,
                    "machine_id_source": source,
                    "machine_id_updated_at": datetime.utcnow().isoformat() + "Z",
                }
            )
            _save_state(state)
        return machine_id

    if state.get("machine_id"):
        return str(state["machine_id"])

    hostname = socket.gethostname() or "unknown"
    mac = f"{uuid.getnode():012x}"
    machine_id = _hash_machine_id(f"{mac}:{hostname}", "fallback-mac-hostname")
    state.update(
        {
            "machine_id": machine_id,
            "machine_id_source": "fallback-mac-hostname",
            "machine_id_updated_at": datetime.utcnow().isoformat() + "Z",
        }
    )
    _save_state(state)
    return machine_id


def _generate_machine_name(machine_id: str) -> str:
    digest = hashlib.sha256(machine_id.encode("utf-8")).hexdigest()
    adj_index = int(digest[:8], 16) % len(_ADJECTIVES)
    noun_index = int(digest[8:16], 16) % len(_NOUNS)
    return f"{_ADJECTIVES[adj_index]}-{_NOUNS[noun_index]}"


def get_machine_name(machine_id: Optional[str] = None) -> str:
    """Return the current human-friendly machine display name."""

    explicit = getattr(settings, "MACHINE_NAME", None)
    if explicit:
        return str(explicit)

    state = _load_state()
    if state.get("machine_name"):
        return str(state["machine_name"])

    machine_id = machine_id or get_machine_id()
    generated = _generate_machine_name(machine_id)
    state.update(
        {
            "machine_name": generated,
            "machine_name_source": "generated",
            "machine_name_updated_at": datetime.utcnow().isoformat() + "Z",
        }
    )
    _save_state(state)
    return generated
