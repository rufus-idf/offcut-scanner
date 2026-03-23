"""Scanner package exports."""

from .workshop_hub import (
    DEFAULT_PUSH_URL,
    build_workshop_bundle,
    fetch_texture_library_materials,
    load_settings,
    post_workshop_bundle,
    save_settings,
)

__all__ = [
    "DEFAULT_PUSH_URL",
    "OffcutScannerEngine",
    "build_workshop_bundle",
    "fetch_texture_library_materials",
    "load_settings",
    "post_workshop_bundle",
    "save_settings",
]


def __getattr__(name):
    if name == "OffcutScannerEngine":
        from .engine import OffcutScannerEngine

        return OffcutScannerEngine
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
