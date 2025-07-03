import subprocess
import os
import time
from datetime import datetime

KEYS_PER_FILE = 200000

def run_vanitysearch(start, end, pattern, output_dir, batch_id):
    os.makedirs(output_dir, exist_ok=True)
    file_index = 0
    address_count = 0
    current_file = None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"vanity_output_batch{batch_id}_{timestamp}"

    hex_seed = hex(start)[2:]
    current_output_path = os.path.join(output_dir, f"{base_name}_part{file_index:03d}.txt")
    cmd = [
        "vanitysearch",
        "-s", hex_seed,
        "-gpu",
        "-o", current_output_path,
        "-u", pattern
    ]

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )

    print(f"[{timestamp}] 🧬 Launched VanitySearch with seed range {start}-{end}")

    while True:
        line = process.stdout.readline()
        if not line:
            break

        if address_count % KEYS_PER_FILE == 0:
            if current_file:
                current_file.close()
            file_path = os.path.join(output_dir, f"{base_name}_part{file_index:03d}.txt")
            current_file = open(file_path, 'w', encoding='utf-8')
            file_index += 1

        current_file.write(line)
        address_count += 1

    if current_file:
        current_file.close()

    process.stdout.close()
    process.wait()
