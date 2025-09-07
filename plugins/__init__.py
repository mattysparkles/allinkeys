"""Plugin registry for All In Keys.

Plugins can register derivation or alert handlers by calling the helper
functions defined here. Loaded plugins are stored in ``derivation_plugins`` and
``alert_plugins`` dictionaries mapping a plugin name to a callable.
"""

derivation_plugins = {}
alert_plugins = {}


def register_derivation(name, func):
    """Register a derivation plugin."""
    derivation_plugins[name] = func


def register_alert(name, func):
    """Register an alert plugin."""
    alert_plugins[name] = func
