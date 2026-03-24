import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pyrealsense2 as rs


MIN_HEIGHT_MM = 8
MIN_CONTOUR_AREA_PX = 5000
APPROX_EPSILON_RATIO = 0.02
HEIGHT_PERCENTILE = 95
DEPTH_SAMPLE_RADIUS_PX = 2
MIN_BED_PLANE_SCALE = 0.85
DEFAULT_BED_WIDTH_MM = 400.0
DEFAULT_BED_HEIGHT_MM = 300.0
STREAM_MODES = [
    (1280, 720, 30),
    (848, 480, 30),
    (640, 480, 30),
]


@dataclass
class FrameView:
    color_image: np.ndarray
    preview_image: np.ndarray
    mask_image: np.ndarray
    payload: dict[str, Any] | None
    scan_result: dict[str, Any] | None
    status_text: str
    has_detection: bool


class OffcutScannerEngine:
    def __init__(
        self,
        capture_dir: str | None = None,
        calibration_file: str | None = None,
        baseline_file: str | None = None,
    ):
        self.runtime_dir = self.default_runtime_dir()
        self.capture_dir = str(self.resolve_runtime_path(capture_dir or "captures"))
        self.calibration_file = str(self.resolve_runtime_path(calibration_file or "calibration.json"))
        self.baseline_file = str(self.resolve_runtime_path(baseline_file or "baseline_depth.npy"))
        self.calibration_snapshot_file = str(self.resolve_runtime_path("calibration_snapshot.png"))
        os.makedirs(self.capture_dir, exist_ok=True)
        self.stream_width = None
        self.stream_height = None
        self.stream_fps = None

        self.pipeline = None
        self.align = None
        self.depth_scale = None
        self.spatial = None
        self.temporal = None
        self.hole_filling = None
        self.color_intrinsics = None
        self.principal_point_px = None

        self.H = None
        self.H_inv = None
        self.bed_points_mm = None

        self.baseline_depth_mm = None
        self.latest_depth_mm = None
        self.latest_color_image = None
        self.latest_view = None

    @staticmethod
    def default_runtime_dir():
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent
        return Path(__file__).resolve().parent.parent

    def resolve_runtime_path(self, path_value: str):
        path = Path(path_value)
        if path.is_absolute():
            return path
        return self.runtime_dir / path

    @staticmethod
    def utc_now_str():
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def timestamp_id():
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    @staticmethod
    def default_bed_points_mm(width_mm=DEFAULT_BED_WIDTH_MM, height_mm=DEFAULT_BED_HEIGHT_MM):
        return np.array(
            [
                [0.0, 0.0],
                [float(width_mm), 0.0],
                [float(width_mm), float(height_mm)],
                [0.0, float(height_mm)],
            ],
            dtype=np.float32,
        )

    @staticmethod
    def order_points(points_px):
        pts = np.array(points_px, dtype=np.float32)
        s = pts.sum(axis=1)
        d = np.diff(pts, axis=1)

        top_left = pts[np.argmin(s)]
        bottom_right = pts[np.argmax(s)]
        top_right = pts[np.argmin(d)]
        bottom_left = pts[np.argmax(d)]

        return np.array([top_left, top_right, bottom_right, bottom_left], dtype=np.float32)

    def has_calibration(self):
        return self.H is not None and self.H_inv is not None and self.bed_points_mm is not None

    def has_baseline(self):
        return self.baseline_depth_mm is not None

    def load_calibration(self, required=True):
        calibration_path = Path(self.calibration_file)
        if not calibration_path.exists():
            self.H = None
            self.H_inv = None
            self.bed_points_mm = None
            if required:
                raise FileNotFoundError(
                    f"Calibration file not found: {calibration_path}. "
                    "Use in-app calibration or place calibration.json next to the app."
                )
            return False

        with calibration_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        self.H = np.array(data["homography_px_to_mm"], dtype=np.float32)
        self.H_inv = np.array(data["homography_mm_to_px"], dtype=np.float32)
        self.bed_points_mm = np.array(data["bed_points_mm"], dtype=np.float32)
        return True

    def save_calibration(self, image_points_px, bed_points_mm=None, snapshot_image=None):
        if len(image_points_px) != 4:
            raise ValueError("Exactly 4 calibration points are required.")

        ordered_image_points_px = self.order_points(image_points_px)
        ordered_bed_points_mm = np.array(
            bed_points_mm if bed_points_mm is not None else self.default_bed_points_mm(),
            dtype=np.float32,
        )

        H = cv2.getPerspectiveTransform(ordered_image_points_px, ordered_bed_points_mm)
        H_inv = cv2.getPerspectiveTransform(ordered_bed_points_mm, ordered_image_points_px)

        payload = {
            "image_points_px": ordered_image_points_px.tolist(),
            "bed_points_mm": ordered_bed_points_mm.tolist(),
            "homography_px_to_mm": H.tolist(),
            "homography_mm_to_px": H_inv.tolist(),
        }

        calibration_path = Path(self.calibration_file)
        with calibration_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        if snapshot_image is not None:
            cv2.imwrite(self.calibration_snapshot_file, snapshot_image)

        self.H = H
        self.H_inv = H_inv
        self.bed_points_mm = ordered_bed_points_mm
        self.clear_baseline(delete_file=True)
        return calibration_path

    def load_baseline(self):
        baseline_path = Path(self.baseline_file)
        if not baseline_path.exists():
            self.baseline_depth_mm = None
            return False

        baseline_depth_mm = np.load(baseline_path)
        if self.latest_depth_mm is not None and baseline_depth_mm.shape != self.latest_depth_mm.shape:
            self.baseline_depth_mm = None
            return False

        self.baseline_depth_mm = baseline_depth_mm.astype(np.float32)
        return True

    def save_baseline(self):
        if self.baseline_depth_mm is None:
            raise RuntimeError("No baseline is loaded to save.")
        np.save(self.baseline_file, self.baseline_depth_mm)
        return Path(self.baseline_file)

    def clear_baseline(self, delete_file=False):
        self.baseline_depth_mm = None
        if delete_file:
            baseline_path = Path(self.baseline_file)
            if baseline_path.exists():
                baseline_path.unlink()

    def clear_calibration(self, delete_file=False):
        self.H = None
        self.H_inv = None
        self.bed_points_mm = None
        self.clear_baseline(delete_file=True)
        if delete_file:
            calibration_path = Path(self.calibration_file)
            if calibration_path.exists():
                calibration_path.unlink()

    @staticmethod
    def transform_points_px_to_mm(points_px, H):
        pts = np.array(points_px, dtype=np.float32).reshape(-1, 1, 2)
        pts_mm = cv2.perspectiveTransform(pts, H)
        return pts_mm.reshape(-1, 2)

    @staticmethod
    def transform_points_mm_to_px(points_mm, H_inv):
        pts = np.array(points_mm, dtype=np.float32).reshape(-1, 1, 2)
        pts_px = cv2.perspectiveTransform(pts, H_inv)
        return pts_px.reshape(-1, 2)

    @staticmethod
    def polygon_area_mm2(points_mm):
        pts = np.array(points_mm, dtype=np.float32).reshape(-1, 1, 2)
        return float(abs(cv2.contourArea(pts)))

    @staticmethod
    def bbox_from_points_mm(points_mm):
        xs = points_mm[:, 0]
        ys = points_mm[:, 1]
        min_x = float(np.min(xs))
        min_y = float(np.min(ys))
        max_x = float(np.max(xs))
        max_y = float(np.max(ys))
        return min_x, min_y, max_x, max_y, max_x - min_x, max_y - min_y

    @staticmethod
    def polygon_edge_lengths_mm(points_mm):
        if len(points_mm) < 2:
            return []

        lengths = []
        for i in range(len(points_mm)):
            p1 = points_mm[i]
            p2 = points_mm[(i + 1) % len(points_mm)]
            lengths.append(float(np.linalg.norm(p2 - p1)))
        return lengths

    def rectangle_dimensions_mm(self, points_mm):
        if len(points_mm) != 4:
            return None

        edge_lengths = self.polygon_edge_lengths_mm(points_mm)
        pair_a = (edge_lengths[0] + edge_lengths[2]) / 2.0
        pair_b = (edge_lengths[1] + edge_lengths[3]) / 2.0

        return {
            "width_mm": float(max(pair_a, pair_b)),
            "height_mm": float(min(pair_a, pair_b)),
            "edge_lengths_mm": edge_lengths,
        }

    def measurement_summary(self, points_mm, shape_type):
        min_x, min_y, _, _, axis_bbox_w_mm, axis_bbox_h_mm = self.bbox_from_points_mm(points_mm)

        summary = {
            "bbox_x_mm": min_x,
            "bbox_y_mm": min_y,
            "bbox_w_mm": axis_bbox_w_mm,
            "bbox_h_mm": axis_bbox_h_mm,
            "axis_aligned_bbox_w_mm": axis_bbox_w_mm,
            "axis_aligned_bbox_h_mm": axis_bbox_h_mm,
            "edge_lengths_mm": self.polygon_edge_lengths_mm(points_mm),
        }

        if shape_type == "RECT" and len(points_mm) == 4:
            rect_dims = self.rectangle_dimensions_mm(points_mm)
            if rect_dims is not None:
                summary["bbox_w_mm"] = rect_dims["width_mm"]
                summary["bbox_h_mm"] = rect_dims["height_mm"]
                summary["edge_lengths_mm"] = rect_dims["edge_lengths_mm"]

        return summary

    @staticmethod
    def classify_shape(vertices):
        n = len(vertices)
        if n == 4:
            return "RECT"
        if n == 6:
            return "L"
        if n == 8:
            return "C"
        return "POLY"

    @staticmethod
    def mm_points_to_svg_path(points_mm):
        if len(points_mm) == 0:
            return ""
        first = points_mm[0]
        parts = [f"M{first[0]:.1f} {first[1]:.1f}"]
        for p in points_mm[1:]:
            parts.append(f"L{p[0]:.1f} {p[1]:.1f}")
        parts.append("Z")
        return " ".join(parts)

    @staticmethod
    def depth_frame_to_mm(depth_frame, depth_scale):
        depth_raw = np.asanyarray(depth_frame.get_data()).astype(np.float32)
        return depth_raw * depth_scale * 1000.0

    @staticmethod
    def sample_depth_mm(depth_map_mm, point_px, radius_px=DEPTH_SAMPLE_RADIUS_PX):
        x = int(round(point_px[0]))
        y = int(round(point_px[1]))

        x0 = max(0, x - radius_px)
        x1 = min(depth_map_mm.shape[1], x + radius_px + 1)
        y0 = max(0, y - radius_px)
        y1 = min(depth_map_mm.shape[0], y + radius_px + 1)

        window = depth_map_mm[y0:y1, x0:x1]
        valid = window[window > 0]
        if valid.size == 0:
            return 0.0

        return float(np.median(valid))

    def estimate_bed_depth_mm(self, baseline_depth_mm):
        bed_outline_px = self.transform_points_mm_to_px(self.bed_points_mm, self.H_inv).astype(np.int32)
        mask = np.zeros(baseline_depth_mm.shape, dtype=np.uint8)
        cv2.fillPoly(mask, [bed_outline_px], 255)

        bed_depths = baseline_depth_mm[mask > 0]
        bed_depths = bed_depths[bed_depths > 0]
        if bed_depths.size == 0:
            return 0.0

        return float(np.median(bed_depths))

    def compensate_vertices_to_bed_plane(self, vertices_px, current_depth_mm, bed_depth_mm):
        corrected_vertices_px = []
        vertex_depths_mm = []
        cx, cy = float(self.principal_point_px[0]), float(self.principal_point_px[1])

        for vertex_px in vertices_px:
            vertex_depth_mm = self.sample_depth_mm(current_depth_mm, vertex_px)
            vertex_depths_mm.append(vertex_depth_mm)

            if vertex_depth_mm <= 0 or bed_depth_mm <= 0:
                corrected_vertices_px.append([float(vertex_px[0]), float(vertex_px[1])])
                continue

            scale = float(np.clip(vertex_depth_mm / bed_depth_mm, MIN_BED_PLANE_SCALE, 1.0))
            corrected_x = cx + (float(vertex_px[0]) - cx) * scale
            corrected_y = cy + (float(vertex_px[1]) - cy) * scale
            corrected_vertices_px.append([corrected_x, corrected_y])

        return corrected_vertices_px, vertex_depths_mm

    def start_camera(self):
        self.load_calibration(required=False)

        profile = None
        last_error = None
        for width, height, fps in STREAM_MODES:
            trial_pipeline = rs.pipeline()
            config = rs.config()
            config.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)
            config.enable_stream(rs.stream.color, width, height, rs.format.bgr8, fps)
            try:
                profile = trial_pipeline.start(config)
                self.pipeline = trial_pipeline
                self.stream_width = width
                self.stream_height = height
                self.stream_fps = fps
                break
            except Exception as exc:
                last_error = exc
                try:
                    trial_pipeline.stop()
                except Exception:
                    pass

        if profile is None or self.pipeline is None:
            raise RuntimeError(f"Failed to start RealSense stream in supported modes: {last_error}")

        depth_sensor = profile.get_device().first_depth_sensor()
        self.depth_scale = depth_sensor.get_depth_scale()
        color_stream = profile.get_stream(rs.stream.color).as_video_stream_profile()
        self.color_intrinsics = color_stream.get_intrinsics()
        self.principal_point_px = np.array([self.color_intrinsics.ppx, self.color_intrinsics.ppy], dtype=np.float32)

        self.align = rs.align(rs.stream.color)
        self.spatial = rs.spatial_filter()
        self.temporal = rs.temporal_filter()
        self.hole_filling = rs.hole_filling_filter()
        self.load_baseline()

    def stop_camera(self):
        if self.pipeline is not None:
            self.pipeline.stop()
        self.pipeline = None
        self.align = None
        self.depth_scale = None
        self.spatial = None
        self.temporal = None
        self.hole_filling = None
        self.color_intrinsics = None
        self.principal_point_px = None
        self.latest_depth_mm = None
        self.latest_color_image = None
        self.latest_view = None
        self.stream_width = None
        self.stream_height = None
        self.stream_fps = None

    def capture_baseline(self):
        if self.latest_depth_mm is None:
            raise RuntimeError("No frame available yet.")
        self.baseline_depth_mm = self.latest_depth_mm.copy()
        self.save_baseline()

    def process_frames(self, frames):
        aligned = self.align.process(frames)
        depth_frame = aligned.get_depth_frame()
        color_frame = aligned.get_color_frame()

        if not depth_frame or not color_frame:
            return None, None

        depth_frame = self.spatial.process(depth_frame)
        depth_frame = self.temporal.process(depth_frame)
        depth_frame = self.hole_filling.process(depth_frame)

        color_image = np.asanyarray(color_frame.get_data())
        return depth_frame, color_image

    @staticmethod
    def build_mask(current_depth_mm, baseline_depth_mm):
        valid = (current_depth_mm > 0) & (baseline_depth_mm > 0)
        diff_mm = baseline_depth_mm - current_depth_mm

        mask = np.zeros_like(diff_mm, dtype=np.uint8)
        mask[(diff_mm > MIN_HEIGHT_MM) & valid] = 255

        kernel = np.ones((5, 5), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
        mask = cv2.erode(mask, np.ones((3, 3), np.uint8), iterations=1)

        return mask, diff_mm

    @staticmethod
    def find_main_contour(mask):
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if not contours:
            return None

        contour = max(contours, key=cv2.contourArea)
        if cv2.contourArea(contour) < MIN_CONTOUR_AREA_PX:
            return None

        return contour

    @staticmethod
    def contour_vertices(contour):
        epsilon = APPROX_EPSILON_RATIO * cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, epsilon, True)
        return [[float(pt[0][0]), float(pt[0][1])] for pt in approx]

    @staticmethod
    def percentile_height_mm(diff_mm, mask, percentile=HEIGHT_PERCENTILE):
        if not np.any(mask > 0):
            return 0.0

        valid_heights = diff_mm[mask > 0]
        valid_heights = valid_heights[valid_heights > 0]
        if valid_heights.size == 0:
            return 0.0

        return float(np.percentile(valid_heights, percentile))

    def draw_calibration_overlay(self, display):
        if not self.has_calibration():
            return
        bed_outline_px = self.transform_points_mm_to_px(self.bed_points_mm, self.H_inv).astype(int)
        cv2.polylines(display, [bed_outline_px], True, (0, 255, 255), 2)

        for i, (px, py) in enumerate(bed_outline_px):
            cv2.circle(display, (int(px), int(py)), 5, (0, 255, 255), -1)
            cv2.putText(display, f"C{i + 1}", (int(px) + 8, int(py) - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 255, 255), 2, cv2.LINE_AA)

        anchor_x, anchor_y = bed_outline_px[0]
        cv2.putText(display, "Calibration area", (int(anchor_x) + 10, int(anchor_y) + 24),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 255, 255), 2, cv2.LINE_AA)

    def draw_mm_overlay(self, display, points_mm, shape_type):
        if not self.has_calibration():
            return
        if len(points_mm) == 0:
            return

        summary = self.measurement_summary(points_mm, shape_type)
        label = f"{summary['bbox_w_mm']:.0f}mm x {summary['bbox_h_mm']:.0f}mm"

        bbox_mm = np.array([
            [summary["bbox_x_mm"], summary["bbox_y_mm"]],
            [summary["bbox_x_mm"] + summary["axis_aligned_bbox_w_mm"], summary["bbox_y_mm"]],
            [summary["bbox_x_mm"] + summary["axis_aligned_bbox_w_mm"], summary["bbox_y_mm"] + summary["axis_aligned_bbox_h_mm"]],
            [summary["bbox_x_mm"], summary["bbox_y_mm"] + summary["axis_aligned_bbox_h_mm"]],
        ], dtype=np.float32)

        bbox_px = self.transform_points_mm_to_px(bbox_mm, self.H_inv).astype(int)
        cv2.polylines(display, [bbox_px], True, (255, 255, 0), 2)
        x, y = bbox_px[0]
        cv2.putText(display, label, (int(x) + 10, int(y) + 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2, cv2.LINE_AA)

    def build_payload(self, points_mm, shape_type, diff_mm, mask, vertices_px, corrected_vertices_px,
                      bed_depth_mm, vertex_depths_mm):
        area_mm2 = self.polygon_area_mm2(points_mm)
        summary = self.measurement_summary(points_mm, shape_type)
        representative_height_mm = self.percentile_height_mm(diff_mm, mask)

        return {
            "captured_at_utc": self.utc_now_str(),
            "shape_type": shape_type,
            "area_mm2": round(area_mm2, 1),
            "bbox_x_mm": round(summary["bbox_x_mm"], 1),
            "bbox_y_mm": round(summary["bbox_y_mm"], 1),
            "bbox_w_mm": round(summary["bbox_w_mm"], 1),
            "bbox_h_mm": round(summary["bbox_h_mm"], 1),
            "axis_aligned_bbox_w_mm": round(summary["axis_aligned_bbox_w_mm"], 1),
            "axis_aligned_bbox_h_mm": round(summary["axis_aligned_bbox_h_mm"], 1),
            "edge_lengths_mm": [round(length, 1) for length in summary["edge_lengths_mm"]],
            "vertices_px": [[round(float(x), 1), round(float(y), 1)] for x, y in vertices_px],
            "vertices_px_bed_plane": [[round(float(x), 1), round(float(y), 1)] for x, y in corrected_vertices_px],
            "vertex_depths_mm": [round(float(depth), 1) for depth in vertex_depths_mm],
            "bed_depth_mm": round(float(bed_depth_mm), 1),
            "vertices_mm": [[round(float(x), 1), round(float(y), 1)] for x, y in points_mm],
            "svg_path_data": self.mm_points_to_svg_path(points_mm),
            "height_percentile": HEIGHT_PERCENTILE,
            "height_mm_above_bed_p95": round(representative_height_mm, 2),
        }

    @staticmethod
    def mm_points_to_dxf(points_mm):
        lines = [
            "0", "SECTION",
            "2", "ENTITIES",
            "0", "LWPOLYLINE",
            "8", "OFFCUT",
            "90", str(len(points_mm)),
            "70", "1",
        ]
        for x, y in points_mm:
            lines.extend(["10", f"{float(x):.3f}", "20", f"{float(y):.3f}"])
        lines.extend(["0", "ENDSEC", "0", "EOF"])
        return "\n".join(lines) + "\n"

    def process_next_frame(self):
        if self.pipeline is None:
            raise RuntimeError("Camera is not started.")

        frames = self.pipeline.wait_for_frames()
        depth_frame, color_image = self.process_frames(frames)
        if depth_frame is None:
            return None

        current_depth_mm = self.depth_frame_to_mm(depth_frame, self.depth_scale)
        self.latest_depth_mm = current_depth_mm
        self.latest_color_image = color_image.copy()

        display = color_image.copy()
        mask_display = np.zeros(current_depth_mm.shape, dtype=np.uint8)

        payload = None
        scan_result = None
        if not self.has_calibration():
            status_text = "Calibration required. Use the in-app calibration controls."
        elif self.baseline_depth_mm is None:
            status_text = "Press 'Capture Empty Bed' to record the empty surface."
        else:
            status_text = "No offcut detected."
        has_detection = False

        if self.has_calibration():
            self.draw_calibration_overlay(display)

        if self.has_calibration() and self.baseline_depth_mm is not None:
            bed_depth_mm = self.estimate_bed_depth_mm(self.baseline_depth_mm)
            mask, diff_mm = self.build_mask(current_depth_mm, self.baseline_depth_mm)
            mask_display = mask
            contour = self.find_main_contour(mask)

            if contour is not None:
                vertices_px = self.contour_vertices(contour)
                corrected_vertices_px, vertex_depths_mm = self.compensate_vertices_to_bed_plane(
                    vertices_px,
                    current_depth_mm,
                    bed_depth_mm,
                )
                points_mm = self.transform_points_px_to_mm(corrected_vertices_px, self.H)
                shape_type = self.classify_shape(points_mm)
                payload = self.build_payload(
                    points_mm,
                    shape_type,
                    diff_mm,
                    mask_display,
                    vertices_px,
                    corrected_vertices_px,
                    bed_depth_mm,
                    vertex_depths_mm,
                )
                summary = self.measurement_summary(np.array(points_mm, dtype=np.float32), shape_type)

                cv2.drawContours(display, [contour], -1, (0, 255, 0), 2)
                for i, (vx, vy) in enumerate(vertices_px):
                    cv2.circle(display, (int(vx), int(vy)), 4, (0, 0, 255), -1)
                    cv2.putText(display, str(i + 1), (int(vx) + 5, int(vy) - 5),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1, cv2.LINE_AA)

                corrected_poly = np.array(corrected_vertices_px, dtype=np.int32)
                cv2.polylines(display, [corrected_poly], True, (255, 0, 255), 2)
                self.draw_mm_overlay(display, np.array(points_mm, dtype=np.float32), shape_type)

                status_text = (
                    f"{shape_type} | area={payload['area_mm2']:.0f} mm2 | "
                    f"size={summary['bbox_w_mm']:.0f} x {summary['bbox_h_mm']:.0f} mm | "
                    f"p95 height={payload['height_mm_above_bed_p95']:.1f} mm"
                )
                has_detection = True
                scan_result = {
                    "color_image": color_image.copy(),
                    "preview_image": display.copy(),
                    "mask_image": mask_display.copy(),
                    "contour": contour,
                    "vertices_px": vertices_px,
                    "corrected_vertices_px": corrected_vertices_px,
                    "points_mm": [[float(x), float(y)] for x, y in points_mm],
                    "shape_type": shape_type,
                    "diff_mm": diff_mm.copy(),
                    "bed_depth_mm": bed_depth_mm,
                    "vertex_depths_mm": vertex_depths_mm,
                    "payload": payload,
                }
            else:
                status_text = "No offcut detected."

        cv2.putText(display, status_text, (20, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0) if has_detection else (0, 200, 255),
                    2, cv2.LINE_AA)

        self.latest_view = FrameView(
            color_image=color_image.copy(),
            preview_image=display,
            mask_image=mask_display,
            payload=payload,
            scan_result=scan_result,
            status_text=status_text,
            has_detection=has_detection,
        )
        return self.latest_view

    def save_scan_result(self, scan_result, workshop_bundle=None):
        if scan_result is None:
            raise RuntimeError("No scan result available to save.")

        ts = self.timestamp_id()
        image_path = os.path.join(self.capture_dir, f"{ts}_preview.png")
        mask_path = os.path.join(self.capture_dir, f"{ts}_mask.png")
        json_path = os.path.join(self.capture_dir, f"{ts}_scan_mm.json")
        dxf_path = os.path.join(self.capture_dir, f"{ts}_scan_mm.dxf")
        workshop_json_path = os.path.join(self.capture_dir, f"{ts}_workshop_hub.json")

        cv2.imwrite(image_path, scan_result["preview_image"])
        cv2.imwrite(mask_path, scan_result["mask_image"])
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(scan_result["payload"], f, indent=2)
        with open(dxf_path, "w", encoding="utf-8") as f:
            f.write(self.mm_points_to_dxf(scan_result["payload"]["vertices_mm"]))
        if workshop_bundle is not None:
            with open(workshop_json_path, "w", encoding="utf-8") as f:
                json.dump(workshop_bundle, f, indent=2)

        return {
            "image_path": image_path,
            "mask_path": mask_path,
            "json_path": json_path,
            "dxf_path": dxf_path,
            "workshop_json_path": workshop_json_path if workshop_bundle is not None else None,
            "payload": scan_result["payload"],
        }
