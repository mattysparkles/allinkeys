"""Stable machine identity and human-friendly naming utilities."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import socket
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import config.settings as settings
from config.directories import LOG_DIR
from utils.word_lists import ADJECTIVES, NOUNS, VERBS

STATE_PATH = Path(LOG_DIR) / "machine_identity.json"



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
    digest = hashlib.sha256(machine_id.encode("utf-8")).digest()
    if len(digest) < 20:
        digest = hashlib.sha256(machine_id.encode("utf-8")).digest()
    variant = digest[0] % 2
    adj_index = int.from_bytes(digest[1:5], "big") % len(ADJECTIVES)
    noun_index = int.from_bytes(digest[5:9], "big") % len(NOUNS)
    verb_index = int.from_bytes(digest[9:13], "big") % len(VERBS)
    if variant == 0:
        return f"{ADJECTIVES[adj_index]}-{NOUNS[noun_index]}"
    return f"{NOUNS[noun_index]}-{VERBS[verb_index]}"


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


def get_machine_name_state() -> Tuple[Optional[str], Optional[str]]:
    """Return (machine_name, source) from the local identity state."""

    state = _load_state()
    name = state.get("machine_name")
    source = state.get("machine_name_source")
    return (str(name) if name else None, str(source) if source else None)


def suggest_machine_name() -> str:
    """Return a hostname-based default machine name suggestion."""

    hostname = socket.gethostname() or "albatross"
    cleaned = re.sub(r"[^A-Za-z0-9]+", "-", hostname).strip("-")
    if not cleaned:
        cleaned = "albatross"
    if not cleaned[-1].isdigit():
        cleaned = f"{cleaned}-1"
    return cleaned.upper()


def set_machine_name(name: str) -> None:
    """Persist a friendly machine name override to the local identity state."""

    cleaned = (name or "").strip()
    if not cleaned:
        return
    state = _load_state()
    state.update(
        {
            "machine_name": cleaned,
            "machine_name_source": "user",
            "machine_name_updated_at": datetime.utcnow().isoformat() + "Z",
        }
    )
    _save_state(state)
