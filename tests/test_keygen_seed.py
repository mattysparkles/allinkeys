import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from config.constants import SECP256K1_ORDER

for mod in ["core.keygen", "core.dashboard"]:
    sys.modules.pop(mod, None)
keygen = importlib.import_module("core.keygen")


def test_generate_seed_from_batch_basic():
    seed = keygen.generate_seed_from_batch(0, 0)
    assert seed == 1 << 128
    seed2 = keygen.generate_seed_from_batch(1, 5, batch_size=10)
    assert seed2 == (1 * 10 + 5) + (1 << 128)


def test_generate_seed_from_batch_overflow():
    batch_size = 10
    batch_id = SECP256K1_ORDER // batch_size
    index = SECP256K1_ORDER % batch_size + 1
    assert keygen.generate_seed_from_batch(batch_id, index, batch_size) is None


def test_generate_random_seed_queue(monkeypatch):
    keygen._SEED_QUEUE = [42]
    seed = keygen.generate_random_seed()
    assert seed == 42
    assert keygen._SEED_QUEUE == []


def test_generate_random_seed_fills_queue(monkeypatch):
    keygen._SEED_QUEUE = []
    monkeypatch.setattr(keygen, "seed_in_used_range", lambda c, ranges=None: False)
    monkeypatch.setattr(keygen, "get_condensed_ranges", lambda: [])
    monkeypatch.setattr(keygen.secrets, "randbelow", lambda span: 1)
    seed = keygen.generate_random_seed(min_bits=128)
    assert seed == (1 << 128) + 1
    assert len(keygen._SEED_QUEUE) == keygen.SEED_QUEUE_SIZE - 1
