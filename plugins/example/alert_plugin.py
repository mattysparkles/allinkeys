"""Sample alert plugin.

Expose via entry point::

    [options.entry_points]
    allinkeys.plugins.alert =
        example = your_pkg.alert:register
"""

from allinkeys.plugins import register_alert


def alert(message: str):
    """Dummy alert that prints a message."""
    print(f"example alert: {message}")


def register():
    register_alert("example", alert)
