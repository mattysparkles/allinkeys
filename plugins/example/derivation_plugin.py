"""Sample derivation plugin.

To use this plugin in your own package, expose it via an entry point in your
``setup.cfg`` or ``pyproject.toml`` like so::

    [options.entry_points]
    allinkeys.plugins.derivation =
        example = your_pkg.example:register

The function registered here simply echoes the provided seed.
"""

from allinkeys.plugins import register_derivation


def derive(seed: str):
    """Dummy derivation that returns the seed unchanged."""
    return {"echo": seed}


def register():
    register_derivation("example", derive)
