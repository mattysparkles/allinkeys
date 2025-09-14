import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import vanity_runner  # noqa: E402


class DummyProc:
    def __init__(self, lines):
        self.stdout = io.StringIO("\n".join(lines))

    def poll(self):
        # Return 0 when all lines have been read
        return 0 if self.stdout.tell() >= len(self.stdout.getvalue()) else None

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass


def test_run_vanity_generator_creates_output(tmp_path, monkeypatch):
    """Ensure vanity generator writes output files even when ensure_dir returns str."""
    # Patch dependencies so we don't call the real binary
    monkeypatch.setattr(vanity_runner, "_resolve_exe", lambda: "vanity_mock")
    monkeypatch.setattr(
        vanity_runner,
        "_popen_stream",
        lambda args: DummyProc(["1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"]),
    )
    monkeypatch.setattr(vanity_runner, "VANITY_OUTPUT_DIR", tmp_path)

    count = vanity_runner.run_vanity_generator(seed_start=0, patterns=["1abc"])
    assert count == 1

    files = list(Path(tmp_path).glob("vanity_*.txt"))
    assert len(files) == 1
    assert "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa" in files[0].read_text()
