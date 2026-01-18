from core.logger import get_logger

logger = get_logger(__name__)


def start(shared_metrics, args):
    """Run mnemonic generation mode."""
    from keygen.mnemonic_mode import run_mnemonic_mode

    run_mnemonic_mode(args)
    return 0
