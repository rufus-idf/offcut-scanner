import os
import json
from datetime import datetime, timezone

import cv2
import numpy as np
import pyrealsense2 as rs


CAPTURE_DIR = "captures"
CALIBRATION_FILE = "calibration.json"

os.makedirs(CAPTURE_DIR, exist_ok=True)

MIN_HEIGHT_MM = 8
MIN_CONTOUR_AREA_PX = 5000
APPROX_EPSILON_RATIO = 0.01

baseline_depth_mm = None


def utc_now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def timestamp_id():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def load_calibration():
    with open(CALIBRATION_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    H = np.array(data["homography_px_to_mm"], dtype=np.float32)
    H_inv = np.array(data["homography_mm_to_px"], dtype=np.float32)
    bed_points_mm = np.array(data["bed_points_mm"], dtype=np.float32)
    return H, H_inv, bed_points_mm


def transform_points_px_to_mm(points_px, H):
    pts = np.array(points_px, dtype=np.float32).reshape(-1, 1, 2)
    pts_mm = cv2.perspectiveTransform(pts, H)
    return pts_mm.reshape(-1, 2)


def polygon_area_mm2(points_mm):
    pts = np.array(points_mm, dtype=np.float32).reshape(-1, 1, 2)
    return float(abs(cv2.contourArea(pts)))


def bbox_from_points_mm(points_mm):
    xs = points_mm[:, 0]
    ys = points_mm[:, 1]
    min_x = float(np.min(xs))
    min_y = float(np.min(ys))
    max_x = float(np.max(xs))
    max_y = float(np.max(ys))
    return min_x, min_y, max_x, max_y, max_x - min_x, max_y - min_y


def classify_shape(vertices):
    n = len(vertices)
    if n == 4:
        return "RECT"
    if n == 6:
        return "L"
    if n == 8:
        return "C"
    return "POLY"


def mm_points_to_svg_path(points_mm):
    if len(points_mm) == 0:
        return ""
    first = points_mm[0]
    parts = [f"M{first[0]:.1f} {first[1]:.1f}"]
    for p in points_mm[1:]:
        parts.append(f"L{p[0]:.1f} {p[1]:.1f}")
    parts.append("Z")
    return " ".join(parts)


def depth_frame_to_mm(depth_frame, depth_scale):
    depth_raw = np.asanyarray(depth_frame.get_data()).astype(np.float32)
    return depth_raw * depth_scale * 1000.0


def create_pipeline():
    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
    config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)

    profile = pipeline.start(config)

    depth_sensor = profile.get_device().first_depth_sensor()
    depth_scale = depth_sensor.get_depth_scale()

    align = rs.align(rs.stream.color)
    spatial = rs.spatial_filter()
    temporal = rs.temporal_filter()
    hole_filling = rs.hole_filling_filter()

    return pipeline, align, depth_scale, spatial, temporal, hole_filling


def process_frames(frames, align, spatial, temporal, hole_filling):
    aligned = align.process(frames)
    depth_frame = aligned.get_depth_frame()
    color_frame = aligned.get_color_frame()

    if not depth_frame or not color_frame:
        return None, None

    depth_frame = spatial.process(depth_frame)
    depth_frame = temporal.process(depth_frame)
    depth_frame = hole_filling.process(depth_frame)

    color_image = np.asanyarray(color_frame.get_data())
    return depth_frame, color_image


def build_mask(current_depth_mm, baseline_depth_mm):
    valid = (current_depth_mm > 0) & (baseline_depth_mm > 0)
    diff_mm = baseline_depth_mm - current_depth_mm

    mask = np.zeros_like(diff_mm, dtype=np.uint8)
    mask[(diff_mm > MIN_HEIGHT_MM) & valid] = 255

    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    mask = cv2.GaussianBlur(mask, (5, 5), 0)
    _, mask = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)

    return mask, diff_mm


def find_main_contour(mask):
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    contour = max(contours, key=cv2.contourArea)
    if cv2.contourArea(contour) < MIN_CONTOUR_AREA_PX:
        return None

    return contour


def contour_vertices(contour):
    epsilon = APPROX_EPSILON_RATIO * cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, epsilon, True)
    return [[float(pt[0][0]), float(pt[0][1])] for pt in approx]


def draw_mm_overlay(display, points_mm, H_inv):
    if len(points_mm) == 0:
        return

    min_x, min_y, max_x, max_y, w_mm, h_mm = bbox_from_points_mm(points_mm)
    label = f"{w_mm:.0f}mm x {h_mm:.0f}mm"

    bbox_mm = np.array([
        [min_x, min_y],
        [max_x, min_y],
        [max_x, max_y],
        [min_x, max_y]
    ], dtype=np.float32).reshape(-1, 1, 2)

    bbox_px = cv2.perspectiveTransform(bbox_mm, H_inv).reshape(-1, 2).astype(int)

    cv2.polylines(display, [bbox_px], True, (255, 255, 0), 2)
    x, y = bbox_px[0]
    cv2.putText(display, label, (x + 10, y + 25),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2, cv2.LINE_AA)


