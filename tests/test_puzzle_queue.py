import importlib
import sys
import types

# Stub external dependencies only if missing
try:  # pragma: no cover - prefer real module if available
    import dotenv  # noqa: F401
except Exception:  # pragma: no cover
    sys.modules["dotenv"] = types.SimpleNamespace(load_dotenv=lambda *a, **k: None)

try:  # pragma: no cover
    import ecdsa  # noqa: F401
except Exception:  # pragma: no cover
    class _DummySigningKey:
        @staticmethod
        def from_string(data, curve=None):
            class _DummySK:
                def get_verifying_key(self):
                    class _DummyVK:
                        def to_string(self):
                            return b"\x00" * 64
                    return _DummyVK()
            return _DummySK()

    sys.modules["ecdsa"] = types.SimpleNamespace(
        SigningKey=_DummySigningKey, SECP256k1=object()
    )

try:  # pragma: no cover
    import base58  # noqa: F401
except Exception:  # pragma: no cover
    sys.modules["base58"] = types.SimpleNamespace(b58encode=lambda b: b"1addr")

import config.settings as settings
import core.paths as paths


def test_puzzle_queue_sequential(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOG_DIR", tmp_path)
    monkeypatch.setattr(paths, "LOG_DIR", tmp_path)
    pq = importlib.reload(importlib.import_module("core.puzzle_queue"))
    pq.init_work_queue()
    start = 1 << (76 - 1)
    seed1 = pq.next_seed(76, "worker1")
    seed2 = pq.next_seed(76, "worker1")
    assert seed1 == start
    assert seed2 == start + 1


def test_puzzle_queue_specific_chunk(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOG_DIR", tmp_path)
    monkeypatch.setattr(paths, "LOG_DIR", tmp_path)
    pq = importlib.reload(importlib.import_module("core.puzzle_queue"))
    pq.init_work_queue()
    start_bound, _ = pq._get_bounds(76)
    seed1 = pq.next_seed(76, "worker1", chunk_index=5)
    assert seed1 == start_bound + 5 * pq.CHUNK_SIZE
    seed2 = pq.next_seed(76, "worker1", chunk_index=5)
    assert seed2 == seed1 + 1


def test_puzzle_queue_skips_out_of_range(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "LOG_DIR", tmp_path)
    monkeypatch.setattr(paths, "LOG_DIR", tmp_path)
    pq = importlib.reload(importlib.import_module("core.puzzle_queue"))
    pq.init_work_queue()

    calls = {}

    def inc(name, amount=1):
        calls[name] = calls.get(name, 0) + amount

    monkeypatch.setattr(pq, "increment_metric", inc, raising=False)

    start_bound, _ = pq._get_bounds(76)

    def fake_claim_next_chunk(puzzle, assignee):
        return start_bound - 10, start_bound - 5

    monkeypatch.setattr(pq, "claim_next_chunk", fake_claim_next_chunk)

    seed = pq.next_seed(76, "worker1")
    assert seed is None
    assert calls.get("out_of_range_skipped") == 1
