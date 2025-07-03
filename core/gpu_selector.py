# core/gpu_selector.py

import os
import time
import json
import pyopencl as cl

try:
    import GPUtil
except ImportError:
    GPUtil = None

# Shared state
assigned_gpus = {
    "vanitysearch": [],
    "altcoin_derive": []
}

GPU_ASSIGN_FILE = "gpu_assignments.json"
SELECTION_TIMEOUT = 20  # seconds


def list_gpus():
    gpus = []
    seen_names = set()

    # NVIDIA GPUs via GPUtil
    if GPUtil:
        try:
            for gpu in GPUtil.getGPUs():
                name = f"NVIDIA: {gpu.name}"
                if name not in seen_names:
                    gpus.append({"type": "nvidia", "name": gpu.name, "id": gpu.id, "cl_index": None})
                    seen_names.add(name)
        except:
            pass

    # AMD or others via OpenCL
    try:
        cl_index = 0
        for platform in cl.get_platforms():
            for device in platform.get_devices():
                name = f"AMD: {device.name}"
                if name not in seen_names:
                    gpus.append({"type": "amd", "name": device.name, "id": len(gpus), "cl_index": cl_index})
                    seen_names.add(name)
                cl_index += 1
    except:
        pass

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


def prompt_user_to_choose(gpus):
    print("🧠 Available GPUs:")
    for i, g in enumerate(gpus):
        print(f"  [{i}] {g['type'].upper()} - {g['name']}")

    print("\n💡 Suggested: NVIDIA for VanitySearch, AMD for Altcoin Derive.")
    print("Enter GPU indices separated by commas (e.g., 0 or 1,2).")
    print(f"You have {SELECTION_TIMEOUT} seconds to respond or default will be chosen.\n")

    try:
        import signal

        def timeout_handler(signum, frame):
            raise TimeoutError

        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(SELECTION_TIMEOUT)

        try:
            vs_input = input("Select GPU(s) for VanitySearch: ").strip()
            ad_input = input("Select GPU(s) for Altcoin Derive: ").strip()
            signal.alarm(0)

            vs_indices = [int(i.strip()) for i in vs_input.split(",") if i.strip().isdigit()]
            ad_indices = [int(i.strip()) for i in ad_input.split(",") if i.strip().isdigit()]
            ad_indices = [i for i in ad_indices if i not in vs_indices]

            assigned_gpus["vanitysearch"] = [gpus[i] for i in vs_indices if i < len(gpus)]
            assigned_gpus["altcoin_derive"] = [gpus[i] for i in ad_indices if i < len(gpus)]

        except TimeoutError:
            print("\n⏱️ No response in time. Defaulting to best configuration.")
            auto_assign_best(gpus)

    except (ImportError, AttributeError, OSError):
        try:
            vs_input = input("Select GPU(s) for VanitySearch: ").strip()
            ad_input = input("Select GPU(s) for Altcoin Derive: ").strip()

            vs_indices = [int(i.strip()) for i in vs_input.split(",") if i.strip().isdigit()]
            ad_indices = [int(i.strip()) for i in ad_input.split(",") if i.strip().isdigit()]
            ad_indices = [i for i in ad_indices if i not in vs_indices]

            assigned_gpus["vanitysearch"] = [gpus[i] for i in vs_indices if i < len(gpus)]
            assigned_gpus["altcoin_derive"] = [gpus[i] for i in ad_indices if i < len(gpus)]

        except Exception as e:
            print(f"⚠️ Invalid input, defaulting: {e}")
            auto_assign_best(gpus)

    print("\n🎯 GPU Assignments:")
    print("  VanitySearch →", ", ".join([g["name"] for g in assigned_gpus["vanitysearch"]]) or "None")
    print("  Altcoin Derive →", ", ".join([g["name"] for g in assigned_gpus["altcoin_derive"]]) or "None")


def assign_gpu_roles():
    gpus = list_gpus()
    if not gpus:
        print("⚠️ No GPUs found! Proceeding without GPU acceleration.")
        return

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
