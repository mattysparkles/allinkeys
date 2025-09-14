import importlib
import types
import sys

# Stub heavy modules used by core.keygen
sys_modules_backup = {}


def setup_module(module):
    sys_modules_backup["core.gpu_selector"] = sys.modules.get("core.gpu_selector")
    sys.modules["core.gpu_selector"] = types.SimpleNamespace(
        get_vanitysearch_gpu_ids=lambda: []
    )
    sys_modules_backup["core.logger"] = sys.modules.get("core.logger")
    sys.modules["core.logger"] = types.SimpleNamespace(
        get_logger=lambda *a, **k: types.SimpleNamespace(
            info=lambda *a, **k: None,
            error=lambda *a, **k: None,
            warning=lambda *a, **k: None,
            debug=lambda *a, **k: None,
            exception=lambda *a, **k: None,
        )
    )
    sys_modules_backup["core.dashboard"] = sys.modules.get("core.dashboard")
    sys.modules["core.dashboard"] = types.SimpleNamespace(
        update_dashboard_stat=lambda *a, **k: None,
        get_metric=lambda *a, **k: 0,
        init_shared_metrics=lambda *a, **k: None,
        set_metric=lambda *a, **k: None,
        increment_metric=lambda *a, **k: None,
    )


def teardown_module(module):
    for name, mod in sys_modules_backup.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


def test_parse_vanity_file_formats(tmp_path):
    keygen = importlib.reload(importlib.import_module("core.keygen"))
    sample = "\n".join(
        [
            "Priv (HEX): 0x10",
            "Privkey: 20",
            "Addr: 1abc",
            "foo Privkey: 0x30",
            "Priv (HEX): 40",
            "Privkey: nothex",
        ]
    )
    path = tmp_path / "vanity.txt"
    path.write_text(sample)
    lines, first_seed, last_seed = keygen.parse_vanity_file(path)
    assert lines == 4
    assert first_seed == 0x10
    assert last_seed == 0x40
