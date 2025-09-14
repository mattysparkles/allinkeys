import types
import importlib
import sys

# Stub heavy modules before importing keygen
sys.modules.setdefault(
    "core.gpu_selector", types.SimpleNamespace(get_vanitysearch_gpu_ids=lambda: [])
)

sys.modules.setdefault(
    "core.logger",
    types.SimpleNamespace(
        get_logger=lambda *a, **k: types.SimpleNamespace(
            info=lambda *a, **k: None,
            error=lambda *a, **k: None,
            warning=lambda *a, **k: None,
            debug=lambda *a, **k: None,
            exception=lambda *a, **k: None,
        )
    ),
)

sys.modules.setdefault(
    "core.dashboard",
    types.SimpleNamespace(
        init_shared_metrics=lambda *a, **k: None,
        set_metric=lambda *a, **k: None,
        increment_metric=lambda *a, **k: None,
        get_metric=lambda *a, **k: 0,
    ),
)

# Import the module under test
keygen = importlib.import_module("core.keygen")


def test_run_vanitysearch_stream_skips_out_of_range(monkeypatch):
    calls = {}

    def inc(name, amount=1):
        calls[name] = calls.get(name, 0) + amount

    monkeypatch.setattr(keygen, "increment_metric", inc, raising=False)

    settings = types.SimpleNamespace(
        PUZZLE_MODE=True, PUZZLE_START="0x10", PUZZLE_END="0x20", VANITY_PATTERN="1**"
    )
    monkeypatch.setattr(keygen, "settings", settings, raising=False)

    result = keygen.run_vanitysearch_stream(0x30, 0, 0, None, None)
    assert result is False
    assert calls.get("out_of_range_skipped") == 1
