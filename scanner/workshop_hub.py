import json
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INVENTORY_HEADERS = [
    "offcut_id",
    "status",
    "material",
    "thickness_mm",
    "shape_type",
    "area_mm2",
    "bbox_w_mm",
    "bbox_h_mm",
    "qty",
    "grade",
    "sheet_origin_job",
    "sheet_origin_index",
    "captured_at_utc",
    "min_internal_width_mm",
    "usable_score",
    "location",
    "preview_ref",
    "shape_ref",
    "notes",
]

SHAPES_HEADERS = [
    "shape_ref",
    "offcut_id",
    "coord_unit",
    "bbox_x_mm",
    "bbox_y_mm",
    "vertices_json",
    "holes_json",
    "version",
]

DEFAULT_SETTINGS = {
    "material": "",
    "thickness_mm": 0.0,
    "grade": "",
    "location": "",
    "notes": "",
    "sheet_origin_job": "",
    "sheet_origin_index": "",
    "min_internal_width_mm": "",
    "usable_score": "",
    "qty": 1,
    "push_url": "",
    "push_on_save": False,
}

SETTINGS_FILE = "workshop_hub_settings.json"


def runtime_root() -> Path:
    return Path(__file__).resolve().parent.parent


def settings_path() -> Path:
    return runtime_root() / SETTINGS_FILE


def load_settings() -> dict[str, Any]:
    path = settings_path()
    if not path.exists():
        return DEFAULT_SETTINGS.copy()

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    merged = DEFAULT_SETTINGS.copy()
    merged.update(data)
    return merged


def save_settings(settings: dict[str, Any]) -> Path:
    path = settings_path()
    merged = DEFAULT_SETTINGS.copy()
    merged.update(settings)

    with path.open("w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)

    return path


def compact_timestamp(captured_at_utc: str) -> str:
    dt = datetime.strptime(captured_at_utc, "%Y-%m-%dT%H:%M:%SZ")
    return dt.strftime("%Y%m%d%H%M%S")


def map_shape_type(shape_type: str) -> str:
    return "POLYGON" if shape_type == "POLY" else shape_type


def optional_number(value: Any) -> float | int | str:
    if value in ("", None):
        return ""
    return value


def build_ids(captured_at_utc: str) -> dict[str, str]:
    stamp = compact_timestamp(captured_at_utc)
    suffix = uuid.uuid4().hex[:4].upper()
    offcut_id = f"OC-{stamp}-{suffix}"
    return {
        "offcut_id": offcut_id,
        "shape_ref": f"SHAPE-{stamp}-{suffix}",
        "preview_ref": f"PREV-{stamp}-{suffix}",
    }


def build_inventory_row(scan_payload: dict[str, Any], metadata: dict[str, Any], ids: dict[str, str]) -> dict[str, Any]:
    return {
        "offcut_id": ids["offcut_id"],
        "status": "IN_STOCK",
        "material": metadata["material"],
        "thickness_mm": round(float(metadata["thickness_mm"]), 1),
        "shape_type": map_shape_type(scan_payload["shape_type"]),
        "area_mm2": scan_payload["area_mm2"],
        "bbox_w_mm": scan_payload["bbox_w_mm"],
        "bbox_h_mm": scan_payload["bbox_h_mm"],
        "qty": int(metadata["qty"]),
        "grade": metadata["grade"],
        "sheet_origin_job": metadata["sheet_origin_job"],
        "sheet_origin_index": metadata["sheet_origin_index"],
        "captured_at_utc": scan_payload["captured_at_utc"],
        "min_internal_width_mm": optional_number(metadata["min_internal_width_mm"]),
        "usable_score": optional_number(metadata["usable_score"]),
        "location": metadata["location"],
        "preview_ref": ids["preview_ref"],
        "shape_ref": ids["shape_ref"],
        "notes": metadata["notes"],
    }


def build_shape_row(scan_payload: dict[str, Any], ids: dict[str, str]) -> dict[str, Any]:
    return {
        "shape_ref": ids["shape_ref"],
        "offcut_id": ids["offcut_id"],
        "coord_unit": "mm",
        "bbox_x_mm": scan_payload["bbox_x_mm"],
        "bbox_y_mm": scan_payload["bbox_y_mm"],
        "vertices_json": json.dumps(scan_payload["vertices_mm"]),
        "holes_json": "[]",
        "version": 1,
    }


def build_workshop_bundle(scan_payload: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    ids = build_ids(scan_payload["captured_at_utc"])
    inventory_row = build_inventory_row(scan_payload, metadata, ids)
    shape_row = build_shape_row(scan_payload, ids)

    return {
        "created_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "offcut_scanner_app",
        "sheet_tabs": {
            "offcut_inventory": [inventory_row],
            "offcut_shapes": [shape_row],
        },
        "raw_scan_payload": scan_payload,
    }


def post_workshop_bundle(push_url: str, bundle: dict[str, Any], timeout_seconds: int = 20) -> dict[str, Any]:
    payload = json.dumps(bundle).encode("utf-8")
    request = urllib.request.Request(
        push_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8", errors="replace")
            content_type = response.headers.get("Content-Type", "")
            parsed_body: Any = body
            if "json" in content_type.lower():
                try:
                    parsed_body = json.loads(body)
                except json.JSONDecodeError:
                    parsed_body = body

            return {
                "status_code": response.status,
                "body": parsed_body,
            }
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Sheet push failed with HTTP {exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Sheet push failed: {exc.reason}") from exc
