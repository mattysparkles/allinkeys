# core/gpu_selector.py

import os
import json
import sys
import threading
try:
    import pyopencl as cl
except Exception:  # pragma: no cover - optional dependency
    cl = None

try:
    import GPUtil
except ImportError:
    GPUtil = None

from utils.thread_guard import can_spawn_thread

# Shared state
assigned_gpus = {
    "vanitysearch": [],
    "altcoin_derive": []
}

GPU_ASSIGN_FILE = "gpu_assignments.json"
SELECTION_TIMEOUT = 20  # seconds


def list_gpus():
    """Return a list of detected GPU devices.

    The routine attempts to enumerate both CUDA and OpenCL capable hardware
    using the optional ``GPUtil`` and ``pyopencl`` libraries.  Each entry in the
    returned list contains at minimum ``type`` (``nvidia``/``amd``/``other``),
    ``name`` and a monotonically increasing ``id``.  When an OpenCL index is
    known it is exposed via ``cl_index``.  A ``backend`` field records whether
    the device was discovered through the CUDA or OpenCL stack (or both).

    Missing libraries or enumeration failures emit warnings but do not raise
    exceptions so the application can gracefully fall back to CPU execution.
    """

    gpus: list[dict] = []
    seen: dict[str, dict] = {}

    # ----------------------------- CUDA detection -----------------------------
    if GPUtil is None:
        print("⚠️ GPUtil not available; CUDA devices will not be detected.")
    else:
        try:
            for gpu in GPUtil.getGPUs():
                entry = {
                    "type": "nvidia",
                    "name": gpu.name,
                    "id": len(gpus),
                    "cl_index": None,
                    "backend": ["CUDA"],
                }
                gpus.append(entry)
                seen[gpu.name] = entry
        except Exception as exc:
            print(f"⚠️ CUDA detection failed: {exc}")

    # ---------------------------- OpenCL detection ----------------------------
    if cl is None:
        print("⚠️ PyOpenCL not available; OpenCL devices will not be detected.")
    else:
        try:
            cl_index = 0
            for platform in cl.get_platforms():
                for device in platform.get_devices(device_type=cl.device_type.GPU):
                    vendor = device.vendor.lower()
                    if "nvidia" in vendor:
                        gtype = "nvidia"
                    elif "amd" in vendor or "advanced micro devices" in vendor:
                        gtype = "amd"
                    else:
                        gtype = "other"
                    name = device.name.strip()
                    existing = seen.get(name)
                    if existing:
                        existing["backend"].append("OpenCL")
                        existing["cl_index"] = cl_index
                    else:
                        entry = {
                            "type": gtype,
                            "name": name,
                            "id": len(gpus),
                            "cl_index": cl_index,
                            "backend": ["OpenCL"],
                        }
                        gpus.append(entry)
                        seen[name] = entry
                    cl_index += 1
        except Exception as exc:
            print(f"⚠️ OpenCL detection failed: {exc}")

    # Normalise backend listing
    for g in gpus:
        g["backend"] = "/".join(g.get("backend", []))

    if not gpus:
        print("⚠️ No CUDA or OpenCL GPUs detected; falling back to CPU.")

    return gpus



def auto_assign_best(gpus):
    nvidia = [g for g in gpus if g["type"] == "nvidia"]
    amd = [g for g in gpus if g["type"] == "amd"]

    if len(gpus) == 1:
        assigned_gpus["altcoin_derive"].append(gpus[0])
    elif len(gpus) == 2:
        if nvidia and amd:
            assigned_gpus["vanitysearch"].append(nvidia[0])
            assigned_gpus["altcoin_derive"].append(amd[0])
        elif len(amd) == 2:
            assigned_gpus["altcoin_derive"].append(amd[0])
            assigned_gpus["vanitysearch"].append(amd[1])
        elif len(nvidia) == 2:
            assigned_gpus["altcoin_derive"].append(nvidia[0])
            assigned_gpus["vanitysearch"].append(nvidia[1])
    else:
        for g in gpus:
            if g["type"] == "nvidia" and len(assigned_gpus["vanitysearch"]) == 0:
                assigned_gpus["vanitysearch"].append(g)
            elif g["type"] == "amd" and len(assigned_gpus["altcoin_derive"]) == 0:
                assigned_gpus["altcoin_derive"].append(g)

        for g in gpus:
            if g not in assigned_gpus["vanitysearch"] and g not in assigned_gpus["altcoin_derive"]:
                if len(assigned_gpus["vanitysearch"]) == 0:
                    assigned_gpus["vanitysearch"].append(g)
                elif len(assigned_gpus["altcoin_derive"]) == 0:
                    assigned_gpus["altcoin_derive"].append(g)


