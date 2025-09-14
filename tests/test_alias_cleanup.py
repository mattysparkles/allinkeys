import warnings
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from config import settings  # noqa: E402


def test_vanity_txt_dir_alias():
    """Accessing deprecated VANITY_TXT_DIR emits warning and matches new path."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        path = settings.VANITY_TXT_DIR
        assert path == settings.VANITY_OUTPUT_DIR
        assert any(isinstance(item.message, DeprecationWarning) for item in w)