def save_scan(color_image, mask, contour, vertices_px, vertices_mm, shape_type, diff_mm):
    ts = timestamp_id()

    points_mm = np.array(vertices_mm, dtype=np.float32)
    area_mm2 = polygon_area_mm2(points_mm)
    min_x, min_y, max_x, max_y, bbox_w_mm, bbox_h_mm = bbox_from_points_mm(points_mm)

    preview = color_image.copy()
    cv2.drawContours(preview, [contour], -1, (0, 255, 0), 2)

    for i, (vx, vy) in enumerate(vertices_px):
        cv2.circle(preview, (int(vx), int(vy)), 5, (0, 0, 255), -1)
        cv2.putText(preview, str(i + 1), (int(vx) + 6, int(vy) - 6),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1, cv2.LINE_AA)

    max_height_mm = float(np.max(diff_mm[mask > 0])) if np.any(mask > 0) else 0.0

    payload = {
        "captured_at_utc": utc_now_str(),
        "shape_type": shape_type,
        "area_mm2": round(area_mm2, 1),
        "bbox_x_mm": round(min_x, 1),
        "bbox_y_mm": round(min_y, 1),
        "bbox_w_mm": round(bbox_w_mm, 1),
        "bbox_h_mm": round(bbox_h_mm, 1),
        "vertices_mm": [[round(float(x), 1), round(float(y), 1)] for x, y in vertices_mm],
        "svg_path_data": mm_points_to_svg_path(vertices_mm),
        "max_height_mm_above_bed": round(max_height_mm, 2),
    }

    image_path = os.path.join(CAPTURE_DIR, f"{ts}_preview.png")
    mask_path = os.path.join(CAPTURE_DIR, f"{ts}_mask.png")
    json_path = os.path.join(CAPTURE_DIR, f"{ts}_scan_mm.json")

    cv2.imwrite(image_path, preview)
    cv2.imwrite(mask_path, mask)

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    print("\nSaved scan:")
    print(f"  Image: {image_path}")
    print(f"  Mask : {mask_path}")
    print(f"  JSON : {json_path}")
    print(json.dumps(payload, indent=2))


def main():
    global baseline_depth_mm

    H, H_inv, bed_points_mm = load_calibration()
    pipeline, align, depth_scale, spatial, temporal, hole_filling = create_pipeline()

    print("Camera started.")
    print("Controls:")
    print("  b = capture empty-bed baseline")
    print("  s = save current detected scan")
    print("  q = quit")

    try:
        while True:
            frames = pipeline.wait_for_frames()
            depth_frame, color_image = process_frames(frames, align, spatial, temporal, hole_filling)
            if depth_frame is None:
                continue

            current_depth_mm = depth_frame_to_mm(depth_frame, depth_scale)
            display = color_image.copy()
            mask_display = np.zeros((480, 640), dtype=np.uint8)

            contour = None
            vertices_px = []
            vertices_mm = []
            shape_type = None
            diff_mm = None

            if baseline_depth_mm is not None:
                mask, diff_mm = build_mask(current_depth_mm, baseline_depth_mm)
                mask_display = mask
                contour = find_main_contour(mask)

                if contour is not None:
                    vertices_px = contour_vertices(contour)
                    points_mm = transform_points_px_to_mm(vertices_px, H)
                    vertices_mm = [[float(x), float(y)] for x, y in points_mm]
                    shape_type = classify_shape(vertices_mm)

                    cv2.drawContours(display, [contour], -1, (0, 255, 0), 2)

                    for i, (vx, vy) in enumerate(vertices_px):
                        cv2.circle(display, (int(vx), int(vy)), 4, (0, 0, 255), -1)
                        cv2.putText(display, str(i + 1), (int(vx) + 5, int(vy) - 5),
                                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 0, 255), 1, cv2.LINE_AA)

                    draw_mm_overlay(display, np.array(vertices_mm, dtype=np.float32), H_inv)

                    area_mm2 = polygon_area_mm2(np.array(vertices_mm, dtype=np.float32))
                    min_x, min_y, max_x, max_y, bbox_w_mm, bbox_h_mm = bbox_from_points_mm(np.array(vertices_mm, dtype=np.float32))

                    label = f"{shape_type} | area={area_mm2:.0f} mm2 | bbox={bbox_w_mm:.0f} x {bbox_h_mm:.0f} mm"
                    cv2.putText(display, label, (20, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2, cv2.LINE_AA)
                else:
                    cv2.putText(display, "No offcut detected", (20, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2, cv2.LINE_AA)
            else:
                cv2.putText(display, "Press 'b' to capture empty-bed baseline", (20, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2, cv2.LINE_AA)

            cv2.imshow("Offcut Scanner v2 - Preview", display)
            cv2.imshow("Offcut Scanner v2 - Mask", mask_display)

            key = cv2.waitKey(1) & 0xFF

            if key == ord("b"):
                baseline_depth_mm = current_depth_mm.copy()
                print("Baseline captured.")
            elif key == ord("s"):
                if contour is not None and diff_mm is not None:
                    save_scan(color_image, mask_display, contour, vertices_px, vertices_mm, shape_type, diff_mm)
                else:
                    print("No valid contour to save.")
            elif key == ord("q"):
                break

    finally:
        pipeline.stop()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()