def _input_with_timeout(prompt: str, timeout: int) -> str | None:
    """Read input from ``stdin`` but give up after ``timeout`` seconds.

    ``signal.alarm`` is unavailable on some platforms (e.g. Windows) so a
    background thread is used to implement a portable timeout.  ``None`` is
    returned when no input is received before the deadline.
    """

    if not sys.stdin.isatty():
        return None

    result: list[str] = []

    def worker() -> None:
        try:
            result.append(input(prompt))
        except Exception:
            pass

    if not can_spawn_thread("gpu_input"):
        print("[ThreadGuard] Input thread denied; falling back to defaults")
        return None

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None
    return result[0] if result else None


def prompt_user_to_choose(gpus):
    print("🧠 Available GPUs:")
    for i, g in enumerate(gpus):
        print(f"  [{i}] {g['type'].upper()} - {g['name']}")

    print("\n💡 Suggested: NVIDIA for VanitySearch, AMD for Altcoin Derive.")
    print("Enter GPU indices separated by commas (e.g., 0 or 1,2).")
    print(f"You have {SELECTION_TIMEOUT} seconds to respond or default will be chosen.\n")

    vs_input = _input_with_timeout("Select GPU(s) for VanitySearch: ", SELECTION_TIMEOUT)
    ad_input = _input_with_timeout("Select GPU(s) for Altcoin Derive: ", SELECTION_TIMEOUT)

    if vs_input is None and ad_input is None:
        print("\n⏱️ No response in time. Defaulting to best configuration.")
        auto_assign_best(gpus)
    else:
        try:
            vs_indices = [int(i.strip()) for i in (vs_input or "").split(",") if i.strip().isdigit()]
            ad_indices = [int(i.strip()) for i in (ad_input or "").split(",") if i.strip().isdigit()]
            ad_indices = [i for i in ad_indices if i not in vs_indices]

            assigned_gpus["vanitysearch"] = [gpus[i] for i in vs_indices if i < len(gpus)]
            assigned_gpus["altcoin_derive"] = [gpus[i] for i in ad_indices if i < len(gpus)]
        except Exception as e:
            print(f"⚠️ Invalid input, defaulting: {e}")
            auto_assign_best(gpus)

    print("\n🎯 GPU Assignments:")
    print("  VanitySearch →", ", ".join([g["name"] for g in assigned_gpus["vanitysearch"]]) or "None")
    print("  Altcoin Derive →", ", ".join([g["name"] for g in assigned_gpus["altcoin_derive"]]) or "None")


def assign_gpu_roles(preferred_index: int | None = None):
    """Populate ``assigned_gpus`` based on detected hardware.

    When ``preferred_index`` is provided the corresponding device is selected
    for both VanitySearch and Altcoin Derive without prompting the user.  If the
    index is out of range a warning is emitted and the normal interactive flow
    is used instead.  When no GPUs are detected the function simply prints a
    warning and returns, allowing the rest of the application to continue using
    CPU implementations.
    """

    gpus = list_gpus()
    if not gpus:
        print("⚠️ No GPUs found! Proceeding without GPU acceleration.")
        return

    if preferred_index is not None:
        if 0 <= preferred_index < len(gpus):
            assigned_gpus["vanitysearch"] = [gpus[preferred_index]]
            assigned_gpus["altcoin_derive"] = [gpus[preferred_index]]
            print(
                f"🎯 GPU Assignments:\n  VanitySearch → {gpus[preferred_index]['name']}\n  Altcoin Derive → {gpus[preferred_index]['name']}"
            )
            save_gpu_assignments()
            return
        else:
            print(
                f"⚠️ Requested GPU index {preferred_index} out of range; falling back to interactive selection."
            )

    prompt_user_to_choose(gpus)
    save_gpu_assignments()


def save_gpu_assignments():
    with open(GPU_ASSIGN_FILE, "w") as f:
        json.dump(assigned_gpus, f)


def load_gpu_assignments():
    global assigned_gpus
    if os.path.exists(GPU_ASSIGN_FILE):
        with open(GPU_ASSIGN_FILE, "r") as f:
            try:
                assigned_gpus = json.load(f)
            except json.JSONDecodeError:
                pass


def get_vanitysearch_gpu_ids():
    load_gpu_assignments()
    return [g["id"] for g in assigned_gpus.get("vanitysearch", [])]


def get_vanitysearch_gpus():
    load_gpu_assignments()
    return assigned_gpus.get("vanitysearch", [])


def get_altcoin_gpu_ids():
    load_gpu_assignments()
    return [g["id"] for g in assigned_gpus.get("altcoin_derive", [])]


def get_gpu_assignments():
    """Return a simple mapping of module to assigned GPU names."""
    load_gpu_assignments()
    return {
        "vanitysearch": ", ".join(g.get("name", "N/A") for g in assigned_gpus.get("vanitysearch", [])) or "N/A",
        "altcoin_derive": ", ".join(g.get("name", "N/A") for g in assigned_gpus.get("altcoin_derive", [])) or "N/A",
    }


def clear_gpu_assignments():
    if os.path.exists(GPU_ASSIGN_FILE):
        os.remove(GPU_ASSIGN_FILE)
