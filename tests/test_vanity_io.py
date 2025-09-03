import time
from pathlib import Path
from core.vanity_io import RollingAtomicWriter


def test_time_based_rotation(tmp_path):
    writer = RollingAtomicWriter(
        str(tmp_path),
        rotate_lines=1000,
        max_bytes=10**9,
        prefix="test",
        rotate_seconds=1,
    )
    writer.write_line("first")
    first_path = Path(writer.final_path)
    time.sleep(1.1)
    writer.write_line("second")
    second_path = Path(writer.final_path)
    writer.close()
    assert first_path != second_path
    assert first_path.exists()
    assert second_path.exists()
