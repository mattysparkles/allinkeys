import importlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))
# Provide a dummy requests module so telemetry imports do not require the real dependency
sys.modules.setdefault(
    "requests",
    types.SimpleNamespace(
        Session=lambda *a, **k: None,
        post=lambda *a, **k: None,
    ),
)


def test_empty_vanity_output_advances(monkeypatch, tmp_path):
    """Empty VanitySearch outputs should still advance rotation."""


    monkeypatch.delitem(sys.modules, "core.keygen", raising=False)
    keygen = importlib.import_module("core.keygen")
    vanity_runner = importlib.import_module("core.vanity_runner")

    # Ensure the keygen module uses the temporary output directory.

    fake_exe = tmp_path / "vanitysearch"
    fake_exe.write_text("")
    fake_exe.chmod(0o755)

    monkeypatch.setattr(keygen, "VANITYSEARCH_PATH", fake_exe)
    monkeypatch.setattr(keygen, "VANITY_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(keygen, "get_vanitysearch_gpu_ids", lambda: [])

    class DummyProc:
        def __init__(self, cmd, stdout=None, stderr=None, env=None):
            self.cmd = cmd
            self.pid = 12345
            self._terminated = False

            self.returncode = None

            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.touch()

        def terminate(self):
            self._terminated = True

            if self.returncode is None:
                self.returncode = -15

        def poll(self):
            if self._terminated and self.returncode is None:
                return 0
            return self.returncode if self._terminated else None

        def wait(self, timeout=None):
            self._terminated = True
            self.returncode = 0

            return 0

    monkeypatch.setattr(vanity_runner.subprocess, "Popen", DummyProc)

    result = keygen.run_vanitysearch_stream(0x1, 0, 0, None, None)
    assert result is True

    output_files = list(Path(tmp_path).glob("batch_0_part_0_seed_00000001_*.txt"))
    assert not output_files


def test_missing_vanity_output_advances(monkeypatch, tmp_path):
    """Missing VanitySearch outputs should retry the same part instead of advancing."""

    monkeypatch.delitem(sys.modules, "core.keygen", raising=False)
    keygen = importlib.import_module("core.keygen")
    vanity_runner = importlib.import_module("core.vanity_runner")

    fake_exe = tmp_path / "vanitysearch"
    fake_exe.write_text("")
    fake_exe.chmod(0o755)

    monkeypatch.setattr(keygen, "VANITYSEARCH_PATH", fake_exe)
    monkeypatch.setattr(keygen, "VANITY_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(keygen, "get_vanitysearch_gpu_ids", lambda: [])

    class DummyProcNoFile:
        def __init__(self, cmd, stdout=None, stderr=None, env=None):
            self.cmd = cmd
            self.pid = 67890
            self.returncode = None
            self._done = False

        def terminate(self):
            self._done = True
            if self.returncode is None:
                self.returncode = -15

        def poll(self):
            return self.returncode if self._done else None

        def wait(self, timeout=None):
            self._done = True
            self.returncode = 0
            return 0

    monkeypatch.setattr(vanity_runner.subprocess, "Popen", DummyProcNoFile)

    result = keygen.run_vanitysearch_stream(0x2, 1, 0, None, None)
    assert result is False


def test_unique_output_filename_per_run(monkeypatch, tmp_path):
    """Each run should create a unique output filename."""

    monkeypatch.delitem(sys.modules, "core.keygen", raising=False)
    keygen = importlib.import_module("core.keygen")
    vanity_runner = importlib.import_module("core.vanity_runner")

    fake_exe = tmp_path / "vanitysearch"
    fake_exe.write_text("")
    fake_exe.chmod(0o755)

    monkeypatch.setattr(keygen, "VANITYSEARCH_PATH", fake_exe)
    monkeypatch.setattr(keygen, "VANITY_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(keygen, "get_vanitysearch_gpu_ids", lambda: [])

    class StubbornProc:
        def __init__(self, cmd, stdout=None, stderr=None, env=None):
            self.cmd = cmd
            self.pid = 11111
            self._terminated = False
            self.returncode = None
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.write_text("data")

        def terminate(self):
            self._terminated = True
            if self.returncode is None:
                self.returncode = -15

        def kill(self):
            self._terminated = True
            self.returncode = -9

        def poll(self):
            return self.returncode if self._terminated else None

        def wait(self, timeout=None):
            self._terminated = True
            self.returncode = 0
            return 0

    monkeypatch.setattr(vanity_runner.subprocess, "Popen", StubbornProc)

    result = keygen.run_vanitysearch_stream(0x3, 2, 0, None, None)
    assert result is True

    first_path = Path(keygen.last_output_file)
    result = keygen.run_vanitysearch_stream(0x3, 2, 0, None, None)
    assert result is True
    second_path = Path(keygen.last_output_file)
    assert first_path != second_path


def test_output_file_preserved_with_data(monkeypatch, tmp_path):
    """Non-empty VanitySearch outputs should remain on disk after the run."""

    monkeypatch.delitem(sys.modules, "core.keygen", raising=False)
    keygen = importlib.import_module("core.keygen")
    vanity_runner = importlib.import_module("core.vanity_runner")

    fake_exe = tmp_path / "vanitysearch"
    fake_exe.write_text("")
    fake_exe.chmod(0o755)

    monkeypatch.setattr(keygen, "VANITYSEARCH_PATH", fake_exe)
    monkeypatch.setattr(keygen, "VANITY_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(keygen, "get_vanitysearch_gpu_ids", lambda: [])

    class LockedOnceProc:
        def __init__(self, cmd, stdout=None, stderr=None, env=None):
            self.cmd = cmd
            self.pid = 22222
            self._terminated = False
            self.returncode = None
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.write_text("data")

        def terminate(self):
            self._terminated = True
            if self.returncode is None:
                self.returncode = -15

        def poll(self):
            return self.returncode if self._terminated else None

        def wait(self, timeout=None):
            self._terminated = True
            self.returncode = 0
            return 0

    monkeypatch.setattr(vanity_runner.subprocess, "Popen", LockedOnceProc)

    result = keygen.run_vanitysearch_stream(0x4, 3, 0, None, None)
    assert result is True

    # File should be present since VanitySearch writes directly via -o.
    output_path = Path(keygen.last_output_file)
    assert output_path.exists()
