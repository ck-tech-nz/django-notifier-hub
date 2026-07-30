"""django-notifier-hub: notifications as rows, delivered as a side effect.

Import path is ``notifier`` even though the distribution is
``django-notifier-hub`` -- see PRD "On the distribution name".
"""

from importlib.metadata import PackageNotFoundError, version

__all__ = ["__version__", "send", "send_multi"]

try:
    __version__ = version("django-notifier-hub")
except PackageNotFoundError:  # pragma: no cover - running from a source tree
    __version__ = "0.0.0.dev0"


def __getattr__(name: str):
    # Deferred so importing notifier does not touch models before the app
    # registry is ready.
    if name in {"send", "send_multi"}:
        from notifier import api

        return getattr(api, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
