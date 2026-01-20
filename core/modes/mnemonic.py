from core.logger import get_logger

logger = get_logger(__name__)


def start(shared_metrics, args):
    """Run mnemonic generation mode."""
    from keygen.mnemonic_mode import run_mnemonic_mode

    try:
        from core.dashboard import set_metric
        set_metric("active_mode", "mnemonic")
        set_metric("global_run_state", "running")
    except Exception:
        pass
    run_mnemonic_mode(args)
    return 0
