import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

sys.modules.setdefault(
    "dotenv", types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None)
)

from core import vanity_runner  # noqa: E402


def test_run_vanity_generator_creates_output(tmp_path, monkeypatch):
    """Ensure vanity generator writes output files even when ensure_dir returns str."""
    # Patch dependencies so we don't call the real binary
    monkeypatch.setattr(vanity_runner, "_resolve_exe", lambda: "vanity_mock")
    observed = {"cmds": []}

    class DummyProc:
        def __init__(self, cmd, stdout=None, stderr=None, env=None):
            observed["cmds"].append(cmd)
            out_idx = cmd.index("-o") + 1
            out_path = Path(cmd[out_idx])
            out_path.write_text("1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa\n")
            self.returncode = 0

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(vanity_runner.subprocess, "Popen", DummyProc)
    monkeypatch.setattr(vanity_runner, "VANITY_OUTPUT_DIR", tmp_path)

    count = vanity_runner.run_vanity_generator(seed_start=0, patterns=["1abc"])
    assert count == 1

    files = list(Path(tmp_path).glob("vanity_gpu_*.txt"))
    assert len(files) == 1
    assert "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa" in files[0].read_text()
    assert any("-o" in cmd for cmd in observed["cmds"])
    temp_files = list(Path(tmp_path).glob("*.part"))
    assert not temp_files


def test_run_vanity_generator_fallback_to_single_pattern(tmp_path, monkeypatch):
    """Ensure multi-pattern fallback rewrites args with -o when no output."""

    monkeypatch.setattr(vanity_runner, "_resolve_exe", lambda: "vanity_mock")
    monkeypatch.setattr(vanity_runner, "VANITY_OUTPUT_DIR", tmp_path)

    calls = []
    warnings = []

    class SilentProc:
        def __init__(self, cmd, stdout=None, stderr=None, env=None):
            calls.append(cmd)
            self._output_path = Path(cmd[cmd.index("-o") + 1])
            if "1**" in cmd:
                self._output_path.write_text(
                    "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa\n"
                )
            else:
                self._output_path.touch()
            self.returncode = 0

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(vanity_runner.subprocess, "Popen", SilentProc)

    def fake_warning(msg, *args, **kwargs):
        warnings.append(str(msg))

    monkeypatch.setattr(vanity_runner.logger, "warning", fake_warning)

    # Force the fallback path by using multiple patterns (initial run produces no output)
    count = vanity_runner.run_vanity_generator(seed_start=0, patterns=["1abc", "1def"])
    assert count == 1
    # Ensure we attempted multi-pattern first, then fallback to 1**
    assert len(calls) >= 1
    first_args = calls[0]
    assert "-i" in first_args
    assert "-o" in first_args
    for args in calls:
        assert "-o" in args
    assert any("1**" in msg for msg in warnings)


def test_run_vanitysearch_batch_unique_outputs(tmp_path, monkeypatch):
    """VanitySearch batch invocation should generate unique filenames per run."""
    output_dir = tmp_path
    paths = set()

    class DummyProc:
        def __init__(self, cmd, stdout=None, stderr=None, env=None):
            self.returncode = 0

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(vanity_runner.subprocess, "Popen", DummyProc)

    for _ in range(2):
        output_path, _ = vanity_runner.run_vanitysearch_batch(
            binary="vanity_mock",
            base_args=["-s", "0"],
            output_dir=str(output_dir),
            output_prefix="vanity_test",
        )
        paths.add(output_path.name)
    assert len(paths) == 2
