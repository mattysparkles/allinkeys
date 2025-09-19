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

    # Ensure the keygen module uses the temporary output directory.
    keygen = importlib.import_module("core.keygen")

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
            out_path = Path(cmd[cmd.index("-o") + 1])
            out_path.touch()

        def terminate(self):
            self._terminated = True

        def poll(self):
            return 0 if self._terminated else None

        def wait(self):
            self._terminated = True
            return 0

    monkeypatch.setattr(keygen.subprocess, "Popen", DummyProc)

    result = keygen.run_vanitysearch_stream(0x1, 0, 0, None, None)
    assert result is True

    output_path = Path(tmp_path) / "batch_0_part_0_seed_00000001.txt"
    assert not output_path.exists()
