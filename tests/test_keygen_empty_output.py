import importlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))


def test_empty_vanity_output_advances(monkeypatch, tmp_path):
    """Empty VanitySearch outputs should still advance rotation."""


    monkeypatch.delitem(sys.modules, "core.keygen", raising=False)
    keygen = importlib.import_module("core.keygen")

    # Ensure the keygen module uses the temporary output directory.

    fake_exe = tmp_path / "vanitysearch"
    fake_exe.write_text("")
    fake_exe.chmod(0o755)

    monkeypatch.setattr(keygen, "VANITYSEARCH_PATH", fake_exe)
    monkeypatch.setattr(keygen, "VANITY_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(keygen, "ROTATE_INTERVAL_SECONDS", 0)
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

    monkeypatch.setattr(keygen.subprocess, "Popen", DummyProc)

    result = keygen.run_vanitysearch_stream(0x1, 0, 0, None, None)
    assert result is True

    output_path = Path(tmp_path) / "batch_0_part_0_seed_00000001.txt"
    assert not output_path.exists()


def test_missing_vanity_output_advances(monkeypatch, tmp_path):
    """Missing VanitySearch outputs should retry the same part instead of advancing."""

    monkeypatch.delitem(sys.modules, "core.keygen", raising=False)
    keygen = importlib.import_module("core.keygen")

    fake_exe = tmp_path / "vanitysearch"
    fake_exe.write_text("")
    fake_exe.chmod(0o755)

    monkeypatch.setattr(keygen, "VANITYSEARCH_PATH", fake_exe)
    monkeypatch.setattr(keygen, "VANITY_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(keygen, "ROTATE_INTERVAL_SECONDS", 5)
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

    monkeypatch.setattr(keygen.subprocess, "Popen", DummyProcNoFile)

    result = keygen.run_vanitysearch_stream(0x2, 1, 0, None, None)
    assert result is False


def test_rotation_fallback_without_thread(monkeypatch, tmp_path):
    """Rotation must still occur when the monitor thread cannot start."""

    monkeypatch.delitem(sys.modules, "core.keygen", raising=False)
    keygen = importlib.import_module("core.keygen")

    fake_exe = tmp_path / "vanitysearch"
    fake_exe.write_text("")
    fake_exe.chmod(0o755)

    monkeypatch.setattr(keygen, "VANITYSEARCH_PATH", fake_exe)
    monkeypatch.setattr(keygen, "VANITY_OUTPUT_DIR", str(tmp_path))
    monkeypatch.setattr(keygen, "ROTATE_INTERVAL_SECONDS", 1)
    monkeypatch.setattr(keygen, "get_vanitysearch_gpu_ids", lambda: [])
    monkeypatch.setattr(keygen, "can_spawn_thread", lambda *a, **k: False)

    class StubbornProc:
        def __init__(self, cmd, stdout=None, stderr=None, env=None):
            self.cmd = cmd
            self.pid = 11111
            self._terminated = False
            self.returncode = None
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.touch()

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

    monkeypatch.setattr(keygen.subprocess, "Popen", StubbornProc)

    result = keygen.run_vanitysearch_stream(0x3, 2, 0, None, None)
    assert result is True

    output_path = Path(tmp_path) / "batch_2_part_0_seed_00000003.txt"
    # Empty files are deleted but rotation should still advance
    assert not output_path.exists()
