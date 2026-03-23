"""Scanner package exports."""

from .workshop_hub import build_workshop_bundle, load_settings, post_workshop_bundle, save_settings

__all__ = [
    "OffcutScannerEngine",
    "build_workshop_bundle",
    "load_settings",
    "post_workshop_bundle",
    "save_settings",
]


def __getattr__(name):
    if name == "OffcutScannerEngine":
        from .engine import OffcutScannerEngine

        return OffcutScannerEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
