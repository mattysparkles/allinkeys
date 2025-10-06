import io
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

sys.modules.setdefault(
    "dotenv", types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None)
)

from core import vanity_runner  # noqa: E402


class DummyProc:
    def __init__(self, lines, output_path):
        self.stdout = io.StringIO("\n".join(lines))
        self._output_path = Path(output_path)
        # Simulate VanitySearch writing to the -o file immediately
        self._output_path.write_text("\n".join(lines) + "\n")

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
    observed = {}

    def fake_popen(args):
        observed["args"] = args
        out_idx = args.index("-o") + 1
        return DummyProc(["1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa"], args[out_idx])

    monkeypatch.setattr(vanity_runner, "_popen_stream", fake_popen)
    monkeypatch.setattr(vanity_runner, "VANITY_OUTPUT_DIR", tmp_path)

    count = vanity_runner.run_vanity_generator(seed_start=0, patterns=["1abc"])
    assert count == 1

    files = list(Path(tmp_path).glob("vanity_*.txt"))
    assert len(files) == 1
    assert "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa" in files[0].read_text()
    assert "-o" in observed["args"]
    temp_files = list(Path(tmp_path).glob("*.part"))
    assert not temp_files


def test_run_vanity_generator_fallback_to_single_pattern(tmp_path, monkeypatch):
    """Ensure multi-pattern fallback rewrites args with -o when no output."""

    monkeypatch.setattr(vanity_runner, "_resolve_exe", lambda: "vanity_mock")
    monkeypatch.setattr(vanity_runner, "VANITY_OUTPUT_DIR", tmp_path)

    calls = []
    warnings = []

    class SilentProc:
        def __init__(self, args):
            self.stdout = io.StringIO("")
            self._terminated = False
            self._output_path = Path(args[args.index("-o") + 1])
            self._checks = 0
            self._allow_auto_finish = "1**" in args

        def poll(self):
            if self._terminated:
                return 0
            self._checks += 1
            if self._allow_auto_finish and self._checks > 3:
                self._terminated = True
                return 0
            return None

        def wait(self, timeout=None):
            self._terminated = True
            return 0

        def terminate(self):
            self._terminated = True

    def fake_popen(args):
        calls.append(args)
        proc = SilentProc(args)
        return proc

    now = {"t": 0.0}

    def fake_time():
        return now["t"]

    def fake_sleep(seconds):
        now["t"] += 5

    monkeypatch.setattr(vanity_runner.time, "time", fake_time)
    monkeypatch.setattr(vanity_runner.time, "sleep", fake_sleep)
    monkeypatch.setattr(vanity_runner, "_popen_stream", fake_popen)

    def fake_warning(msg, *args, **kwargs):
        warnings.append(str(msg))

    monkeypatch.setattr(vanity_runner.logger, "warning", fake_warning)

    # Force the fallback path by using multiple patterns (initial run produces no output)
    count = vanity_runner.run_vanity_generator(seed_start=0, patterns=["1abc", "1def"])
    assert count == 0
    # Ensure we attempted multi-pattern first, then fallback to 1**
    assert len(calls) >= 1
    first_args = calls[0]
    assert "-i" in first_args
    assert "-o" in first_args
    for args in calls:
        assert "-o" in args
    assert any("1**" in msg for msg in warnings)
