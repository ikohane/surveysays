"""
Route registration modules.

We intentionally register routes directly on the Flask app (vs Blueprints) to keep
existing endpoint names stable (e.g. url_for("home")) without touching templates.
"""


